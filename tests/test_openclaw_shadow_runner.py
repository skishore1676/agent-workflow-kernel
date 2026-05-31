import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "openclaw_shadow_run.py"
FIXTURE = ROOT / "fixtures" / "openclaw" / "shadow_runner" / "generic_readonly_fixture.json"
IVY_FIXTURE = ROOT / "fixtures" / "openclaw" / "ivy_jonah" / "p3_approval_to_p5_shadow.json"


class OpenClawShadowRunnerTest(unittest.TestCase):
    def run_shadow(self, fixture: Path, report: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--fixture", str(fixture), "--report", report],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_stdout_report_output_for_generic_fixture(self) -> None:
        result = self.run_shadow(FIXTURE, "-")

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "workflow.kernel.openclaw-shadow-report.v1")
        self.assertEqual(payload["fixture_identity"]["fixture_id"], "openclaw-shadow-generic-001")
        self.assertEqual(payload["lane"], "generic")
        self.assertEqual(payload["adoption"]["status"], "shadow_ready")
        self.assertEqual(payload["adoption"]["parity_status"], "equivalent")
        self.assertEqual(payload["mapping_summary"]["work_ledger"]["work_item_id"], "work-shadow-001")
        self.assertEqual(len(payload["receipts_generated"]), 1)
        self.assertEqual(payload["receipts_generated"][0]["status"], "succeeded")
        self.assertEqual(result.stderr, "")

    def test_file_report_output_matches_stdout_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "shadow-report.json"

            result = self.run_shadow(FIXTURE, str(report_path))

            self.assertEqual(result.stdout, "")
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["read_only_adapter_result"]["status"], "succeeded")
            self.assertEqual(payload["parity_report"]["summary"]["different"], 0)
            self.assertIn(
                {
                    "action": "telegram_send",
                    "reason": "shadow report must not send operator notifications",
                },
                payload["blocked_external_actions"],
            )

    def test_unsupported_lane_reports_adoption_boundary_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = Path(tmpdir) / "unsupported.json"
            fixture_path.write_text(
                json.dumps(
                    {
                        "fixture_id": "experimental-fixture",
                        "created_at": "2026-05-31T00:00:00Z",
                        "lane": "experimental-lane",
                        "mapping": {
                            "lane_id": "experimental-lane",
                            "agent_id": "experimental_agent",
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_shadow(fixture_path, "-")

            payload = json.loads(result.stdout)
            self.assertEqual(payload["adoption"]["status"], "unsupported_lane")
            self.assertEqual(payload["adoption"]["read_only_status"], "succeeded")
            self.assertEqual(
                payload["next_recommended_adoption_step"],
                "Create a lane-specific adapter or classify this lane before any takeover decision.",
            )

    def test_ivy_payload_runs_lane_adapter_when_available(self) -> None:
        result = self.run_shadow(IVY_FIXTURE, "-")
        report = json.loads(result.stdout)

        self.assertEqual(report["adoption"]["status"], "shadow_ready")
        self.assertEqual(report["lane_adapter"]["module"], "agent_workflow_kernel_openclaw.ivy_lane")
        self.assertEqual(report["lane_adapter"]["status"], "available")
        self.assertEqual(report["lane_adoption"]["status"], "shadow_ready")
        self.assertTrue(report["lane_adoption"]["report"]["ready_for_shadow"])
        self.assertTrue(report["lane_adoption"]["report"]["public_publish_blocked"])
        self.assertGreater(report["lane_adoption"]["receipt_count"], 1)
        self.assertIn(
            "public_publish",
            {item["action"] for item in report["blocked_external_actions"]},
        )

    def test_weekly_payload_reports_missing_lane_adapter_until_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = Path(tmpdir) / "weekly.json"
            fixture_path.write_text(
                json.dumps(
                    {
                        "fixture_id": "weekly-fixture",
                        "created_at": "2026-05-31T00:00:00Z",
                        "lane": "weekly",
                        "mapping": {
                            "lane_id": "weekly",
                            "agent_id": "weekly_agent",
                        },
                        "weekly_update": {"note_path": "redacted-weekly-note.md", "checked": False},
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_shadow(fixture_path, "-")
            report = json.loads(result.stdout)

            self.assertEqual(report["adoption"]["status"], "adapter_missing")
            self.assertEqual(report["lane_adapter"]["module"], "agent_workflow_kernel_openclaw.weekly_update")
            self.assertEqual(report["lane_adapter"]["status"], "adapter_missing")
            self.assertIn(
                "blackboard_or_obsidian_write",
                {item["action"] for item in report["blocked_external_actions"]},
            )

    def test_output_is_deterministic_for_repeated_runs(self) -> None:
        first = self.run_shadow(FIXTURE, "-").stdout
        second = self.run_shadow(FIXTURE, "-").stdout

        self.assertEqual(first, second)
        self.assertEqual(first.count("\n"), 1)
        self.assertEqual(json.loads(first)["parity_report"]["fields"]["different"], [])


if __name__ == "__main__":
    unittest.main()
