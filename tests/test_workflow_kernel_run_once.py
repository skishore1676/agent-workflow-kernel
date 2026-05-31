import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterRegistration,
    AdapterRegistry,
    KernelRuntimeConfig,
    LocalFakeRuntimeAdapter,
    PromptRef,
    PromptRegistry,
    RiskClass,
    StageDef,
    StageRunStatus,
    StageType,
    WorkflowDef,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowStatus,
)


UTC = timezone.utc
PROMPT_REFS = (
    PromptRef(id="identity.portable_worker", kind="identity", version="1.0.0"),
    PromptRef(id="policy.no_external_effects", kind="policy", version="1.0.0", render_mode="yaml"),
    PromptRef(id="lane.quality_review", kind="lane", version="1.0.0"),
    PromptRef(id="stage.review", kind="stage", version="1.0.0"),
)


class WorkflowKernelRunOnceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "kernel.sqlite3"
        self.ledger = WorkflowLedger(self.db_path)
        self.now = datetime(2026, 5, 31, 13, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.ledger.close()
        self.tmpdir.cleanup()

    def test_start_queues_first_stage_and_records_workflow_event(self) -> None:
        kernel = self.kernel_for(self.workflow_with_runtime_stage())

        instance = kernel.start(
            instance_id="instance-1",
            inputs={"objective": "write a receipt-backed fixture"},
            now=self.now,
        )

        self.assertEqual(instance.status, WorkflowStatus.RUNNING)
        self.assertEqual(instance.current_stage_id, "draft")
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.current_stage_id, "draft")
        run = self.ledger.get_stage_run("instance-1:draft:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.QUEUED)
        self.assertEqual(run.adapter_id, "runtime.local_fake")
        events = self.ledger.list_events()
        self.assertEqual([event["event_type"] for event in events], ["workflow_started"])
        self.assertEqual(events[0]["payload"]["first_stage_id"], "draft")

    def test_run_once_invokes_readonly_adapter_and_records_receipt(self) -> None:
        kernel = self.kernel_for(self.workflow_with_runtime_stage())
        kernel.start(
            instance_id="instance-1",
            inputs={"objective": "write a receipt-backed fixture"},
            now=self.now,
        )

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "succeeded")
        self.assertIsNotNone(step.adapter_result)
        assert step.adapter_result is not None
        self.assertEqual(step.adapter_result.status, "succeeded")
        self.assertEqual(step.receipt_id, "receipt:" + step.adapter_result.invocation_id + ":succeeded")
        run = self.ledger.get_stage_run("instance-1:draft:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.SUCCEEDED)
        self.assertEqual(run.receipt_id, step.receipt_id)

        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE receipt_id = ?",
            (step.receipt_id,),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        self.assertEqual(receipt["stage_id"], "draft")
        self.assertEqual(receipt["runtime_provenance"]["adapter_id"], "runtime.local_fake")
        self.assertEqual(receipt["policy_snapshot"]["decision"], "allow_with_receipt")
        self.assertEqual(
            receipt["runtime_provenance"]["outputs"]["runtime_input"]["stage"]["id"],
            "draft",
        )

        invocation_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM adapter_invocations"
        ).fetchone()["count"]
        self.assertEqual(invocation_count, 1)
        events = [event["event_type"] for event in self.ledger.list_events()]
        self.assertEqual(
            events,
            [
                "workflow_started",
                "stage_claimed",
                "receipt_recorded",
                "stage_completed",
                "workflow_stage_succeeded",
            ],
        )

    def test_prompt_refs_render_context_and_record_prompt_provenance(self) -> None:
        kernel = self.kernel_for(
            self.workflow_with_runtime_stage(prompt_refs=PROMPT_REFS),
            prompt_registry_path=ROOT / "prompts",
        )
        kernel.start(
            instance_id="instance-1",
            inputs={"objective": "review a prompt-backed stage"},
            now=self.now,
        )

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "succeeded")
        self.assertIsNotNone(step.receipt_id)
        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE receipt_id = ?",
            (step.receipt_id,),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        context_ref = receipt["context_packet_ref"]
        self.assertIsNotNone(context_ref)
        self.assertEqual(receipt["prompt_provenance"]["context"]["packet_id"], context_ref)
        self.assertEqual(len(receipt["prompt_provenance"]["refs"]), 4)
        self.assertTrue(
            receipt["prompt_provenance"]["context"]["rendered_input_digest"].startswith("sha256:")
        )

        invocation_row = self.ledger.connection.execute(
            "SELECT context_packet_ref FROM adapter_invocations"
        ).fetchone()
        self.assertEqual(invocation_row["context_packet_ref"], context_ref)
        runtime_input = receipt["runtime_provenance"]["outputs"]["runtime_input"]
        self.assertEqual(runtime_input["context_packet"]["packet_id"], context_ref)
        self.assertEqual(runtime_input["context_packet"]["workflow"]["id"], "toy-kernel")
        self.assertIn("identity.portable_worker", runtime_input["rendered_input"])

    def test_prompt_hash_mismatch_blocks_before_adapter_invocation(self) -> None:
        bad_ref = PromptRef(
            id="stage.review",
            kind="stage",
            version="1.0.0",
            content_hash="sha256:" + "0" * 64,
        )
        workflow = self.workflow_with_runtime_stage(prompt_refs=(bad_ref,))
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        kernel = WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(
                owner_id="kernel-test",
                adapter_registry=registry,
                prompt_registry=PromptRegistry.load(ROOT / "prompts"),
            ),
        )
        kernel.start(instance_id="instance-1", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("Hash mismatch", step.failure_summary or "")
        self.assertEqual(adapter.receipts, [])
        invocation_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM adapter_invocations"
        ).fetchone()["count"]
        self.assertEqual(invocation_count, 0)
        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE stage_run_id = ?",
            ("instance-1:draft:1",),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        self.assertEqual(receipt["kind"], "kernel.prompt_context")
        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(receipt["prompt_provenance"]["error"]["class"], "invalid_output")

    def test_missing_adapter_blocks_without_invocation(self) -> None:
        workflow = self.workflow_with_runtime_stage(adapter="runtime.missing")
        kernel = self.kernel_for(workflow)
        kernel.start(instance_id="instance-1", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("missing adapter registration", step.failure_summary or "")
        run = self.ledger.get_stage_run("instance-1:draft:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.BLOCKED)
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.BLOCKED)
        invocation_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM adapter_invocations"
        ).fetchone()["count"]
        self.assertEqual(invocation_count, 0)

    def test_human_gate_waits_without_adapter_invocation(self) -> None:
        workflow = WorkflowDef(
            id="toy-human",
            version="0.1.0",
            name="Toy human workflow",
            stages=(
                StageDef(
                    id="approve",
                    type=StageType.HUMAN_GATE,
                    adapter="surface.local_fake",
                    outcomes=("approved", "rejected"),
                ),
            ),
            transitions=(),
        )
        kernel = self.kernel_for(workflow)
        kernel.start(instance_id="instance-1", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("Human gate reached", step.failure_summary or "")
        run = self.ledger.get_stage_run("instance-1:approve:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.BLOCKED)
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.WAITING_ON_HUMAN)
        approval_required = self.ledger.connection.execute(
            "SELECT approval_required FROM stage_runs WHERE stage_run_id = ?",
            ("instance-1:approve:1",),
        ).fetchone()["approval_required"]
        self.assertEqual(approval_required, 1)

    def test_non_readonly_registration_requires_human_before_invocation(self) -> None:
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry(
            (
                AdapterRegistration.from_runtime_adapter(
                    adapter,
                    side_effects=(RiskClass.EXTERNAL_EFFECT,),
                ),
            )
        )
        kernel = WorkflowKernel(
            self.ledger,
            self.workflow_with_runtime_stage(),
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-1", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("approval", step.failure_summary or "")
        self.assertEqual(adapter.receipts, [])
        invocation_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM adapter_invocations"
        ).fetchone()["count"]
        self.assertEqual(invocation_count, 0)

    def kernel_for(
        self,
        workflow: WorkflowDef,
        *,
        prompt_registry: PromptRegistry | None = None,
        prompt_registry_path: Path | None = None,
    ) -> WorkflowKernel:
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        return WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(
                owner_id="kernel-test",
                adapter_registry=registry,
                prompt_registry=prompt_registry,
                prompt_registry_path=prompt_registry_path,
            ),
        )

    def workflow_with_runtime_stage(
        self,
        *,
        adapter: str = "runtime.local_fake",
        prompt_refs: tuple[PromptRef, ...] = (),
    ) -> WorkflowDef:
        return WorkflowDef(
            id="toy-kernel",
            version="0.1.0",
            name="Toy kernel workflow",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter=adapter,
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                    prompt_refs=prompt_refs,
                ),
            ),
            transitions=(),
        )


if __name__ == "__main__":
    unittest.main()
