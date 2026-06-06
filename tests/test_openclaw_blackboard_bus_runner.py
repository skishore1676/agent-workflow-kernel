import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "openclaw_blackboard_bus_runner.py"
    spec = importlib.util.spec_from_file_location("openclaw_blackboard_bus_runner_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = load_script()


class OpenClawBlackboardBusRunnerTest(unittest.TestCase):
    def test_decision_ingest_dry_run_plans_without_direct_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            scaffold_openclaw(openclaw)
            summary_path = root / "summary.json"
            ledger_path = root / "awk.sqlite3"

            with redirect_stdout(io.StringIO()):
                exit_code = script.main(
                    [
                        "decision-ingest",
                        "--openclaw-root",
                        str(openclaw),
                        "--summary-json",
                        str(summary_path),
                        "--ledger",
                        str(ledger_path),
                        "--dry-run",
                        "--instance-id",
                        "blackboard-bus-dry-run",
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["ok"])
            self.assertEqual(summary["lane"], "blackboard_decision_ingest")
            self.assertEqual(summary["mode"], "dry_run")
            self.assertEqual(summary["action"], "planned")
            self.assertEqual([stage["stage_id"] for stage in summary["stage_runs"]], [
                "ingest_decisions_dry_run",
                "plan_review_runner",
            ])
            self.assertEqual([receipt["stage_run_id"] for receipt in summary["receipts"]], [
                "blackboard-bus-dry-run:ingest_decisions_dry_run",
                "blackboard-bus-dry-run:plan_review_runner",
            ])
            with sqlite3.connect(ledger_path) as conn:
                conn.row_factory = sqlite3.Row
                instance = conn.execute("SELECT status FROM workflow_instances WHERE instance_id = ?", ("blackboard-bus-dry-run",)).fetchone()
                stages = conn.execute("SELECT stage_id, status FROM stage_runs ORDER BY created_at").fetchall()
            self.assertEqual(instance["status"], "done")
            self.assertEqual([(row["stage_id"], row["status"]) for row in stages], [
                ("ingest_decisions_dry_run", "succeeded"),
                ("plan_review_runner", "succeeded"),
            ])

    def test_publisher_mode_wraps_attention_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            scaffold_openclaw(openclaw, publisher_payload={"ok": True, "published": True, "review_note": "note.md"})
            summary_path = root / "summary.json"

            with redirect_stdout(io.StringIO()):
                exit_code = script.main(
                    [
                        "publisher",
                        "--openclaw-root",
                        str(openclaw),
                        "--summary-json",
                        str(summary_path),
                        "--instance-id",
                        "blackboard-publisher-test",
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["ok"])
            self.assertEqual(summary["lane"], "blackboard_publisher")
            self.assertEqual(summary["action"], "published_review_note")
            self.assertEqual(summary["surface_ref"], "note.md")

    def test_publisher_dry_run_does_not_call_attention_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            scaffold_openclaw(openclaw, publisher_payload={"ok": False, "published": False})
            summary_path = root / "summary.json"

            with redirect_stdout(io.StringIO()):
                exit_code = script.main(
                    [
                        "publisher",
                        "--openclaw-root",
                        str(openclaw),
                        "--summary-json",
                        str(summary_path),
                        "--dry-run",
                        "--instance-id",
                        "blackboard-publisher-dry-run",
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["ok"])
            self.assertEqual(summary["mode"], "dry_run")
            self.assertEqual(summary["action"], "noop")
            self.assertEqual(summary["stage_runs"][0]["stage_id"], "publish_attention_dry_run")

    def test_live_decision_loop_requires_explicit_allow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            scaffold_openclaw(openclaw)

            with redirect_stdout(io.StringIO()):
                exit_code = script.main(
                    [
                        "decision-ingest",
                        "--openclaw-root",
                        str(openclaw),
                        "--instance-id",
                        "blackboard-bus-blocked",
                    ]
                )

            self.assertEqual(exit_code, 1)

    def test_decision_loop_uses_final_legacy_json_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            scaffold_openclaw(
                openclaw,
                direct_loop_body=(
                    "#!/usr/bin/env bash\n"
                    "printf '{\"ok\": true, \"ingested_count\": 1}\\n'\n"
                    "printf '{\"ok\": true, \"action\": \"handled_or_research_review_handoff\"}\\n'\n"
                ),
            )
            summary_path = root / "summary.json"

            with redirect_stdout(io.StringIO()):
                exit_code = script.main(
                    [
                        "decision-ingest",
                        "--openclaw-root",
                        str(openclaw),
                        "--allow-agent-dispatch",
                        "--summary-json",
                        str(summary_path),
                        "--instance-id",
                        "blackboard-bus-final-json",
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["action"], "handled_or_research_review_handoff")

    def test_completed_jarvis_review_runner_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            scaffold_openclaw(
                openclaw,
                direct_loop_body=(
                    "#!/usr/bin/env bash\n"
                    "printf '{\"ok\": true, \"action\": \"completed_jarvis_review_runner\"}\\n'\n"
                ),
            )
            summary_path = root / "summary.json"

            with redirect_stdout(io.StringIO()):
                exit_code = script.main(
                    [
                        "decision-ingest",
                        "--openclaw-root",
                        str(openclaw),
                        "--allow-agent-dispatch",
                        "--summary-json",
                        str(summary_path),
                        "--instance-id",
                        "blackboard-bus-jarvis-terminal",
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["action"], "completed_jarvis_review_runner")
            self.assertTrue(summary["terminal"])

    def test_repeated_runs_can_share_the_same_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            scaffold_openclaw(openclaw)
            ledger_path = root / "awk.sqlite3"

            for instance_id in ("blackboard-publisher-repeat-1", "blackboard-publisher-repeat-2"):
                with redirect_stdout(io.StringIO()):
                    exit_code = script.main(
                        [
                            "publisher",
                            "--openclaw-root",
                            str(openclaw),
                            "--ledger",
                            str(ledger_path),
                            "--dry-run",
                            "--instance-id",
                            instance_id,
                        ]
                    )
                self.assertEqual(exit_code, 0)

            with sqlite3.connect(ledger_path) as conn:
                conn.row_factory = sqlite3.Row
                instances = conn.execute("SELECT instance_id, status FROM workflow_instances ORDER BY instance_id").fetchall()
                stage_runs = conn.execute("SELECT stage_run_id FROM stage_runs ORDER BY stage_run_id").fetchall()
            self.assertEqual([row["instance_id"] for row in instances], [
                "blackboard-publisher-repeat-1",
                "blackboard-publisher-repeat-2",
            ])
            self.assertEqual({row["status"] for row in instances}, {"done"})
            self.assertEqual(len(stage_runs), 2)
            self.assertEqual(len({row["stage_run_id"] for row in stage_runs}), 2)


def scaffold_openclaw(
    openclaw: Path,
    *,
    publisher_payload: dict | None = None,
    direct_loop_body: str | None = None,
) -> None:
    scripts = openclaw / "workspace-main" / "scripts"
    write_script(scripts / "surfaces" / "update_review_inbox.py", "import json\nprint(json.dumps({'ok': True}))\n")
    write_script(
        scripts / "surfaces" / "ingest_agent_reviews.py",
        "import json, sys\nprint(json.dumps({'ok': True, 'applied': '--apply' in sys.argv, 'argv': sys.argv[1:]}))\n",
    )
    write_script(
        scripts / "programs" / "agent_review_runner.py",
        "import json, sys\nprint(json.dumps({'ok': True, 'candidate': None, 'argv': sys.argv[1:]}))\n",
    )
    payload = publisher_payload or {"ok": True, "published": False}
    write_script(
        scripts / "surfaces" / "publish_or_research_attention.py",
        f"import json\nprint(json.dumps({payload!r}))\n",
    )
    write_script(
        openclaw / "scripts" / "lanes" / "run_blackboard_decision_loop_direct.sh",
        direct_loop_body or "#!/usr/bin/env bash\nprintf '{\"ok\": true, \"direct_loop\": true}\\n'\n",
    )


def write_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
