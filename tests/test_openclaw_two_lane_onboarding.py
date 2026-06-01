import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "openclaw_two_lane_onboarding.py"
IVY_FIXTURE = ROOT / "fixtures" / "openclaw" / "ivy_jonah" / "p3_approval_to_p5_shadow.json"
IVY_PUBLISH_FIXTURE = ROOT / "fixtures" / "openclaw" / "ivy_jonah" / "p5_publish_decision_packet.json"
WEEKLY_READY_FIXTURE = ROOT / "fixtures" / "openclaw" / "weekly_update" / "weekly_check_in_ready.json"
WEEKLY_CLEARED_FIXTURE = ROOT / "fixtures" / "openclaw" / "weekly_update" / "weekly_check_in_cleared.json"


class OpenClawTwoLaneOnboardingTest(unittest.TestCase):
    def run_packet(
        self,
        weekly_fixture: Path,
        output_dir: Path,
        *,
        ivy_fixture: Path = IVY_FIXTURE,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--ivy-fixture",
                str(ivy_fixture),
                "--weekly-fixture",
                str(weekly_fixture),
                "--output-dir",
                str(output_dir),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_packet_contains_both_lanes_shadow_reports_and_local_review_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "packet"

            result = self.run_packet(WEEKLY_READY_FIXTURE, output_dir)

            self.assertEqual(result.stderr, "")
            cli_payload = json.loads(result.stdout)
            self.assertTrue(cli_payload["ok"])

            summary_path = output_dir / "summary.json"
            readme_path = output_dir / "README.md"
            self.assertTrue(summary_path.exists())
            self.assertTrue(readme_path.exists())
            self.assertTrue((output_dir / "lanes" / "ivy" / "shadow_report.json").exists())
            self.assertTrue((output_dir / "lanes" / "weekly" / "shadow_report.json").exists())

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(set(summary["lanes"]), {"ivy", "weekly"})
            self.assertEqual(summary["lanes"]["ivy"]["workflow_id"], "ivy_jonah_editorial")
            self.assertEqual(summary["lanes"]["weekly"]["workflow_id"], "jarvis_weekly_update_shadow")
            self.assertEqual(summary["overall_readiness"]["classification"], "human_review_required")

            ivy = summary["lanes"]["ivy"]
            weekly = summary["lanes"]["weekly"]
            self.assertTrue(ivy["public_publish_blocked"])
            self.assertIn("public_publish", {item["action"] for item in ivy["blocked_external_actions"]})
            self.assertEqual(
                ivy["readiness"]["classification"],
                "shadow_ready_human_gate_required",
            )
            self.assertEqual(
                weekly["readiness"]["classification"],
                "waiting_on_human_read_clear",
            )
            self.assertFalse(weekly["read_clear_is_mutation_permission"])
            self.assertFalse(weekly["mutation_permission_granted"])

            human_gates = ivy["human_gates"] + weekly["human_gates"]
            self.assertGreaterEqual(len(human_gates), 3)
            self.assertTrue(all(gate["requires_explicit_approval"] for gate in human_gates))
            review_notes = ivy["review_notes"] + weekly["review_notes"]
            self.assertEqual(len(review_notes), len(human_gates))
            for note in review_notes:
                note_path = Path(note["note_path"])
                self.assertTrue(note_path.exists())
                note_text = note_path.read_text(encoding="utf-8")
                self.assertIn("TEST ONLY - NON-LIVE LOCAL REVIEW PACKET", note_text)
                self.assertIn("Action fingerprint:", note_text)
                self.assertTrue(note["requires_explicit_approval"])
                self.assertTrue(note["non_live"])
                self.assertTrue(note["readback"]["exists"])
                self.assertEqual(note["readback"]["note_path"], note["note_path"])
                self.assertTrue(note["readback"]["action_fingerprint_matches"])
                self.assertTrue(note["validation"]["valid"])

            manifest = json.loads((output_dir / "evidence_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["inputs_read"]), 2)
            self.assertIn("readiness_deltas", manifest)
            self.assertEqual(
                manifest["next_owned_execution_gate"]["gate"],
                "human_review_decision_readback",
            )
            self.assertTrue((output_dir / "lanes" / "ivy" / "lane_report.json").exists())
            self.assertTrue((output_dir / "lanes" / "weekly" / "lane_report.json").exists())

    def test_weekly_read_clear_fixture_does_not_grant_mutation_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "packet"

            self.run_packet(WEEKLY_CLEARED_FIXTURE, output_dir)

            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            weekly = summary["lanes"]["weekly"]

            self.assertEqual(
                weekly["readiness"]["classification"],
                "read_clear_shadow_complete_no_mutation",
            )
            self.assertTrue(weekly["observed_read_clear"])
            self.assertFalse(weekly["read_clear_is_mutation_permission"])
            self.assertFalse(weekly["blackboard_or_obsidian_write_allowed"])
            self.assertFalse(weekly["mutation_permission_granted"])
            self.assertEqual(weekly["human_gates"], [])
            self.assertEqual(weekly["review_notes"], [])
            self.assertIn(
                "blackboard_or_obsidian_write",
                {item["action"] for item in weekly["blocked_external_actions"]},
            )

            readme = (output_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("No live writes", readme)
            self.assertIn("Read clear is mutation permission: `False`", readme)

    def test_repeated_live_readonly_fixture_packet_is_stable_and_classified(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            ivy_fixture = self.fixture_with_source_mode(IVY_FIXTURE, tmp / "ivy_live.json")
            weekly_fixture = self.fixture_with_source_mode(WEEKLY_READY_FIXTURE, tmp / "weekly_live.json")
            output_dir = tmp / "packet"

            self.run_packet(weekly_fixture, output_dir, ivy_fixture=ivy_fixture)
            first_summary = (output_dir / "summary.json").read_text(encoding="utf-8")
            first_readme = (output_dir / "README.md").read_text(encoding="utf-8")
            self.run_packet(weekly_fixture, output_dir, ivy_fixture=ivy_fixture)
            second_summary = (output_dir / "summary.json").read_text(encoding="utf-8")
            second_readme = (output_dir / "README.md").read_text(encoding="utf-8")

            self.assertEqual(first_summary, second_summary)
            self.assertEqual(first_readme, second_readme)

            summary = json.loads(second_summary)
            inputs = summary["dual_run_evidence"]["inputs_read"]
            self.assertEqual({item["classification"] for item in inputs}, {"live_readonly_fixture"})
            self.assertTrue(all(item["live_readonly"] for item in inputs))
            self.assertTrue(all(item["runtime_contacted"] is False for item in inputs))
            self.assertFalse(summary["overall_readiness"]["mutation_permission_granted"])

    def test_ivy_publish_packet_artifacts_do_not_grant_mutation_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "packet"

            self.run_packet(
                WEEKLY_CLEARED_FIXTURE,
                output_dir,
                ivy_fixture=IVY_PUBLISH_FIXTURE,
            )

            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            ivy = summary["lanes"]["ivy"]

            self.assertTrue(ivy["historical_publish_artifacts"]["observed"])
            self.assertGreaterEqual(ivy["historical_publish_artifacts"]["count"], 1)
            self.assertFalse(
                ivy["historical_publish_artifacts"]["historical_publish_artifacts_are_mutation_permission"]
            )
            self.assertFalse(
                ivy["safety_boundaries"]["historical_publish_artifacts_are_mutation_permission"]
            )
            self.assertFalse(ivy["mutation_permission_granted"])
            self.assertFalse(ivy["next_owned_execution_gate"]["owned_execution_allowed_after_gate"])

    def fixture_with_source_mode(self, source: Path, target: Path) -> Path:
        data = json.loads(source.read_text(encoding="utf-8"))
        data["source_mode"] = "live_readonly"
        target.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return target


if __name__ == "__main__":
    unittest.main()
