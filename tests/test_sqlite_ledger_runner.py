import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    ArtifactRef,
    FailureClass,
    Receipt,
    RunnerResult,
    StageRun,
    StageRunStatus,
    WorkflowInstance,
    WorkflowLedger,
    WorkflowRunner,
    WorkflowStatus,
)


UTC = timezone.utc


class SQLiteLedgerRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "kernel.sqlite3"
        self.ledger = WorkflowLedger(self.db_path)
        self.ledger.initialize()
        self.created_at = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
        self.ledger.insert_workflow_instance(
            WorkflowInstance(
                instance_id="instance-1",
                workflow_def_id="workflow-1",
                workflow_version="0.1.0",
                status=WorkflowStatus.RUNNING,
                current_stage_id="stage-1",
                input_hash="input-sha",
            ),
            created_at=self.created_at,
        )

    def tearDown(self) -> None:
        self.ledger.close()
        self.tmpdir.cleanup()

    def insert_run(self, run_id: str = "run-1") -> None:
        self.ledger.insert_stage_run(
            StageRun(
                stage_run_id=run_id,
                instance_id="instance-1",
                stage_id="stage-1",
                status=StageRunStatus.QUEUED,
                adapter_id="runtime.fake",
                actor_ref="worker",
            ),
            input_hash="stage-input-sha",
            created_at=self.created_at,
        )

    def test_initialize_creates_required_tables(self) -> None:
        expected = {
            "workflow_instances",
            "stage_runs",
            "receipts",
            "artifact_refs",
            "adapter_invocations",
            "events",
            "child_sessions",
        }
        rows = self.ledger.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

        self.assertTrue(expected.issubset({row["name"] for row in rows}))

    def test_claim_is_exclusive_and_records_event(self) -> None:
        self.insert_run()

        claimed = self.ledger.claim_next_queued_run(
            owner_id="runner-a", lease_seconds=30, now=self.created_at
        )
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.status, StageRunStatus.CLAIMED)
        self.assertIsNotNone(claimed.lease_token)

        second_claim = self.ledger.claim_next_queued_run(
            owner_id="runner-b", lease_seconds=30, now=self.created_at
        )
        self.assertIsNone(second_claim)

        events = self.ledger.list_events(stage_run_id="run-1")
        self.assertEqual([event["event_type"] for event in events], ["stage_claimed"])
        self.assertEqual(events[0]["actor"], "runner-a")

    def test_receipt_artifact_and_completion_are_append_only_then_state_update(self) -> None:
        self.insert_run()
        claimed = self.ledger.claim_next_queued_run(
            owner_id="runner-a", lease_seconds=30, now=self.created_at
        )
        assert claimed is not None and claimed.lease_token is not None
        receipt = Receipt(
            receipt_id="receipt-1",
            kind="stage_result",
            workflow_id="workflow-1",
            instance_id="instance-1",
            stage_id="stage-1",
            stage_run_id="run-1",
            status="succeeded",
            summary="Fake adapter completed.",
            created_at=self.created_at.isoformat(),
            artifact_refs=(
                ArtifactRef(
                    artifact_id="artifact-1",
                    role="output",
                    uri="memory://artifact-1",
                    content_hash="artifact-sha",
                ),
            ),
            runtime_provenance={"actor": "runner-a", "adapter": "runtime.fake"},
        )

        self.ledger.record_receipt(receipt)
        self.ledger.complete_stage_run(
            stage_run_id="run-1",
            lease_token=claimed.lease_token,
            receipt_id="receipt-1",
            output_hash="output-sha",
            now=self.created_at + timedelta(seconds=5),
            actor="runner-a",
        )

        run = self.ledger.get_stage_run("run-1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.SUCCEEDED)
        self.assertEqual(run.receipt_id, "receipt-1")
        artifact_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM artifact_refs"
        ).fetchone()["count"]
        self.assertEqual(artifact_count, 1)
        events = [event["event_type"] for event in self.ledger.list_events(stage_run_id="run-1")]
        self.assertEqual(events, ["stage_claimed", "receipt_recorded", "stage_completed"])

    def test_recovery_sweep_requeues_expired_pre_start_claims(self) -> None:
        self.insert_run()
        claimed = self.ledger.claim_next_queued_run(
            owner_id="runner-a", lease_seconds=1, now=self.created_at
        )
        assert claimed is not None

        actions = self.ledger.sweep_stale_leases(
            now=self.created_at + timedelta(seconds=2), actor="recovery"
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].stage_run_id, "run-1")
        self.assertEqual(actions[0].action, "requeued")
        recovered = self.ledger.get_stage_run("run-1")
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered.status, StageRunStatus.QUEUED)
        self.assertIsNone(recovered.lease_token)
        events = [event["event_type"] for event in self.ledger.list_events(stage_run_id="run-1")]
        self.assertEqual(events, ["stage_claimed", "recovery"])

    def test_recovery_sweep_blocks_claims_with_start_evidence(self) -> None:
        self.insert_run()
        claimed = self.ledger.claim_next_queued_run(
            owner_id="runner-a", lease_seconds=1, now=self.created_at
        )
        assert claimed is not None
        self.ledger.append_event(
            instance_id="instance-1",
            stage_run_id="run-1",
            event_type="adapter_invocation_preflight",
            actor="runner-a",
            payload={
                "idempotency_key": "instance-1:stage-1:1",
                "side_effect_scope": {"adapter_id": "runtime.fake"},
            },
            created_at=self.created_at,
        )

        actions = self.ledger.sweep_stale_leases(
            now=self.created_at + timedelta(seconds=2), actor="recovery"
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action, "blocked")
        self.assertEqual(actions[0].failure_class, FailureClass.UNKNOWN_SIDE_EFFECT_STATE)
        recovered = self.ledger.get_stage_run("run-1")
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered.status, StageRunStatus.BLOCKED)
        self.assertEqual(recovered.failure_class, FailureClass.UNKNOWN_SIDE_EFFECT_STATE)

    def test_recovery_sweep_blocks_started_runs(self) -> None:
        self.insert_run()
        claimed = self.ledger.claim_next_queued_run(
            owner_id="runner-a", lease_seconds=1, now=self.created_at
        )
        assert claimed is not None and claimed.lease_token is not None
        self.ledger.mark_stage_run_started(
            stage_run_id="run-1",
            lease_token=claimed.lease_token,
            actor="runner-a",
            idempotency_key="instance-1:stage-1:1",
            side_effect_scope={"adapter_id": "runtime.fake"},
            now=self.created_at,
        )

        actions = self.ledger.sweep_stale_leases(
            now=self.created_at + timedelta(seconds=2), actor="recovery"
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action, "blocked")
        self.assertEqual(actions[0].failure_class, FailureClass.UNKNOWN_SIDE_EFFECT_STATE)

    def test_runner_skeleton_completes_through_injected_handler(self) -> None:
        self.insert_run()

        def handler(run: StageRun) -> RunnerResult:
            return RunnerResult(
                decision="succeeded",
                receipt=Receipt(
                    receipt_id="receipt-runner",
                    kind="stage_result",
                    workflow_id="workflow-1",
                    instance_id=run.instance_id,
                    stage_id=run.stage_id,
                    stage_run_id=run.stage_run_id,
                    status="succeeded",
                    summary="Handler completed without external adapter calls.",
                    created_at=self.created_at.isoformat(),
                    runtime_provenance={"actor": "runner-a"},
                ),
                output_hash="output-sha",
            )

        runner = WorkflowRunner(self.ledger, owner_id="runner-a")
        step = runner.run_once(handler, now=self.created_at)

        self.assertEqual(step.decision, "succeeded")
        run = self.ledger.get_stage_run("run-1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.SUCCEEDED)
        events = [event["event_type"] for event in self.ledger.list_events(stage_run_id="run-1")]
        self.assertEqual(
            events,
            ["stage_claimed", "stage_started", "receipt_recorded", "stage_completed"],
        )

    def test_runner_blocks_handler_exceptions_as_runtime_failures(self) -> None:
        self.insert_run()

        def handler(_: StageRun) -> RunnerResult:
            raise RuntimeError("transport died")

        runner = WorkflowRunner(self.ledger, owner_id="runner-a")
        step = runner.run_once(handler, now=self.created_at)

        self.assertEqual(step.decision, "failed")
        run = self.ledger.get_stage_run("run-1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.FAILED)
        self.assertEqual(run.failure_class, FailureClass.RUNTIME_FAILURE)

    def test_runner_retry_creates_append_only_attempt(self) -> None:
        self.insert_run()

        def handler(_: StageRun) -> RunnerResult:
            return RunnerResult(
                decision="retry",
                failure_class=FailureClass.RUNTIME_FAILURE,
                failure_summary="temporary transport failure",
                retry_after_at=self.created_at,
            )

        runner = WorkflowRunner(self.ledger, owner_id="runner-a")
        step = runner.run_once(handler, now=self.created_at)

        self.assertEqual(step.decision, "retry")
        first = self.ledger.get_stage_run("run-1")
        retry = self.ledger.get_stage_run("instance-1:stage-1:2")
        self.assertIsNotNone(first)
        self.assertIsNotNone(retry)
        assert first is not None
        assert retry is not None
        self.assertEqual(first.status, StageRunStatus.FAILED)
        self.assertEqual(retry.status, StageRunStatus.QUEUED)
        parent = self.ledger.connection.execute(
            "SELECT parent_stage_run_id, retry_count FROM stage_runs WHERE stage_run_id = ?",
            ("instance-1:stage-1:2",),
        ).fetchone()
        self.assertEqual(parent["parent_stage_run_id"], "run-1")
        self.assertEqual(parent["retry_count"], 1)

    def test_foreign_keys_are_enabled(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.insert_stage_run(
                StageRun(
                    stage_run_id="orphan",
                    instance_id="missing",
                    stage_id="stage-1",
                    status=StageRunStatus.QUEUED,
                )
            )


if __name__ == "__main__":
    unittest.main()
