import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "openclaw_ivy_jonah_owned_runner.py"
    spec = importlib.util.spec_from_file_location("openclaw_ivy_jonah_owned_runner_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = load_script()


class OpenClawIvyJonahOwnedRunnerTest(unittest.TestCase):
    def test_run_mode_wraps_legacy_handoff_and_passes_publish_packet_fields_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            write_fake_openclaw_scripts(openclaw, action="prepared_or_research_publish_packet")

            summary = script.run_owned_ivy_jonah(
                openclaw_root=openclaw,
                ledger_path=root / "awk.sqlite3",
                stale_minutes=15,
                instance_id="ivy-owned-test",
                now="2026-06-01T12:00:00Z",
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["workflow_status"], "done")
            self.assertEqual(summary["action"], "prepared_or_research_publish_packet")
            self.assertEqual(summary["compatibility_action"], "handled")
            self.assertEqual(summary["project_id"], "nvidia-test")
            self.assertEqual(summary["publish_ready_path"], "publish.md")
            self.assertEqual(summary["browser_plan_path"], "browser.md")
            self.assertEqual(summary["publish_staging_path"], "staging.md")
            self.assertEqual(summary["runner_result"]["action"], "prepared_or_research_publish_packet")
            self.assertEqual([row["stage_id"] for row in summary["stage_runs"]], [
                "audit_editorial_path",
                "run_review_handoff",
                "refresh_blackboard",
            ])
            self.assertTrue((openclaw / "blackboard-refreshed.txt").exists())
            self.assertTrue(all(receipt["legacy_compatibility_adapter"] for receipt in summary["receipts"]))

    def test_noop_handoff_reaches_done_without_refreshing_blackboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            write_fake_openclaw_scripts(openclaw, action="noop")

            summary = script.run_owned_ivy_jonah(
                openclaw_root=openclaw,
                ledger_path=root / "awk.sqlite3",
                instance_id="ivy-owned-noop",
                now="2026-06-01T12:00:00Z",
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["action"], "noop")
            self.assertEqual(summary["compatibility_action"], "noop")
            self.assertEqual([row["stage_id"] for row in summary["stage_runs"]], [
                "audit_editorial_path",
                "run_review_handoff",
            ])
            self.assertFalse((openclaw / "blackboard-refreshed.txt").exists())

    def test_default_instance_id_is_invocation_scoped_not_daily(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            write_fake_openclaw_scripts(openclaw, action="noop")
            ledger = root / "awk.sqlite3"

            first = script.run_owned_ivy_jonah(
                openclaw_root=openclaw,
                ledger_path=ledger,
                now="2026-06-01T12:00:00Z",
            )
            second = script.run_owned_ivy_jonah(
                openclaw_root=openclaw,
                ledger_path=ledger,
                now="2026-06-01T12:00:00Z",
            )

            self.assertNotEqual(first["instance_id"], second["instance_id"])
            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])

    def test_cli_dry_run_emits_summary_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            write_fake_openclaw_scripts(openclaw, action="prepared_or_research_publish_packet")
            summary_path = root / "summary.json"

            with redirect_stdout(io.StringIO()):
                exit_code = script.main(
                    [
                        "--openclaw-root",
                        str(openclaw),
                        "--ledger",
                        str(root / "awk.sqlite3"),
                        "--dry-run",
                        "--summary-json",
                        str(summary_path),
                        "--instance-id",
                        "ivy-owned-dry-run",
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["mode"], "dry_run")
            self.assertEqual(summary["action"], "noop")
            self.assertFalse((openclaw / "blackboard-refreshed.txt").exists())


def write_fake_openclaw_scripts(openclaw: Path, *, action: str) -> None:
    cli = openclaw / "scripts" / "lib" / "work_ledger" / "cli.py"
    refresh = openclaw / "workspace-main" / "scripts" / "surfaces" / "update_review_inbox.py"
    cli.parent.mkdir(parents=True, exist_ok=True)
    refresh.parent.mkdir(parents=True, exist_ok=True)
    (openclaw / "workspace" / "agents" / "or_research" / "handoffs" / "review_decisions").mkdir(
        parents=True,
        exist_ok=True,
    )
    (openclaw / "workspace" / "agents" / "or_research").mkdir(parents=True, exist_ok=True)
    cli.write_text(
        "\n".join(
            [
                "import json, sys",
                "cmd = sys.argv[1]",
                "if cmd == 'audit-editorial-path':",
                "    print(json.dumps({'ok': True, 'action': 'audited'}))",
                "elif cmd == 'run-next-or-review-handoff':",
                f"    action = {action!r}",
                "    payload = {'ok': True, 'action': action}",
                "    if action != 'noop':",
                "        payload.update({",
                "            'project_id': 'nvidia-test',",
                "            'handoff_path': 'handoff.json',",
                "            'operator_summary_path': 'summary.md',",
                "            'obsidian_path': 'review.md',",
                "            'browser_plan_path': 'browser.md',",
                "            'publish_ready_path': 'publish.md',",
                "            'publish_staging_path': 'staging.md',",
                "        })",
                "    print(json.dumps(payload))",
                "else:",
                "    print(json.dumps({'ok': False, 'action': 'unknown'}))",
                "    raise SystemExit(1)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    refresh.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "root = Path(__file__).resolve().parents[3]",
                "(root / 'blackboard-refreshed.txt').write_text('ok\\n')",
                "print('review-inbox validation: OK')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
