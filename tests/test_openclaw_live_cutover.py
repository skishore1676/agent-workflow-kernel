import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
IVY_FIXTURE = ROOT / "fixtures" / "openclaw" / "ivy_jonah" / "p3_approval_to_p5_shadow.json"
WEEKLY_FIXTURE = ROOT / "fixtures" / "openclaw" / "weekly_update" / "weekly_check_in_ready.json"


def load_script():
    path = ROOT / "scripts" / "openclaw_live_cutover.py"
    spec = importlib.util.spec_from_file_location("openclaw_live_cutover_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = load_script()


class OpenClawLiveCutoverTest(unittest.TestCase):
    def test_fixture_dry_run_writes_sandbox_artifacts_without_live_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "cutover"
            vault_root = root / "real-vault"

            with patch.object(script.subprocess, "run") as send:
                receipt = script.build_live_cutover(
                    ivy_fixture=IVY_FIXTURE,
                    weekly_fixture=WEEKLY_FIXTURE,
                    vault_root=vault_root,
                    obsidian_prefix="OpenClaw/Cutover",
                    telegram_target="owner",
                    telegram_account="oldmac",
                    output_dir=output_dir,
                )

            self.assertEqual(receipt["status"], "ready")
            send.assert_not_called()
            self.assertFalse(receipt["safety"]["allow_live_obsidian"])
            self.assertFalse(receipt["safety"]["allow_live_telegram"])
            self.assertIn("live_obsidian_or_northstar_write", receipt["safety"]["blocked_actions"])
            self.assertIn("telegram_send", receipt["safety"]["blocked_actions"])
            self.assertFalse(vault_root.exists())
            self.assertTrue((output_dir / "cutover_receipt.json").exists())
            self.assertTrue((output_dir / "cutover_receipt.md").exists())
            self.assertEqual(receipt["telegram"]["send_result"]["status"], "not_sent")
            self.assertTrue(Path(receipt["telegram"]["outbox_message_path"]).exists())
            self.assertEqual(
                {item["reviewer_human_ref"] for item in receipt["review"]["decisions"]},
                {"Suman(test automated reviewer)"},
            )

            notes = receipt["obsidian"]["notes"]
            self.assertEqual({note["lane_id"] for note in notes}, {"ivy", "weekly"})
            for note in notes:
                note_path = Path(note["note_path"])
                self.assertTrue(note_path.exists())
                self.assertIn(str(output_dir / "obsidian-sandbox"), str(note_path))
                self.assertTrue(note["readback_hash"].startswith("sha256:"))
                self.assertEqual(note["readback_hash"], note["content_hash"])

    def test_explicit_flags_write_temp_vault_notes_and_mock_telegram_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "cutover"
            vault_root = root / "sandbox-vault"
            completed = subprocess.CompletedProcess(
                args=["openclaw"],
                returncode=0,
                stdout='{"message_id":"message/ref/123"}\n',
                stderr="",
            )

            with patch.object(script.subprocess, "run", return_value=completed) as send:
                receipt = script.build_live_cutover(
                    ivy_fixture=IVY_FIXTURE,
                    weekly_fixture=WEEKLY_FIXTURE,
                    vault_root=vault_root,
                    obsidian_prefix="OpenClaw/Cutover",
                    telegram_target="owner-chat",
                    telegram_account="oldmac-account",
                    allow_live_obsidian=True,
                    allow_live_telegram=True,
                    output_dir=output_dir,
                    telegram_send_cmd="openclaw",
                )

            self.assertEqual(receipt["status"], "ready")
            send.assert_called_once()
            args = send.call_args.args[0]
            self.assertIn("--account", args)
            self.assertIn("oldmac-account", args)
            self.assertIn("--target", args)
            self.assertIn("owner-chat", args)
            self.assertIn("--message", args)
            message = args[args.index("--message") + 1]
            self.assertIn("OpenClaw AWK cutover review artifacts ready.", message)
            self.assertIn("mutation_permission_granted=False", message)

            self.assertTrue(receipt["safety"]["allow_live_obsidian"])
            self.assertTrue(receipt["safety"]["allow_live_telegram"])
            self.assertNotIn("live_obsidian_or_northstar_write", receipt["safety"]["blocked_actions"])
            self.assertNotIn("telegram_send", receipt["safety"]["blocked_actions"])
            self.assertEqual(receipt["telegram"]["send_result"]["status"], "sent")
            self.assertEqual(receipt["telegram"]["send_result"]["message_id"], "message/ref/123")
            for note in receipt["obsidian"]["notes"]:
                note_path = Path(note["note_path"])
                self.assertTrue(note_path.exists())
                self.assertTrue(note_path.resolve().is_relative_to(vault_root.resolve()))
                self.assertIn("OpenClaw/Cutover", str(note_path))

    def test_refuses_unsafe_obsidian_prefix_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaisesRegex(ValueError, "obsidian-prefix"):
                script.build_live_cutover(
                    ivy_fixture=IVY_FIXTURE,
                    weekly_fixture=WEEKLY_FIXTURE,
                    vault_root=root / "vault",
                    obsidian_prefix="../Northstar",
                    allow_live_obsidian=True,
                    output_dir=root / "cutover",
                )
            self.assertFalse((root / "vault").exists())

    def test_cli_accepts_packet_dir_and_writes_reviewable_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet_dir = root / "packet"
            script.build_onboarding_packet(
                ivy_fixture=IVY_FIXTURE,
                weekly_fixture=WEEKLY_FIXTURE,
                output_dir=packet_dir,
            )
            output_dir = root / "cutover"

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = script.main(
                    [
                        "--packet-dir",
                        str(packet_dir),
                        "--obsidian-prefix",
                        "OpenClaw/Cutover",
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue(json.loads(stdout.getvalue())["ok"])
            receipt = json.loads((output_dir / "cutover_receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["input"]["source_kind"], "packet_dir")
            self.assertEqual(receipt["status"], "ready")
            self.assertTrue((output_dir / "cutover_receipt.md").exists())
            self.assertTrue((output_dir / "input_packet" / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
