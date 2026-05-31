import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "packages" / "kernel"


class CLILocalExecutionTest(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PACKAGE_PATH)
        return subprocess.run(
            [sys.executable, "-m", "agent_workflow_kernel.cli", *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_validate_reports_workflow_summary(self) -> None:
        result = self.run_cli("validate", "workflows/bumblebee_quality_review.yaml")

        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["workflow_id"], "bumblebee_quality_review")
        self.assertEqual(payload["stages"], 5)
        self.assertEqual(payload["transitions"], 13)

    def test_compile_prints_canonical_json(self) -> None:
        result = self.run_cli("compile", "workflows/bumblebee_quality_review.yaml")

        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["id"], "bumblebee_quality_review")
        self.assertEqual(payload["stages"][0]["id"], "build_review_contract")

    def test_run_local_executes_bumblebee_to_terminal_and_writes_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "local.sqlite3"

            result = self.run_cli(
                "run-local",
                "workflows/bumblebee_quality_review.yaml",
                "--ledger",
                str(ledger_path),
            )

            summary = json.loads(result.stdout)
            self.assertEqual(summary["workflow_id"], "bumblebee_quality_review")
            self.assertEqual(summary["status"], "done")
            self.assertEqual(summary["stop_reason"], "terminal")
            self.assertEqual(summary["terminal"], "done")
            self.assertEqual(summary["stages_run"], 4)
            self.assertEqual(summary["ledger_path"], str(ledger_path))

            conn = sqlite3.connect(ledger_path)
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM workflow_instances").fetchone()[0],
                    1,
                )
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 4)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM adapter_invocations").fetchone()[0],
                    4,
                )
                self.assertGreaterEqual(
                    conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    12,
                )
                workflow_status = conn.execute(
                    "SELECT status FROM workflow_instances"
                ).fetchone()[0]
                self.assertEqual(workflow_status, "done")
            finally:
                conn.close()

    def test_run_local_stops_at_human_gate_without_advancing_to_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "local.sqlite3"

            result = self.run_cli(
                "run-local",
                "workflows/deterministic_system_action.yaml",
                "--ledger",
                str(ledger_path),
            )

            summary = json.loads(result.stdout)
            self.assertEqual(summary["workflow_id"], "deterministic_system_action")
            self.assertEqual(summary["status"], "waiting_on_human")
            self.assertEqual(summary["stop_reason"], "human_gate")
            self.assertEqual(summary["current_stage_id"], "approval")

            conn = sqlite3.connect(ledger_path)
            try:
                stage_rows = conn.execute(
                    """
                    SELECT stage_id, status, approval_required
                    FROM stage_runs
                    ORDER BY stage_id
                    """
                ).fetchall()
                self.assertIn(("approval", "blocked", 1), stage_rows)
                self.assertNotIn("apply_action", {row[0] for row in stage_rows})
                workflow_row = conn.execute(
                    "SELECT status, current_stage_id FROM workflow_instances"
                ).fetchone()
                self.assertEqual(tuple(workflow_row), ("waiting_on_human", "approval"))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
