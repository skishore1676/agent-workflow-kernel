import json
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "a2a"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "x_digest"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterRegistration,
    AdapterRegistry,
    HumanApprovalReceipt,
    KernelRuntimeConfig,
    PromptRegistry,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowStatus,
    load_workflow_file,
)
from agent_workflow_kernel_a2a import A2AReviewRuntimeAdapter  # noqa: E402
from agent_workflow_kernel_x_digest import (  # noqa: E402
    XBookmarkIntakeLaneAdapter,
    XDigestDraftRuntimeAdapter,
    XDryRunPublicPublishHostAdapter,
    XPostPacketValidatorLaneAdapter,
)


WORKFLOW_PATH = ROOT / "workflows" / "x_digest_post_review.yaml"
BOOKMARKS_PATH = ROOT / "fixtures" / "x_digest" / "bookmarks_fixture.json"
CREATED_AT = "2026-06-08T12:00:00+00:00"


class XDigestTracerBulletTest(unittest.TestCase):
    def test_x_digest_tracer_runs_to_dry_run_publish_without_live_effects(self) -> None:
        with _kernel() as kernel:
            kernel.start(
                instance_id="x-digest-tracer",
                inputs={
                    "bookmark_window": json.loads(BOOKMARKS_PATH.read_text(encoding="utf-8")),
                    "project_context": {
                        "summary": "AWK/OpenClaw migration and applied AI operations writing",
                    },
                    "style_memory_ref": "fixture://suman-x-style",
                },
                now=_now(),
            )

            _run_until_human_gate(kernel, "option_selection_gate")
            option_packet = _latest_receipt_output(kernel, "propose_post_options", "option_packet")
            self.assertEqual(option_packet["option_count"], 4)

            _ingest_decision(
                kernel,
                stage_id="option_selection_gate",
                decision="draft_selected",
                constraints={"selected_option_ids": ["option-1", "option-3"]},
            )
            _run_until_human_gate(kernel, "final_publish_gate")
            publish_packet = _latest_receipt_output(
                kernel,
                "validate_publish_packet",
                "validated_publish_packet",
            )
            self.assertEqual(len(publish_packet["posts"]), 2)

            _ingest_decision(
                kernel,
                stage_id="final_publish_gate",
                decision="approve_publish",
                constraints={"approved_post_ids": ["option-1", "option-3"]},
            )
            _run_until_terminal(kernel)

            instance = kernel.ledger.get_workflow_instance("x-digest-tracer")
            self.assertIsNotNone(instance)
            assert instance is not None
            self.assertEqual(instance.status, WorkflowStatus.DONE)

            publish_receipt = _latest_receipt_output(
                kernel,
                "publish_approved_posts",
                "x_publish_receipt",
            )
            self.assertTrue(publish_receipt["dry_run"])
            self.assertFalse(publish_receipt["live_mutation_performed"])
            self.assertIn("approval_receipt_id", publish_receipt)

            artifacts = _artifact_roles(kernel)
            self.assertIn("bookmark_digest", artifacts)
            self.assertIn("option_packet", artifacts)
            self.assertIn("draft_post_packet", artifacts)
            self.assertIn("post_review_verdict", artifacts)
            self.assertIn("validated_publish_packet", artifacts)
            self.assertIn("x_publish_receipt", artifacts)

            prompt_refs = _latest_prompt_refs(kernel, "propose_post_options")
            self.assertIn("stage.x_digest.propose_post_options", prompt_refs)
            self.assertIn("lane.x_digest_post_review", prompt_refs)
            self.assertIn("policy.openclaw.editorial_public_boundary", prompt_refs)


class _kernel:
    def __init__(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.ledger = WorkflowLedger(Path(self.temp_dir.name) / "workflow.sqlite")
        workflow = load_workflow_file(WORKFLOW_PATH)
        registry = AdapterRegistry(
            (
                AdapterRegistration.from_lane_adapter(XBookmarkIntakeLaneAdapter(created_at=CREATED_AT)),
                AdapterRegistration.from_runtime_adapter(XDigestDraftRuntimeAdapter(created_at=CREATED_AT)),
                AdapterRegistration.from_runtime_adapter(
                    A2AReviewRuntimeAdapter(
                        scripted_turn_batches=(
                            (
                                {
                                    "actor": "reviewer",
                                    "message": "Accepted for final approval.",
                                    "outcome": "accepted",
                                },
                            ),
                        ),
                        created_at=CREATED_AT,
                    )
                ),
                AdapterRegistration.from_lane_adapter(XPostPacketValidatorLaneAdapter(created_at=CREATED_AT)),
                AdapterRegistration.from_host_adapter(XDryRunPublicPublishHostAdapter(created_at=CREATED_AT)),
            )
        )
        self.kernel = WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(
                owner_id="x-digest-tracer-test",
                adapter_registry=registry,
                prompt_registry=PromptRegistry.load(ROOT / "prompts"),
            ),
        )

    def __enter__(self) -> WorkflowKernel:
        return self.kernel

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.ledger.close()
        self.temp_dir.cleanup()


def _run_until_stage(kernel: WorkflowKernel, stage_id: str, *, max_steps: int = 20) -> None:
    for _ in range(max_steps):
        instance = kernel.ledger.get_workflow_instance("x-digest-tracer")
        if instance is not None and instance.current_stage_id == stage_id:
            return
        step = kernel.run_once(now=_now())
        if step.decision == "idle":
            break
    instance = kernel.ledger.get_workflow_instance("x-digest-tracer")
    current = instance.current_stage_id if instance is not None else None
    raise AssertionError(f"workflow did not reach {stage_id}; current stage is {current}")


def _run_until_human_gate(kernel: WorkflowKernel, stage_id: str, *, max_steps: int = 20) -> None:
    _run_until_stage(kernel, stage_id, max_steps=max_steps)
    for _ in range(max_steps):
        if _latest_human_gate_event(kernel, stage_id) is not None:
            return
        step = kernel.run_once(now=_now())
        if step.decision == "idle":
            break
    raise AssertionError(f"workflow reached {stage_id} but did not emit a human gate event")


def _run_until_terminal(kernel: WorkflowKernel, *, max_steps: int = 20) -> None:
    for _ in range(max_steps):
        instance = kernel.ledger.get_workflow_instance("x-digest-tracer")
        if instance is not None and instance.current_stage_id is None:
            return
        step = kernel.run_once(now=_now())
        if step.decision == "idle":
            break
    instance = kernel.ledger.get_workflow_instance("x-digest-tracer")
    raise AssertionError(f"workflow did not reach terminal state; instance is {instance}")


def _ingest_decision(
    kernel: WorkflowKernel,
    *,
    stage_id: str,
    decision: str,
    constraints: dict[str, Any],
) -> None:
    gate_event = _latest_human_gate_event(kernel, stage_id)
    if gate_event is None:
        raise AssertionError(f"no human gate event for stage {stage_id}")
    payload = gate_event["payload"]
    receipt = HumanApprovalReceipt(
        approval_id=f"approval:{stage_id}:{decision}",
        gate_id=payload["gate_id"],
        human_ref="Suman(test)",
        canonical_surface="fixture",
        decision=decision,
        exact_action_approved=payload["requested_action"],
        action_fingerprint=payload["action_fingerprint"],
        constraints=constraints,
        created_at=CREATED_AT,
    )
    result = kernel.ingest_human_decision(
        instance_id="x-digest-tracer",
        decision=receipt,
        now=_now(),
    )
    self_status = kernel.ledger.get_workflow_instance("x-digest-tracer")
    if result.decision == "blocked":
        raise AssertionError(f"decision was not accepted: {result}")
    if self_status is None:
        raise AssertionError("workflow instance disappeared after human decision")


def _latest_human_gate_event(kernel: WorkflowKernel, stage_id: str) -> dict[str, Any] | None:
    for event in reversed(kernel.ledger.list_events()):
        if event["event_type"] == "human_gate_waiting" and event["payload"]["stage_id"] == stage_id:
            return event
    return None


def _latest_receipt_output(kernel: WorkflowKernel, stage_id: str, key: str) -> Any:
    row = kernel.ledger.connection.execute(
        """
        SELECT receipt_json
        FROM receipts r
        JOIN stage_runs sr ON sr.stage_run_id = r.stage_run_id
        WHERE r.instance_id = ? AND sr.stage_id = ?
        ORDER BY r.created_at DESC, r.receipt_id DESC
        LIMIT 1
        """,
        ("x-digest-tracer", stage_id),
    ).fetchone()
    if row is None:
        raise AssertionError(f"no receipt for stage {stage_id}")
    receipt = json.loads(row["receipt_json"])
    outputs = receipt["runtime_provenance"]["outputs"]
    return outputs[key]


def _artifact_roles(kernel: WorkflowKernel) -> set[str]:
    rows = kernel.ledger.connection.execute(
        "SELECT role FROM artifact_refs WHERE instance_id = ?",
        ("x-digest-tracer",),
    ).fetchall()
    return {row["role"] for row in rows}


def _latest_prompt_refs(kernel: WorkflowKernel, stage_id: str) -> set[str]:
    row = kernel.ledger.connection.execute(
        """
        SELECT receipt_json
        FROM receipts r
        JOIN stage_runs sr ON sr.stage_run_id = r.stage_run_id
        WHERE r.instance_id = ? AND sr.stage_id = ?
        ORDER BY r.created_at DESC, r.receipt_id DESC
        LIMIT 1
        """,
        ("x-digest-tracer", stage_id),
    ).fetchone()
    if row is None:
        return set()
    receipt = json.loads(row["receipt_json"])
    refs = ((receipt.get("prompt_provenance") or {}).get("refs") or [])
    return {str(ref.get("id")) for ref in refs if isinstance(ref, dict)}


def _now() -> datetime:
    return datetime.fromisoformat(CREATED_AT).astimezone(UTC)


if __name__ == "__main__":
    unittest.main()
