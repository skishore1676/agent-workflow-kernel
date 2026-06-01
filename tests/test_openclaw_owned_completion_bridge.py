import json
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

from agent_workflow_kernel import WorkflowLedger, WorkflowStatus  # noqa: E402
from agent_workflow_kernel_openclaw import run_owned_completion_bridge  # noqa: E402


NOW = "2026-06-01T16:00:00+00:00"


class OpenClawOwnedCompletionBridgeTest(unittest.TestCase):
    def test_acknowledged_blackboard_artifacts_reach_terminal_awk_workflow_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("ivy", "weekly"))
            self.write_openclaw_done_state(openclaw, "awk-cutover-ivy-test", "Ivy/Jonah")
            self.write_openclaw_done_state(openclaw, "awk-cutover-weekly-test", "Weekly")

            summary = run_owned_completion_bridge(
                ledger_path=root / "awk-ledger.sqlite3",
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["artifact_count"], 2)
            self.assertEqual({result["status"] for result in summary["results"]}, {"done"})
            self.assertEqual({result["workflow_status"] for result in summary["results"]}, {"done"})
            self.assertEqual({result["terminal_event_count"] for result in summary["results"]}, {1})

            ledger = WorkflowLedger(root / "awk-ledger.sqlite3")
            try:
                rows = ledger.connection.execute(
                    "SELECT instance_id, workflow_def_id, status FROM workflow_instances ORDER BY instance_id"
                ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual({row["status"] for row in rows}, {WorkflowStatus.DONE.value})
                decisions = ledger.connection.execute(
                    "SELECT decision, human_ref, canonical_surface FROM human_decisions ORDER BY decision_id"
                ).fetchall()
                self.assertEqual([row["decision"] for row in decisions], ["acknowledged", "acknowledged"])
                self.assertEqual({row["human_ref"] for row in decisions}, {"Suman"})
                self.assertEqual({row["canonical_surface"] for row in decisions}, {"openclaw_blackboard"})
            finally:
                ledger.close()

    def test_missing_runner_receipt_leaves_verify_stage_queued_until_next_pickup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("weekly",))
            self.write_openclaw_done_state(
                openclaw,
                "awk-cutover-weekly-test",
                "Weekly",
                runner_status=None,
            )

            first = run_owned_completion_bridge(
                ledger_path=root / "awk-ledger.sqlite3",
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )

            self.assertFalse(first["ok"])
            result = first["results"][0]
            self.assertEqual(result["status"], "waiting_on_openclaw_runner")
            self.assertEqual(result["workflow_status"], "running")
            self.assertEqual(result["terminal_event_count"], 0)
            verify = [
                run
                for run in result["stage_runs"]
                if run["stage_id"] == "verify_openclaw_review_runner"
            ][0]
            self.assertEqual(verify["status"], "queued")

            self.write_runner_receipt(openclaw, "awk-cutover-weekly-test", "done")
            second = run_owned_completion_bridge(
                ledger_path=root / "awk-ledger.sqlite3",
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )
            self.assertTrue(second["ok"])
            self.assertEqual(second["results"][0]["workflow_status"], "done")
            self.assertEqual(second["results"][0]["terminal_event_count"], 1)

            third = run_owned_completion_bridge(
                ledger_path=root / "awk-ledger.sqlite3",
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )
            self.assertTrue(third["ok"])
            self.assertEqual(third["results"][0]["stop_reason"], "already_terminal")
            self.assertEqual(third["results"][0]["terminal_event_count"], 1)

    def test_unacknowledged_artifact_waits_at_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("ivy",))
            self.write_artifact_record(openclaw, "awk-cutover-ivy-test", "Ivy/Jonah")

            summary = run_owned_completion_bridge(
                ledger_path=root / "awk-ledger.sqlite3",
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )

            self.assertFalse(summary["ok"])
            result = summary["results"][0]
            self.assertEqual(result["status"], "waiting_on_human")
            self.assertEqual(result["workflow_status"], "waiting_on_human")
            waiting = [
                run
                for run in result["stage_runs"]
                if run["stage_id"] == "blackboard_acknowledgement"
            ][0]
            self.assertEqual(waiting["status"], "waiting_on_human")

    def write_cutover_receipt(self, root: Path, lanes: tuple[str, ...]) -> Path:
        records = []
        for lane in lanes:
            records.append(
                {
                    "lane_id": lane,
                    "artifact_id": f"awk-cutover-{lane}-test",
                    "title": f"{lane} review",
                    "status": "succeeded",
                }
            )
        path = root / "cutover_receipt.json"
        path.write_text(
            json.dumps({"schema": "workflow.kernel.openclaw-live-cutover-receipt.v1", "blackboard": {"records": records}}),
            encoding="utf-8",
        )
        return path

    def write_openclaw_done_state(
        self,
        openclaw: Path,
        artifact_id: str,
        title: str,
        *,
        runner_status: str | None = "done",
    ) -> None:
        self.write_artifact_record(openclaw, artifact_id, title)
        handoffs = openclaw / "workspace" / "agents" / "codex" / "handoffs" / "review_decisions"
        handoffs.mkdir(parents=True, exist_ok=True)
        (handoffs / f"{artifact_id}.json").write_text(
            json.dumps(
                {
                    "artifact_id": artifact_id,
                    "status": "done",
                    "decision": "approved",
                    "decision_label": "`acknowledged`",
                    "action": "continue_awk_workflow",
                    "owner": "main",
                    "title": title,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        if runner_status is not None:
            self.write_runner_receipt(openclaw, artifact_id, runner_status)

    def write_artifact_record(self, openclaw: Path, artifact_id: str, title: str) -> None:
        records = openclaw / "workspace-main" / "state" / "artifact_outbox" / "records"
        records.mkdir(parents=True, exist_ok=True)
        (records / f"{artifact_id}.json").write_text(
            json.dumps(
                {
                    "artifact_id": artifact_id,
                    "status": "approved",
                    "owner": "awk_openclaw",
                    "title": title,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def write_runner_receipt(self, openclaw: Path, artifact_id: str, status: str) -> None:
        receipts = (
            openclaw
            / "workspace-main"
            / "state"
            / "agent_review_runner"
            / "receipts"
            / "awk_openclaw"
        )
        receipts.mkdir(parents=True, exist_ok=True)
        (receipts / f"{artifact_id}-20260601T160000Z.json").write_text(
            json.dumps(
                {
                    "artifact_id": artifact_id,
                    "status": status,
                    "owner": "main",
                    "summary": "Verified migrated AWK lane handoff.",
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
