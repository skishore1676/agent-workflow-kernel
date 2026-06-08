import json
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "a2a"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "artifact_validation"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "ivy"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterRegistration,
    AdapterRegistry,
    HumanApprovalReceipt,
    KernelRuntimeConfig,
    PromptRegistry,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowStatus,
    digest_data,
    load_workflow_file,
)
from agent_workflow_kernel_a2a import A2AReviewRuntimeAdapter  # noqa: E402
from agent_workflow_kernel_artifact_validation import ArtifactHashValidatorAdapter  # noqa: E402
from agent_workflow_kernel_openclaw import OpenClawAgentRuntimeAdapter  # noqa: E402


WORKFLOW_PATH = ROOT / "workflows" / "ivy_jonah_editorial.yaml"
PROMPTS_PATH = ROOT / "prompts"
CREATED_AT = "2026-06-07T12:00:00+00:00"


class MockOpenClawRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        text: bool,
        capture_output: bool,
        timeout: int,
        check: bool,
    ) -> CompletedProcess[str]:
        del cwd, env, text, capture_output, timeout, check
        self.calls.append(list(cmd))
        packet = _packet_from_command(cmd)
        stage_id = str(packet.get("stage_id"))
        if stage_id == "build_draft_package":
            output = {
                "status": "done",
                "outcome": "ready",
                "session": {"session_id": "sess-build"},
                "artifact_refs": [
                    {
                        "artifact_id": f"{packet['stage_run_id']}:draft_package",
                        "role": "draft_package",
                        "uri": f"awk://{packet['instance_id']}/{packet['stage_run_id']}/draft",
                        "content_hash": digest_data({"draft": "ivy-v1"}),
                        "mime_type": "application/json",
                    },
                    {
                        "artifact_id": f"{packet['stage_run_id']}:source_trail",
                        "role": "source_trail",
                        "uri": f"awk://{packet['instance_id']}/{packet['stage_run_id']}/source-trail",
                        "content_hash": digest_data({"source_trail": "fixture"}),
                        "mime_type": "application/json",
                    },
                ],
            }
        elif stage_id == "revise_draft":
            output = {
                "status": "done",
                "outcome": "revised",
                "session": {"session_id": "sess-revise"},
                "artifact_refs": [
                    {
                        "artifact_id": f"{packet['stage_run_id']}:revised_draft_package",
                        "role": "revised_draft_package",
                        "uri": f"awk://{packet['instance_id']}/{packet['stage_run_id']}/revised-draft",
                        "content_hash": digest_data({"draft": "ivy-v2"}),
                        "mime_type": "application/json",
                    },
                ],
            }
        else:
            output = {"status": "blocked", "outcome": "blocked", "stage_id": stage_id}
        return CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(output))


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

    def test_revision_path_validates_revised_draft_before_p5_gate(self) -> None:
        with _kernel(
            scripted_turn_batches=(
                (
                    {
                        "actor": "reviewer",
                        "message": "Needs a sharper governance recommendation.",
                        "outcome": "needs_revision",
                    },
                ),
                (
                    {
                        "actor": "reviewer",
                        "message": "Accepted after revision.",
                        "outcome": "accepted",
                    },
                ),
            )
        ) as kernel:
            _start_and_select_source(kernel)
            _run_until_stage(kernel, "p5_final_approval")

            revised = _latest_artifact_by_role(kernel, "revised_draft_package")
            validated = _latest_artifact_by_role(kernel, "validated_draft_package")
            self.assertEqual(validated["content_hash"], revised["content_hash"])
            self.assertEqual(validated["uri"], revised["uri"])

            waiting = kernel.run_once(now=_now())
            self.assertEqual(waiting.decision, "waiting_on_human")
            instance = kernel.ledger.get_workflow_instance("ivy-native-test")
            self.assertEqual(instance.status, WorkflowStatus.WAITING_ON_HUMAN)
            self.assertEqual(instance.current_stage_id, "p5_final_approval")


class _kernel:
    def __init__(self, *, scripted_turn_batches: tuple[tuple[Mapping[str, Any], ...], ...]) -> None:
        self.temp_dir = TemporaryDirectory()
        self.ledger = WorkflowLedger(Path(self.temp_dir.name) / "workflow.sqlite")
        workflow = load_workflow_file(WORKFLOW_PATH)
        ivy = OpenClawAgentRuntimeAdapter(
            openclaw_cli="openclaw",
            default_agent="ivy_writing_ops",
            artifact_root=Path(self.temp_dir.name) / "openclaw-artifacts",
            runner=MockOpenClawRunner(),
            created_at=CREATED_AT,
        )
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


def _packet_from_command(cmd: list[str]) -> dict[str, Any]:
    if "--message" in cmd:
        index = cmd.index("--message")
        return json.loads(cmd[index + 1])
    if "--packet" in cmd:
        index = cmd.index("--packet")
        return json.loads(Path(cmd[index + 1]).read_text(encoding="utf-8"))
    return {}


def _latest_artifact_by_role(kernel: WorkflowKernel, role: str) -> dict[str, Any]:
    row = kernel.ledger.connection.execute(
        """
        SELECT *
        FROM artifact_refs
        WHERE instance_id = ? AND role = ?
        ORDER BY created_at DESC, artifact_id DESC
        LIMIT 1
        """,
        ("ivy-native-test", role),
    ).fetchone()
    if row is None:
        raise AssertionError(f"artifact role {role!r} was not recorded")
    return dict(row)


def _now() -> datetime:
    return datetime.fromisoformat(CREATED_AT).astimezone(UTC)


if __name__ == "__main__":
    unittest.main()
