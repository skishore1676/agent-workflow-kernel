import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

from agent_workflow_kernel import WorkflowLedger, WorkflowStatus  # noqa: E402
from agent_workflow_kernel_openclaw import (  # noqa: E402
    plan_owned_completion_run,
    run_owned_completion_bridge,
    run_owned_completion_scheduler,
)


NOW = "2026-06-01T16:00:00+00:00"


class OpenClawOwnedCompletionBridgeTest(unittest.TestCase):
    def test_scheduler_plan_is_noop_and_reports_candidate_graph_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("weekly",))
            self.write_openclaw_done_state(openclaw, "awk-cutover-weekly-test", "Weekly")
            before = self.snapshot_tree(openclaw)
            ledger_path = root / "awk-ledger.sqlite3"

            summary = plan_owned_completion_run(
                ledger_path=ledger_path,
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["mode"], "plan")
            self.assertTrue(summary["dry_run"])
            self.assertTrue(summary["read_only"])
            self.assertFalse(summary["ledger_write_enabled"])
            self.assertEqual(summary["openclaw_write_count"], 0)
            self.assertFalse(ledger_path.exists())
            self.assertEqual(before, self.snapshot_tree(openclaw))

            result = summary["results"][0]
            self.assertEqual(result["planned_action"], "create_or_resume")
            self.assertEqual(result["predicted_stop_reason"], "would_reach_terminal")
            self.assertEqual(result["next"]["stage_id"], "capture_openclaw_surface_artifact")
            self.assertEqual(result["next"]["owner"], "awk_openclaw")
            self.assertIsNone(result["predicted_next"])

    def test_scheduler_run_mode_against_fixtures_writes_only_awk_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("ivy",))
            self.write_openclaw_done_state(openclaw, "awk-cutover-ivy-test", "Ivy/Jonah")
            before = self.snapshot_tree(openclaw)
            ledger_path = root / "awk-ledger.sqlite3"

            summary = run_owned_completion_scheduler(
                ledger_path=ledger_path,
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                run=True,
                now=NOW,
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["mode"], "run")
            self.assertFalse(summary["dry_run"])
            self.assertTrue(summary["read_only"])
            self.assertTrue(summary["ledger_write_enabled"])
            self.assertEqual(summary["openclaw_write_count"], 0)
            self.assertTrue(ledger_path.exists())
            self.assertEqual(before, self.snapshot_tree(openclaw))
            self.assertEqual(summary["results"][0]["status"], "done")

    def test_scheduler_plan_reports_already_terminal_without_duplicate_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("ivy",))
            self.write_openclaw_done_state(openclaw, "awk-cutover-ivy-test", "Ivy/Jonah")
            ledger_path = root / "awk-ledger.sqlite3"

            run_owned_completion_scheduler(
                ledger_path=ledger_path,
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                run=True,
                now=NOW,
            )
            plan = run_owned_completion_scheduler(
                ledger_path=ledger_path,
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                run=False,
                now=NOW,
            )

            result = plan["results"][0]
            self.assertEqual(result["planned_action"], "already_terminal")
            self.assertEqual(result["predicted_stop_reason"], "already_terminal")
            self.assertEqual(result["workflow_status"], "done")
            self.assertEqual(result["terminal_event_count"], 1)
            self.assertIsNone(result["predicted_next"])

    def test_cli_defaults_to_noop_plan_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("weekly",))
            self.write_openclaw_done_state(openclaw, "awk-cutover-weekly-test", "Weekly")
            ledger_path = root / "awk-ledger.sqlite3"

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/openclaw_owned_completion_bridge.py",
                    "--openclaw-root",
                    str(openclaw),
                    "--ledger",
                    str(ledger_path),
                    "--cutover-receipt",
                    str(receipt),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            summary = json.loads(completed.stdout)
            self.assertEqual(summary["mode"], "plan")
            self.assertFalse(ledger_path.exists())

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
            for result in summary["results"]:
                crosswalk = result["identity_crosswalk"]
                self.assertEqual(crosswalk["schema"], "openclaw.awk_identity_crosswalk.v1")
                self.assertEqual(crosswalk["awk_instance_id"], result["instance_id"])
                self.assertEqual(crosswalk["workflow_id"], "openclaw_migrated_lane_completion")
                self.assertEqual(crosswalk["workflow_version"], "0.1.0")
                self.assertEqual(crosswalk["openclaw_artifact_id"], result["artifact_id"])
                self.assertEqual(crosswalk["terminal_stage_id"], "verify_openclaw_review_runner")
                self.assertIsNotNone(crosswalk["terminal_event_id"])
                self.assertTrue(crosswalk["handoff_path"].endswith(f"{result['artifact_id']}.json"))
                self.assertTrue(crosswalk["runner_receipt_path"].endswith("20260601T160000Z.json"))
                self.assertEqual(crosswalk["work_id"], f"work-{result['artifact_id']}")
                self.assertEqual(crosswalk["work_item_id"], f"item-{result['artifact_id']}")
                self.assertEqual(crosswalk["work_ledger_handoff_id"], f"handoff-{result['artifact_id']}")
                self.assertEqual(crosswalk["work_ledger_receipt_id"], f"receipt-{result['artifact_id']}")
                self.assertTrue(crosswalk["source_hashes"]["openclaw_handoff"].startswith("sha256:"))
                self.assertEqual(result["identity_crosswalk_status"], "recorded")

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
                crosswalk_events = [
                    event
                    for event in ledger.list_events()
                    if event["event_type"] == "openclaw_identity_crosswalk_recorded"
                ]
                self.assertEqual(len(crosswalk_events), 2)
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
            self.assertEqual(third["results"][0]["identity_crosswalk_status"], "already_recorded")
            ledger = WorkflowLedger(root / "awk-ledger.sqlite3")
            try:
                event_counts = ledger.connection.execute(
                    """
                    SELECT event_type, COUNT(*) AS count
                    FROM events
                    WHERE event_type IN (?, ?)
                    GROUP BY event_type
                    """,
                    ("workflow_terminal", "openclaw_identity_crosswalk_recorded"),
                ).fetchall()
                self.assertEqual(
                    {row["event_type"]: row["count"] for row in event_counts},
                    {"workflow_terminal": 1, "openclaw_identity_crosswalk_recorded": 2},
                )
            finally:
                ledger.close()

    def test_terminal_rerun_rejects_changed_crosswalk_receipt_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("weekly",))
            artifact_id = "awk-cutover-weekly-test"
            self.write_openclaw_done_state(openclaw, artifact_id, "Weekly")

            first = run_owned_completion_bridge(
                ledger_path=root / "awk-ledger.sqlite3",
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )
            self.assertTrue(first["ok"])
            self.assertEqual(first["results"][0]["terminal_event_count"], 1)

            runner = (
                openclaw
                / "workspace-main"
                / "state"
                / "agent_review_runner"
                / "receipts"
                / "awk_openclaw"
                / f"{artifact_id}-20260601T160000Z.json"
            )
            runner_data = json.loads(runner.read_text(encoding="utf-8"))
            runner_data["work_ledger"]["receipt_id"] = "receipt-for-a-different-work-item"
            runner.write_text(json.dumps(runner_data), encoding="utf-8")

            second = run_owned_completion_bridge(
                ledger_path=root / "awk-ledger.sqlite3",
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )

            self.assertFalse(second["ok"])
            result = second["results"][0]
            self.assertEqual(result["status"], "identity_mismatch")
            self.assertEqual(result["stop_reason"], "openclaw_identity_crosswalk_mismatch")
            self.assertEqual(result["workflow_status"], "done")
            self.assertEqual(result["terminal_event_count"], 1)
            self.assertEqual(result["identity_crosswalk_status"], "rejected")
            self.assertEqual(result["identity_crosswalk_errors"][0]["field"], "work_ledger_receipt_id")

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

    def test_mismatched_artifact_record_id_rejects_crosswalk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("ivy",))
            self.write_openclaw_done_state(openclaw, "awk-cutover-ivy-test", "Ivy/Jonah")
            record = (
                openclaw
                / "workspace-main"
                / "state"
                / "artifact_outbox"
                / "records"
                / "awk-cutover-ivy-test.json"
            )
            record_data = json.loads(record.read_text(encoding="utf-8"))
            record_data["artifact_id"] = "different-artifact"
            record.write_text(json.dumps(record_data), encoding="utf-8")

            summary = run_owned_completion_bridge(
                ledger_path=root / "awk-ledger.sqlite3",
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )

            self.assertFalse(summary["ok"])
            result = summary["results"][0]
            self.assertEqual(result["status"], "identity_mismatch")
            self.assertEqual(result["stop_reason"], "openclaw_identity_crosswalk_mismatch")
            self.assertEqual(result["identity_crosswalk_status"], "rejected")
            self.assertEqual(result["identity_crosswalk_errors"][0]["source"], "openclaw_artifact_record")
            self.assertEqual(result["terminal_event_count"], 0)

    def test_mismatched_handoff_artifact_id_does_not_import_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("ivy",))
            self.write_openclaw_done_state(openclaw, "awk-cutover-ivy-test", "Ivy/Jonah")
            handoff = (
                openclaw
                / "workspace"
                / "agents"
                / "codex"
                / "handoffs"
                / "review_decisions"
                / "awk-cutover-ivy-test.json"
            )
            handoff_data = json.loads(handoff.read_text(encoding="utf-8"))
            handoff_data["artifact_id"] = "different-artifact"
            handoff.write_text(json.dumps(handoff_data), encoding="utf-8")

            summary = run_owned_completion_bridge(
                ledger_path=root / "awk-ledger.sqlite3",
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )

            self.assertFalse(summary["ok"])
            result = summary["results"][0]
            self.assertEqual(result["status"], "identity_mismatch")
            self.assertEqual(result["stop_reason"], "openclaw_identity_crosswalk_mismatch")
            self.assertEqual(result["identity_crosswalk_status"], "rejected")
            self.assertEqual(result["identity_crosswalk_errors"][0]["source"], "openclaw_handoff")
            self.assertFalse(result["acknowledged"])
            self.assertEqual(result["terminal_event_count"], 0)

    def test_mismatched_runner_receipt_artifact_id_keeps_verify_stage_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            receipt = self.write_cutover_receipt(root, ("weekly",))
            self.write_openclaw_done_state(openclaw, "awk-cutover-weekly-test", "Weekly")
            runner = (
                openclaw
                / "workspace-main"
                / "state"
                / "agent_review_runner"
                / "receipts"
                / "awk_openclaw"
                / "awk-cutover-weekly-test-20260601T160000Z.json"
            )
            runner_data = json.loads(runner.read_text(encoding="utf-8"))
            runner_data["artifact_id"] = "different-artifact"
            runner.write_text(json.dumps(runner_data), encoding="utf-8")

            summary = run_owned_completion_bridge(
                ledger_path=root / "awk-ledger.sqlite3",
                openclaw_root=openclaw,
                cutover_receipt_path=receipt,
                now=NOW,
            )

            self.assertFalse(summary["ok"])
            result = summary["results"][0]
            self.assertTrue(result["acknowledged"])
            self.assertFalse(result["runner_done"])
            self.assertEqual(result["status"], "identity_mismatch")
            self.assertEqual(result["stop_reason"], "openclaw_identity_crosswalk_mismatch")
            self.assertEqual(result["identity_crosswalk_status"], "rejected")
            self.assertEqual(result["identity_crosswalk_errors"][0]["source"], "openclaw_runner_receipt")
            self.assertEqual(result["terminal_event_count"], 0)

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
                    "work_ledger": {
                        "handoff_id": f"handoff-{artifact_id}",
                        "work_id": f"work-{artifact_id}",
                        "work_item_id": f"item-{artifact_id}",
                    },
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
                    "work_ledger": {
                        "work_id": f"work-{artifact_id}",
                        "work_item_id": f"item-{artifact_id}",
                    },
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
                    "work_ledger": {
                        "receipt_id": f"receipt-{artifact_id}",
                        "work_id": f"work-{artifact_id}",
                        "work_item_id": f"item-{artifact_id}",
                    },
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def snapshot_tree(self, root: Path) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        if not root.exists():
            return snapshot
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
            snapshot[str(path.relative_to(root))] = path.read_bytes().hex()
        return snapshot


if __name__ == "__main__":
    unittest.main()
