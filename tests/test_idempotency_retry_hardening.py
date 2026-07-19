from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterRegistry,
    ArtifactRef,
    FailureClass,
    KernelRuntimeConfig,
    LedgerConflict,
    LedgerSchemaError,
    Receipt,
    RunnerResult,
    StageDef,
    StageRun,
    StageRunStatus,
    StageType,
    WorkflowDef,
    WorkflowInstance,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowRunner,
    WorkflowStatus,
    resolve_stage_lease_policy,
)


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


def workflow() -> WorkflowDef:
    return WorkflowDef(
        id="idempotent-workflow",
        version="1.0.0",
        name="Idempotency fixture",
        stages=(
            StageDef(
                id="build",
                type=StageType.AGENT_WORK,
                adapter="runtime.fixture",
                outcomes=("done",),
                timeout_seconds=60,
            ),
        ),
        transitions=(),
    )


def kernel(ledger: WorkflowLedger) -> WorkflowKernel:
    return WorkflowKernel(
        ledger,
        workflow(),
        KernelRuntimeConfig(
            owner_id="test-runner",
            adapter_registry=AdapterRegistry(),
            default_lease_seconds=120,
            completion_margin_seconds=30,
        ),
    )


class IdempotencyRetryHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tmpdir.name) / "ledger.sqlite3"
        ledger = WorkflowLedger(self.db)
        ledger.initialize()
        ledger.close()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_same_idempotency_key_is_atomic_under_concurrent_starts(self) -> None:
        barrier = threading.Barrier(2)

        def start(instance_id: str) -> str:
            ledger = WorkflowLedger(self.db)
            try:
                barrier.wait()
                return kernel(ledger).start(
                    instance_id=instance_id,
                    inputs={"project": "target", "artifact": "vp-deck"},
                    idempotency_key="target:vp-deck",
                    now=NOW,
                ).instance_id
            finally:
                ledger.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            instance_ids = list(pool.map(start, ("instance-a", "instance-b")))
        self.assertEqual(len(set(instance_ids)), 1)
        ledger = WorkflowLedger(self.db)
        try:
            self.assertEqual(
                ledger.get_workflow_instance_by_idempotency_key("target:vp-deck").instance_id,
                instance_ids[0],
            )
            self.assertEqual(
                ledger.connection.execute("SELECT COUNT(*) FROM workflow_instances").fetchone()[0], 1
            )
            self.assertEqual(ledger.connection.execute("SELECT COUNT(*) FROM stage_runs").fetchone()[0], 1)
            self.assertEqual(ledger.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        finally:
            ledger.close()

    def test_same_key_different_input_conflicts(self) -> None:
        ledger = WorkflowLedger(self.db)
        try:
            first = kernel(ledger).start(
                instance_id="instance-a", inputs={"version": 1},
                idempotency_key="artifact-key", now=NOW,
            )
            replay = kernel(ledger).start(
                instance_id="different-request-id", inputs={"version": 1},
                idempotency_key="artifact-key", now=NOW,
            )
            self.assertEqual(replay.instance_id, first.instance_id)
            with self.assertRaisesRegex(LedgerConflict, "different workflow input"):
                kernel(ledger).start(
                    instance_id="instance-b", inputs={"version": 2},
                    idempotency_key="artifact-key", now=NOW,
                )
        finally:
            ledger.close()

    def test_v1_duplicate_keys_fail_migration_without_mutation(self) -> None:
        ledger = WorkflowLedger(self.db)
        ledger.connection.execute("DROP INDEX uq_workflow_instances_idempotency_key")
        for instance_id in ("duplicate-a", "duplicate-b"):
            ledger.connection.execute(
                """
                INSERT INTO workflow_instances (
                  instance_id, workflow_def_id, workflow_version, status,
                  idempotency_key, input_hash, recovery_epoch, created_at, updated_at
                ) VALUES (?, 'w', '1', 'blocked', 'duplicate-key', 'h', 0, ?, ?)
                """,
                (instance_id, NOW.isoformat(), NOW.isoformat()),
            )
        ledger.connection.execute("PRAGMA user_version = 1")
        ledger.connection.commit()
        with self.assertRaisesRegex(LedgerSchemaError, "duplicate workflow idempotency"):
            ledger.initialize()
        self.assertEqual(ledger.connection.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(
            ledger.connection.execute(
                "SELECT COUNT(*) FROM workflow_instances WHERE idempotency_key = 'duplicate-key'"
            ).fetchone()[0],
            2,
        )
        ledger.close()

    def _failed_instance(self, ledger: WorkflowLedger) -> None:
        ledger.insert_workflow_instance(
            WorkflowInstance(
                instance_id="instance-1", workflow_def_id="w", workflow_version="1",
                status=WorkflowStatus.BLOCKED, current_stage_id="build", input_hash="input",
            ),
            created_at=NOW,
        )
        ledger.insert_stage_run(
            StageRun(
                stage_run_id="instance-1:build:1", instance_id="instance-1",
                stage_id="build", status=StageRunStatus.FAILED, attempt=1,
                adapter_id="runtime.fixture", actor_ref="builder",
            ),
            input_hash="stage-input",
            idempotency_key="instance-1:build:1",
            created_at=NOW,
        )

    def test_operator_retry_is_append_only_receipted_and_rejects_duplicate_active_retry(self) -> None:
        ledger = WorkflowLedger(self.db)
        try:
            self._failed_instance(ledger)
            retry = ledger.operator_retry_stage(
                instance_id="instance-1", reason="provider outage cleared", now=NOW
            )
            self.assertEqual(retry.status, StageRunStatus.QUEUED)
            self.assertEqual(retry.attempt, 2)
            self.assertEqual(ledger.get_stage_run("instance-1:build:1").status, StageRunStatus.FAILED)
            self.assertEqual(
                ledger.get_workflow_instance("instance-1").status, WorkflowStatus.RETRYING
            )
            receipt = ledger.connection.execute(
                "SELECT receipt_kind FROM receipts WHERE stage_run_id = ?", (retry.stage_run_id,)
            ).fetchone()
            self.assertEqual(receipt["receipt_kind"], "operator_retry")
            with self.assertRaisesRegex(LedgerConflict, "active or human-gated"):
                ledger.operator_retry_stage(instance_id="instance-1", reason="duplicate")
        finally:
            ledger.close()

    def test_operator_retry_rejects_waiting_human_terminal_and_ambiguous_states(self) -> None:
        ledger = WorkflowLedger(self.db)
        try:
            self._failed_instance(ledger)
            ledger.update_workflow_instance(
                instance_id="instance-1", status=WorkflowStatus.WAITING_ON_HUMAN,
                current_stage_id="build", updated_at=NOW,
            )
            with self.assertRaisesRegex(LedgerConflict, "human authority"):
                ledger.operator_retry_stage(instance_id="instance-1", reason="unsafe")
            ledger.update_workflow_instance(
                instance_id="instance-1", status=WorkflowStatus.CANCELLED,
                current_stage_id=None, updated_at=NOW,
            )
            with self.assertRaisesRegex(LedgerConflict, "terminal"):
                ledger.operator_retry_stage(instance_id="instance-1", reason="unsafe")
        finally:
            ledger.close()

    def test_operator_retry_rejects_running_workflow_even_without_active_run(self) -> None:
        ledger = WorkflowLedger(self.db)
        try:
            self._failed_instance(ledger)
            ledger.update_workflow_instance(
                instance_id="instance-1", status=WorkflowStatus.RUNNING,
                current_stage_id="build", updated_at=NOW,
            )
            with self.assertRaisesRegex(LedgerConflict, "not in a retryable"):
                ledger.operator_retry_stage(instance_id="instance-1", reason="unsafe")
        finally:
            ledger.close()

    def test_operator_retry_requires_stage_run_id_when_failed_leaf_is_ambiguous(self) -> None:
        ledger = WorkflowLedger(self.db)
        try:
            self._failed_instance(ledger)
            ledger.insert_stage_run(
                StageRun(
                    stage_run_id="instance-1:review:1", instance_id="instance-1",
                    stage_id="review", status=StageRunStatus.BLOCKED, attempt=1,
                    adapter_id="runtime.fixture", actor_ref="reviewer",
                ),
                input_hash="review-input", created_at=NOW,
            )
            with self.assertRaisesRegex(LedgerConflict, "ambiguous"):
                ledger.operator_retry_stage(instance_id="instance-1", reason="choose one")
            retry = ledger.operator_retry_stage(
                instance_id="instance-1", stage_run_id="instance-1:review:1",
                reason="reviewer repair", now=NOW,
            )
            self.assertEqual(retry.stage_id, "review")
        finally:
            ledger.close()

    def test_lease_must_cover_provider_timeout_and_completion_margin(self) -> None:
        stage = workflow().stages[0]
        with self.assertRaisesRegex(ValueError, "provider timeout plus completion margin"):
            resolve_stage_lease_policy(
                workflow(), stage, runtime_default_seconds=89, minimum_seconds=90
            )
        resolved = resolve_stage_lease_policy(
            workflow(), stage, runtime_default_seconds=90, minimum_seconds=90
        )
        self.assertEqual(resolved.lease_seconds, 90)

    def test_cancel_during_blocking_provider_quarantines_late_result(self) -> None:
        setup = WorkflowLedger(self.db)
        self._failed_instance(setup)
        setup.connection.execute(
            "UPDATE stage_runs SET status = ? WHERE stage_run_id = ?",
            (StageRunStatus.QUEUED.value, "instance-1:build:1"),
        )
        setup.connection.execute(
            "UPDATE workflow_instances SET status = ? WHERE instance_id = ?",
            (WorkflowStatus.RUNNING.value, "instance-1"),
        )
        setup.connection.commit()
        setup.close()
        started = threading.Event()
        release = threading.Event()
        returned: list[object] = []

        def run_provider() -> None:
            ledger = WorkflowLedger(self.db)
            try:
                runner = WorkflowRunner(ledger, owner_id="runner-a")

                def blocking(run: StageRun) -> RunnerResult:
                    started.set()
                    release.wait(timeout=5)
                    return RunnerResult(
                        decision="succeeded",
                        output_hash="late-output-hash",
                        receipt=Receipt(
                            receipt_id="late-receipt", kind="stage_result", workflow_id="w",
                            instance_id=run.instance_id, stage_id=run.stage_id,
                            stage_run_id=run.stage_run_id, status="succeeded",
                            summary="returned after cancellation", created_at=NOW.isoformat(),
                            artifact_refs=(ArtifactRef(
                                artifact_id="late-artifact", role="output", uri="memory://late",
                                content_hash="late-output-hash",
                            ),),
                            runtime_provenance={"actor": "runner-a"},
                        ),
                    )

                returned.append(runner.run_once(blocking, instance_id="instance-1", now=NOW))
            finally:
                ledger.close()

        thread = threading.Thread(target=run_provider)
        thread.start()
        self.assertTrue(started.wait(timeout=5))
        operator = WorkflowLedger(self.db)
        operator.cancel_workflow_instance(
            instance_id="instance-1", reason="operator cancel", updated_at=NOW
        )
        release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        step = returned[0]
        self.assertFalse(step.authoritative)
        self.assertIsNotNone(step.late_result_id)
        self.assertEqual(
            operator.get_workflow_instance("instance-1").status, WorkflowStatus.CANCELLED
        )
        self.assertEqual(
            operator.get_stage_run("instance-1:build:1").status, StageRunStatus.SUPERSEDED
        )
        late = operator.list_late_results(stage_run_id="instance-1:build:1")
        self.assertEqual(len(late), 1)
        self.assertTrue(late[0]["evidence"]["quarantined"])
        self.assertFalse(late[0]["evidence"]["promotion_authorized"])
        self.assertEqual(operator.connection.execute("SELECT COUNT(*) FROM artifact_refs").fetchone()[0], 0)
        idle = WorkflowRunner(operator, owner_id="runner-b").run_once(lambda _: None, now=NOW)
        self.assertEqual(idle.decision, "idle")
        operator.close()


if __name__ == "__main__":
    unittest.main()
