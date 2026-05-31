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
    ApprovalDecision,
    HumanApprovalReceipt,
    KernelRuntimeConfig,
    LocalFakeRuntimeAdapter,
    RiskClass,
    StageDef,
    StageRunStatus,
    StageType,
    Transition,
    WorkflowDef,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowStatus,
)


UTC = timezone.utc


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

        self.assertEqual(step.decision, "waiting_on_human")
        self.assertIn("waiting for explicit decision", step.failure_summary or "")
        run = self.ledger.get_stage_run("instance-1:approve:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.WAITING_ON_HUMAN)
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.WAITING_ON_HUMAN)
        approval_required = self.ledger.connection.execute(
            "SELECT approval_required FROM stage_runs WHERE stage_run_id = ?",
            ("instance-1:approve:1",),
        ).fetchone()["approval_required"]
        self.assertEqual(approval_required, 1)
        events = [event["event_type"] for event in self.ledger.list_events()]
        self.assertIn("human_gate_waiting", events)

    def test_success_queues_next_stage_from_transition(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        kernel.start(instance_id="instance-1", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "succeeded")
        draft = self.ledger.get_stage_run("instance-1:draft:1")
        approve = self.ledger.get_stage_run("instance-1:approve:1")
        self.assertIsNotNone(draft)
        self.assertIsNotNone(approve)
        assert draft is not None
        assert approve is not None
        self.assertEqual(draft.status, StageRunStatus.SUCCEEDED)
        self.assertEqual(approve.status, StageRunStatus.QUEUED)
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.RUNNING)
        self.assertEqual(stored.current_stage_id, "approve")

    def test_approved_human_decision_resumes_to_next_stage(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-1")
        decision = self._decision_from_waiting_gate(
            approval_id="approval-1",
            decision=ApprovalDecision.APPROVED,
        )

        result = kernel.ingest_human_decision(
            instance_id="instance-1",
            decision=decision,
            now=self.now,
        )

        self.assertEqual(result.decision, "queued")
        self.assertEqual(result.outcome, "approval_granted")
        self.assertEqual(result.queued_stage_id, "apply")
        approve = self.ledger.get_stage_run("instance-1:approve:1")
        apply = self.ledger.get_stage_run("instance-1:apply:1")
        self.assertIsNotNone(approve)
        self.assertIsNotNone(apply)
        assert approve is not None
        assert apply is not None
        self.assertEqual(approve.status, StageRunStatus.SUCCEEDED)
        self.assertEqual(approve.receipt_id, "approval-1")
        self.assertEqual(apply.status, StageRunStatus.QUEUED)
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.RUNNING)
        self.assertEqual(stored.current_stage_id, "apply")
        decision_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM human_decisions"
        ).fetchone()["count"]
        self.assertEqual(decision_count, 1)

    def test_rejected_human_decision_does_not_queue_unsafe_next_stage(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-1")
        decision = self._decision_from_waiting_gate(
            approval_id="approval-reject",
            decision=ApprovalDecision.REJECTED,
        )

        result = kernel.ingest_human_decision(
            instance_id="instance-1",
            decision=decision,
            now=self.now,
        )

        self.assertEqual(result.decision, "terminal")
        self.assertEqual(result.outcome, "reject")
        self.assertEqual(result.terminal_status, WorkflowStatus.POLICY_DENIED)
        self.assertIsNone(self.ledger.get_stage_run("instance-1:apply:1"))
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.POLICY_DENIED)
        self.assertIsNone(stored.current_stage_id)

    def test_revise_human_decision_queues_configured_revision_path_only(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-1")
        decision = self._decision_from_waiting_gate(
            approval_id="approval-revise",
            decision=ApprovalDecision.REVISE,
        )

        result = kernel.ingest_human_decision(
            instance_id="instance-1",
            decision=decision,
            now=self.now,
        )

        self.assertEqual(result.decision, "queued")
        self.assertEqual(result.outcome, "revise_plan")
        self.assertEqual(result.queued_stage_id, "draft")
        draft_retry = self.ledger.get_stage_run("instance-1:draft:2")
        self.assertIsNotNone(draft_retry)
        assert draft_retry is not None
        self.assertEqual(draft_retry.status, StageRunStatus.QUEUED)
        self.assertIsNone(self.ledger.get_stage_run("instance-1:apply:1"))

    def test_missing_human_decision_blocks_waiting_gate(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-1")

        missing = kernel.ingest_human_decision(
            instance_id="instance-1",
            decision=None,
            now=self.now,
        )

        self.assertEqual(missing.decision, "blocked")
        self.assertIn("Missing human decision", missing.failure_summary or "")
        run = self.ledger.get_stage_run("instance-1:approve:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.BLOCKED)
        self.assertIsNone(self.ledger.get_stage_run("instance-1:apply:1"))

    def test_mismatched_human_decision_blocks_waiting_gate(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-1")
        mismatched = self._decision_from_waiting_gate(
            approval_id="approval-mismatch",
            decision=ApprovalDecision.APPROVED,
            action_fingerprint="wrong-fingerprint",
        )

        result = kernel.ingest_human_decision(
            instance_id="instance-1",
            decision=mismatched,
            now=self.now,
        )

        self.assertEqual(result.decision, "blocked")
        self.assertIn("fingerprint", result.failure_summary or "")
        run = self.ledger.get_stage_run("instance-1:approve:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.BLOCKED)
        self.assertIsNone(self.ledger.get_stage_run("instance-1:apply:1"))

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

    def kernel_for(self, workflow: WorkflowDef) -> WorkflowKernel:
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        return WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )

    def workflow_with_runtime_stage(self, *, adapter: str = "runtime.local_fake") -> WorkflowDef:
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
                ),
            ),
            transitions=(),
        )

    def workflow_with_human_gate(self) -> WorkflowDef:
        return WorkflowDef(
            id="toy-resume",
            version="0.1.0",
            name="Toy resumable workflow",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                ),
                StageDef(
                    id="approve",
                    type=StageType.HUMAN_GATE,
                    adapter="surface.local_fake",
                    outcomes=("approval_granted", "reject", "revise_plan"),
                    actors={"operator": "Suman"},
                ),
                StageDef(
                    id="apply",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("applied",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                ),
            ),
            transitions=(
                Transition(from_stage="draft", on="done", to_stage="approve"),
                Transition(from_stage="approve", on="approval_granted", to_stage="apply"),
                Transition(from_stage="approve", on="reject", terminal="policy_denied"),
                Transition(from_stage="approve", on="revise_plan", to_stage="draft"),
                Transition(from_stage="apply", on="applied", terminal="done"),
            ),
        )

    def _run_to_waiting_gate(self, kernel: WorkflowKernel, *, instance_id: str) -> None:
        kernel.start(instance_id=instance_id, inputs={}, now=self.now)
        first = kernel.run_once(now=self.now)
        self.assertEqual(first.decision, "succeeded")
        gate = kernel.run_once(now=self.now)
        self.assertEqual(gate.decision, "waiting_on_human")

    def _decision_from_waiting_gate(
        self,
        *,
        approval_id: str,
        decision: ApprovalDecision,
        action_fingerprint: str | None = None,
    ) -> HumanApprovalReceipt:
        gate_event = next(
            event for event in self.ledger.list_events()
            if event["event_type"] == "human_gate_waiting"
        )
        payload = gate_event["payload"]
        return HumanApprovalReceipt(
            approval_id=approval_id,
            gate_id=payload["gate_id"],
            human_ref="Suman",
            canonical_surface="local_test_fixture",
            decision=decision,
            exact_action_approved=payload["requested_action"],
            action_fingerprint=action_fingerprint or payload["action_fingerprint"],
            evidence_refs=(f"event:{gate_event['event_id']}",),
            created_at=self.now,
            transcript_or_message_ref="local-test://human-decision",
        )


if __name__ == "__main__":
    unittest.main()
