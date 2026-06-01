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


def write_blackboard_refresh_stub(openclaw_root: Path) -> None:
    refresh = openclaw_root / "workspace-main" / "scripts" / "update_review_inbox.py"
    refresh.parent.mkdir(parents=True)
    refresh.write_text(
        "\n".join(
            [
                "import json, os, re",
                "from pathlib import Path",
                "root = Path(__file__).resolve().parents[1]",
                "vault = Path(os.environ['OPENCLAW_OBSIDIAN_VAULT'])",
                "records = root / 'state' / 'artifact_outbox' / 'records'",
                "def safe_id(value):",
                "    return re.sub(r'[^A-Za-z0-9_.-]+', '-', value.strip()).strip('-').lower() or 'item'",
                "lines = ['# Blackboard', '', '## Decide', '']",
                "for path in sorted(records.glob('*.json')):",
                "    data = json.loads(path.read_text())",
                "    note = Path(data.get('review_note') or data.get('draft_path') or str(path))",
                "    try: note_text = note.relative_to(vault).as_posix()",
                "    except ValueError: note_text = str(note)",
                "    item = 'artifact-' + safe_id(data.get('artifact_id') or path.stem)",
                "    lines.append(f\"- [ ] {data.get('title')} <!-- inbox-item:{item} -->\")",
                "    lines.append(f\"  - executive summary: {data.get('why')}\")",
                "    lines.append(f\"  - decision / next action: {data.get('next')}\")",
                "    lines.append(f\"  - owner: {data.get('owner')}\")",
                "    lines.append(f\"  - evidence: [{note_text}](<{note_text}>)\")",
                "vault.mkdir(parents=True, exist_ok=True)",
                "(vault / '01 Blackboard.md').write_text('\\n'.join(lines) + '\\n')",
                "print('review-inbox validation: OK')",
            ]
        ),
        encoding="utf-8",
    )


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
            self.assertTrue(receipt["telegram"]["upstream_obsidian_trusted"])
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
                note_text = note_path.read_text(encoding="utf-8")
                self.assertIn("## Artifact To Review", note_text)
                self.assertIn("### Evidence Paths", note_text)
                self.assertTrue(note["artifact_review_embedded"])
                self.assertTrue(Path(note["source_artifact_path"]).exists())
                if note["lane_id"] == "ivy":
                    self.assertIn("### Ivy/Jonah Boundary", note_text)
                    self.assertIn("Public publish blocked", note_text)
                if note["lane_id"] == "weekly":
                    self.assertIn("### Jarvis Weekly Boundary", note_text)
                    self.assertIn("Read clear is mutation permission", note_text)

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
            self.assertTrue(receipt["telegram"]["upstream_obsidian_trusted"])
            receipt_md = (output_dir / "cutover_receipt.md").read_text(encoding="utf-8")
            self.assertIn("Send ref: `receipt:cutover:telegram:live-pointer:succeeded`", receipt_md)
            for note in receipt["obsidian"]["notes"]:
                note_path = Path(note["note_path"])
                self.assertTrue(note_path.exists())
                self.assertTrue(note_path.resolve().is_relative_to(vault_root.resolve()))
                self.assertIn("OpenClaw/Cutover", str(note_path))
                self.assertIn("## Artifact To Review", note_path.read_text(encoding="utf-8"))
                self.assertTrue(note["trusted"])

    def test_openclaw_root_publishes_blackboard_records_and_readback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "cutover"
            vault_root = root / "sandbox-vault"
            openclaw_root = root / "openclaw"
            write_blackboard_refresh_stub(openclaw_root)

            receipt = script.build_live_cutover(
                ivy_fixture=IVY_FIXTURE,
                weekly_fixture=WEEKLY_FIXTURE,
                vault_root=vault_root,
                obsidian_prefix="OpenClaw/Cutover",
                telegram_target="owner-chat",
                telegram_account="oldmac-account",
                allow_live_obsidian=True,
                output_dir=output_dir,
                openclaw_root=openclaw_root,
            )

            self.assertEqual(receipt["status"], "ready")
            self.assertTrue(receipt["blackboard"]["enabled"])
            self.assertEqual(receipt["blackboard"]["status"], "succeeded")
            self.assertEqual({record["lane_id"] for record in receipt["blackboard"]["records"]}, {"ivy", "weekly"})
            self.assertTrue((vault_root / "01 Blackboard.md").exists())
            blackboard = (vault_root / "01 Blackboard.md").read_text(encoding="utf-8")
            for record in receipt["blackboard"]["records"]:
                self.assertTrue(record["readback_found"])
                self.assertTrue(Path(record["record_path"]).exists())
                record_json = json.loads(Path(record["record_path"]).read_text(encoding="utf-8"))
                self.assertTrue(Path(record_json["source_artifact_path"]).exists())
                self.assertTrue(Path(record_json["summary_path"]).exists())
                self.assertIn(record["blackboard_item_id"], blackboard)
            self.assertIn("Blackboard", receipt["telegram"]["pointer"])
            receipt_md = (output_dir / "cutover_receipt.md").read_text(encoding="utf-8")
            self.assertIn("## Blackboard", receipt_md)

    def test_blackboard_cutover_ready_is_surface_evidence_not_terminal_workflow_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "cutover"
            vault_root = root / "sandbox-vault"
            openclaw_root = root / "openclaw"
            write_blackboard_refresh_stub(openclaw_root)

            receipt = script.build_live_cutover(
                ivy_fixture=IVY_FIXTURE,
                weekly_fixture=WEEKLY_FIXTURE,
                vault_root=vault_root,
                obsidian_prefix="OpenClaw/Cutover",
                allow_live_obsidian=True,
                output_dir=output_dir,
                openclaw_root=openclaw_root,
            )

            self.assertEqual(receipt["status"], "ready")
            self.assertEqual(receipt["blackboard"]["status"], "succeeded")
            self.assertNotIn("workflow_instance_id", receipt)
            self.assertNotIn("terminal_status", receipt)
            self.assertNotIn("workflow_terminal", json.dumps(receipt, sort_keys=True))
            for record in receipt["blackboard"]["records"]:
                self.assertEqual(record["status"], "succeeded")
                self.assertTrue(record["readback_found"])
                self.assertTrue(record["receipt_id"].startswith("receipt:cutover:blackboard:"))

    def test_live_telegram_send_is_blocked_when_obsidian_receipts_are_untrusted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "cutover"
            vault_root = root / "sandbox-vault"
            conflicting_note = vault_root / "OpenClaw" / "Cutover" / "ivy" / "cutover-review.md"
            conflicting_note.parent.mkdir(parents=True)
            conflicting_note.write_text(
                "\n".join(
                    [
                        "---",
                        "gate_id: cutover:ivy",
                        "---",
                        "",
                        "# Old cutover review",
                        "",
                        "- Action fingerprint: `sha256:old-action`",
                    ]
                ),
                encoding="utf-8",
            )
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

            send.assert_not_called()
            self.assertEqual(receipt["status"], "blocked")
            ivy_note = next(note for note in receipt["obsidian"]["notes"] if note["lane_id"] == "ivy")
            self.assertEqual(ivy_note["status"], "blocked")
            self.assertEqual(ivy_note["error_class"], "idempotency_conflict")
            self.assertFalse(ivy_note["trusted"])
            self.assertFalse(receipt["telegram"]["upstream_obsidian_trusted"])
            self.assertEqual(receipt["telegram"]["send_result"]["status"], "blocked")
            self.assertEqual(receipt["telegram"]["send_result"]["reason"], "obsidian_receipts_not_trusted")
            self.assertFalse(receipt["telegram"]["send_result"]["performed"])
            self.assertEqual(
                receipt["telegram"]["send_result"]["upstream_blockers"][0]["reason"],
                "publish_failed",
            )
            self.assertIn("artifacts are blocked", receipt["telegram"]["pointer"])

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
