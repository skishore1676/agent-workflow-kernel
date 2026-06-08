import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "a2a"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "artifact_validation"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "ivy"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterInvocation,
    AdapterRegistration,
    AdapterRegistry,
    AdapterResult,
    ArtifactRef,
    CapabilitySet,
    HumanApprovalReceipt,
    KernelRuntimeConfig,
    PromptRegistry,
    Receipt,
    RuntimeAdapter,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowStatus,
    digest_data,
    load_workflow_file,
    make_adapter_receipt,
    result_from_receipt,
)
from agent_workflow_kernel.adapters import ADAPTER_STATUS_SUCCEEDED  # noqa: E402
from agent_workflow_kernel_a2a import A2AReviewRuntimeAdapter  # noqa: E402
from agent_workflow_kernel_artifact_validation import ArtifactHashValidatorAdapter  # noqa: E402


WORKFLOW_PATH = ROOT / "workflows" / "ivy_jonah_editorial.yaml"
PROMPTS_PATH = ROOT / "prompts"
CREATED_AT = "2026-06-07T12:00:00+00:00"


class MockIvyRuntimeAdapter:
    adapter_id = "runtime.agent"
    family = AdapterFamily.RUNTIME
    operations = ("invoke",)

    def __init__(self) -> None:
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("mock_ivy_writer",),
        )

    def invoke(
        self,
        invocation: AdapterInvocation,
        runtime_input: Mapping[str, Any],
    ) -> AdapterResult:
        stage_id = runtime_input["stage"]["id"]
        if stage_id == "build_draft_package":
            artifacts = (
                ArtifactRef(
                    artifact_id=f"{invocation.stage_run_id}:draft_package",
                    role="draft_package",
                    uri=f"awk://{invocation.instance_id}/{invocation.stage_run_id}/draft",
                    content_hash=digest_data({"draft": "ivy-v1"}),
                    mime_type="application/json",
                    created_by=self.adapter_id,
                ),
                ArtifactRef(
                    artifact_id=f"{invocation.stage_run_id}:source_trail",
                    role="source_trail",
                    uri=f"awk://{invocation.instance_id}/{invocation.stage_run_id}/source-trail",
                    content_hash=digest_data({"source_trail": "fixture"}),
                    mime_type="application/json",
                    created_by=self.adapter_id,
                ),
            )
            outputs = {"outcome": "ready", "draft_hash": artifacts[0].content_hash}
        elif stage_id == "revise_draft":
            artifacts = (
                ArtifactRef(
                    artifact_id=f"{invocation.stage_run_id}:revised_draft_package",
                    role="revised_draft_package",
                    uri=f"awk://{invocation.instance_id}/{invocation.stage_run_id}/revised-draft",
                    content_hash=digest_data({"draft": "ivy-v2"}),
                    mime_type="application/json",
                    created_by=self.adapter_id,
                ),
            )
            outputs = {"outcome": "revised", "draft_hash": artifacts[0].content_hash}
        else:
            artifacts = ()
            outputs = {"outcome": "blocked", "stage_id": stage_id}
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Mock Ivy completed {stage_id}.",
            created_at=CREATED_AT,
            artifact_refs=artifacts,
            outputs=outputs,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs, artifact_refs=artifacts)


class IvyJonahNativeAdaptersTest(unittest.TestCase):
    def test_needs_revision_routes_from_jonah_back_to_ivy(self) -> None:
        with _kernel(
            scripted_turn_batches=(
                ({"actor": "reviewer", "message": "Please tighten the argument.", "outcome": "needs_revision"},),
            )
        ) as kernel:
            _start_and_select_source(kernel)
            _run_until_stage(kernel, "editor_review")

            step = kernel.run_once(now=_now())

            self.assertEqual(step.decision, "succeeded")
            self.assertEqual(step.adapter_result.outputs["outcome"], "needs_revision")
            prompt_binding = step.adapter_result.outputs["prompt_binding"]
            self.assertIn("context_packet_ref", prompt_binding)
            self.assertIn("prompt_bundle_digest", prompt_binding)
            instance = kernel.ledger.get_workflow_instance("ivy-native-test")
            self.assertEqual(instance.status, WorkflowStatus.RUNNING)
            self.assertEqual(instance.current_stage_id, "revise_draft")

    def test_ping_pong_budget_exceeded_blocks_workflow(self) -> None:
        over_budget_turns = tuple(
            {"actor": "reviewer" if index % 2 else "producer", "message": f"turn {index}"}
            for index in range(7)
        )
        with _kernel(scripted_turn_batches=(over_budget_turns,)) as kernel:
            _start_and_select_source(kernel)
            _run_until_stage(kernel, "editor_review")

            step = kernel.run_once(now=_now())

            self.assertEqual(step.adapter_result.outputs["outcome"], "block")
            self.assertTrue(step.adapter_result.outputs["budget"]["exceeded"])
            self.assertIn("max_ping_pong_turns exceeded", step.adapter_result.outputs["budget"]["reason"])
            instance = kernel.ledger.get_workflow_instance("ivy-native-test")
            self.assertEqual(instance.status, WorkflowStatus.BLOCKED)
            self.assertIsNone(instance.current_stage_id)

    def test_validate_editorial_state_flags_mismatched_hash_as_stale_review(self) -> None:
        with _kernel(
            scripted_turn_batches=(
                (
                    {
                        "actor": "reviewer",
                        "message": "Accepted, but this verdict references an older draft.",
                        "outcome": "accepted",
                        "verdict_packet": {
                            "reviewed_draft_hash": "sha256:stale-review-fixture",
                        },
                    },
                ),
            )
        ) as kernel:
            _start_and_select_source(kernel)
            _run_until_stage(kernel, "validate_editorial_state")

            step = kernel.run_once(now=_now())

            self.assertEqual(step.adapter_result.outputs["outcome"], "stale_review")
            self.assertNotEqual(
                step.adapter_result.outputs["current_draft_hash"],
                step.adapter_result.outputs["reviewed_draft_hash"],
            )
            instance = kernel.ledger.get_workflow_instance("ivy-native-test")
            self.assertEqual(instance.status, WorkflowStatus.BLOCKED)
            self.assertIsNone(instance.current_stage_id)


class _kernel:
    def __init__(self, *, scripted_turn_batches: tuple[tuple[Mapping[str, Any], ...], ...]) -> None:
        self.temp_dir = TemporaryDirectory()
        self.ledger = WorkflowLedger(Path(self.temp_dir.name) / "workflow.sqlite")
        workflow = load_workflow_file(WORKFLOW_PATH)
        ivy = MockIvyRuntimeAdapter()
        jonah = A2AReviewRuntimeAdapter(
            scripted_turn_batches=scripted_turn_batches,
            created_at=CREATED_AT,
        )
        validator = ArtifactHashValidatorAdapter(created_at=CREATED_AT)
        registry = AdapterRegistry(
            (
                AdapterRegistration.from_runtime_adapter(ivy),
                AdapterRegistration.from_runtime_adapter(jonah),
                AdapterRegistration.from_lane_adapter(validator),
            )
        )
        self.kernel = WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(
                owner_id="ivy-native-test",
                adapter_registry=registry,
                prompt_registry=PromptRegistry.load(PROMPTS_PATH),
            ),
        )

    def __enter__(self) -> WorkflowKernel:
        return self.kernel

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.ledger.close()
        self.temp_dir.cleanup()


def _start_and_select_source(kernel: WorkflowKernel) -> None:
    kernel.start(
        instance_id="ivy-native-test",
        inputs={
            "approved_source_packet": {"source": "fixture"},
            "editorial_brief": "Make the argument clear.",
        },
        now=_now(),
    )
    waiting = kernel.run_once(now=_now())
    assert waiting.decision == "waiting_on_human"
    gate_event = next(
        event for event in reversed(kernel.ledger.list_events()) if event["event_type"] == "human_gate_waiting"
    )
    payload = gate_event["payload"]
    decision = HumanApprovalReceipt(
        approval_id="approval:selected-source",
        gate_id=payload["gate_id"],
        human_ref="Suman(test)",
        canonical_surface="local_test",
        decision="selected",
        exact_action_approved=payload["requested_action"],
        action_fingerprint=payload["action_fingerprint"],
        created_at=CREATED_AT,
    )
    result = kernel.ingest_human_decision(
        instance_id="ivy-native-test",
        decision=decision,
        now=_now(),
    )
    assert result.queued_stage_id == "build_draft_package"


def _run_until_stage(kernel: WorkflowKernel, stage_id: str, *, max_steps: int = 10) -> None:
    for _ in range(max_steps):
        instance = kernel.ledger.get_workflow_instance("ivy-native-test")
        if instance.current_stage_id == stage_id:
            return
        step = kernel.run_once(now=_now())
        if step.decision == "idle":
            break
    current = kernel.ledger.get_workflow_instance("ivy-native-test").current_stage_id
    raise AssertionError(f"workflow did not reach {stage_id}; current stage is {current}")


def _now() -> datetime:
    return datetime.fromisoformat(CREATED_AT).astimezone(UTC)


if __name__ == "__main__":
    unittest.main()
