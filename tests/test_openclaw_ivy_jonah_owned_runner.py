import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


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
    def test_run_mode_prepares_publish_packet_and_passes_publish_fields_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            write_fake_openclaw_scripts(openclaw, action="prepared_ivy_writing_ops_publish_packet")
            write_project(openclaw, project_id="nvidia-test", gate="P5", status="needs_suman", needs_suman=True)
            write_handoff(
                openclaw,
                artifact_id="publish-test",
                project_id="nvidia-test",
                handoff_type="ivy_writing_ops_m5_publish_decision",
                action="prepare_publish_bundle",
                decision="approved",
                decision_label="publish_substack_medium",
            )

            summary = script.run_owned_ivy_jonah(
                openclaw_root=openclaw,
                ledger_path=root / "awk.sqlite3",
                stale_minutes=15,
                instance_id="ivy-owned-test",
                now="2026-06-01T12:00:00Z",
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["workflow_status"], "done")
            self.assertEqual(summary["action"], "prepared_ivy_writing_ops_publish_packet")
            self.assertEqual(summary["compatibility_action"], "handled")
            self.assertEqual(summary["project_id"], "nvidia-test")
            self.assertTrue(summary["publish_ready_path"].endswith("publish-ready.md"))
            self.assertTrue(summary["browser_plan_path"].endswith("browser-plan.md"))
            self.assertTrue(summary["publish_staging_path"].endswith("publish-staging.json"))
            self.assertEqual(summary["runner_result"]["action"], "prepared_ivy_writing_ops_publish_packet")
            self.assertEqual([row["stage_id"] for row in summary["stage_runs"]], [
                "audit_editorial_path",
                "run_review_handoff",
                "refresh_blackboard",
            ])
            self.assertTrue((openclaw / "blackboard-refreshed.txt").exists())
            self.assertTrue(all(receipt["legacy_compatibility_adapter"] is False for receipt in summary["receipts"]))

    def test_wrapper_stages_document_deterministic_no_prompt_reason(self) -> None:
        workflow = script.ivy_jonah_owned_workflow()

        self.assertEqual(
            [stage.no_prompt_reason for stage in workflow.stages],
            [script.DETERMINISTIC_OWNED_RUNNER_NO_PROMPT_REASON] * 4,
        )

    def test_p3_approval_runs_native_ivy_jonah_loop_without_work_ledger_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            write_fake_openclaw_scripts(openclaw, action="native_editorial_accept")
            write_project(openclaw, project_id="flowr", gate="P3", status="needs_suman", needs_suman=True)
            write_handoff(
                openclaw,
                artifact_id="flowr-p3",
                project_id="flowr",
                handoff_type="ivy_writing_ops_p3_approved_to_p4",
                action="advance_to_p4",
                decision="approved",
                decision_label="approve_to_p4",
            )

            with mock.patch.dict(os.environ, {"OPENCLAW_COMMAND": str(openclaw / "bin" / "openclaw")}):
                summary = script.run_owned_ivy_jonah(
                    openclaw_root=openclaw,
                    ledger_path=root / "awk.sqlite3",
                    instance_id="ivy-owned-native",
                    now="2026-06-01T12:00:00Z",
                )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["action"], "published_ivy_human_gate")
            self.assertEqual(summary["project_id"], "flowr")
            self.assertEqual(summary["gate"], "P5")
            project = json.loads((openclaw / "workspace/agents/ivy_writing_ops/projects/flowr/project.json").read_text())
            self.assertEqual(project["gate"], "P5")
            self.assertEqual(project["status"], "needs_suman")
            self.assertTrue((openclaw / "workspace/agents/ivy_writing_ops/projects/flowr/p4_editor_review.md").exists())

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
                "advance_lifecycle",
            ])
            self.assertFalse((openclaw / "blackboard-refreshed.txt").exists())

    def test_noop_handoff_advances_p2_then_publishes_p3_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            write_fake_openclaw_scripts(openclaw, action="noop")
            write_project(openclaw, project_id="flowr", gate="P2", status="active", needs_suman=False)

            summary = script.run_owned_ivy_jonah(
                openclaw_root=openclaw,
                ledger_path=root / "awk.sqlite3",
                instance_id="ivy-owned-advance",
                now="2026-06-01T12:00:00Z",
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["action"], "published_ivy_human_gate")
            self.assertEqual(summary["project_id"], "flowr")
            self.assertEqual(summary["gate"], "P3")
            self.assertEqual(summary["advanced_from_gate"], "P2")
            self.assertEqual(summary["advanced_to_gate"], "P3")
            self.assertEqual(summary["owner"], "human")
            project = json.loads((openclaw / "workspace/agents/ivy_writing_ops/projects/flowr/project.json").read_text())
            self.assertEqual(project["gate"], "P3")
            self.assertEqual(project["status"], "needs_suman")
            self.assertTrue((openclaw / "blackboard-refreshed.txt").exists())

    def test_noop_handoff_publishes_existing_human_owned_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw = root / "openclaw"
            write_fake_openclaw_scripts(openclaw, action="noop")
            write_project(openclaw, project_id="flowr", gate="P5", status="needs_suman", needs_suman=True)

            summary = script.run_owned_ivy_jonah(
                openclaw_root=openclaw,
                ledger_path=root / "awk.sqlite3",
                instance_id="ivy-owned-human-gate",
                now="2026-06-01T12:00:00Z",
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["action"], "published_ivy_human_gate")
            self.assertEqual(summary["project_id"], "flowr")
            self.assertEqual(summary["gate"], "P5")
            self.assertEqual(summary["owner"], "human")
            self.assertTrue(summary["attention_path"].endswith(".json"))
            self.assertEqual(summary["review_note_rel"], "03 Agent Org/ivy_writing_ops/Reviews/flowr.md")
            self.assertTrue((openclaw / "blackboard-refreshed.txt").exists())

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
            write_fake_openclaw_scripts(openclaw, action="prepared_ivy_writing_ops_publish_packet")
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
    ledger = openclaw / "workspace" / "agents" / "ivy_writing_ops" / "scripts" / "or_project_ledger.py"
    attention = openclaw / "workspace" / "agents" / "ivy_writing_ops" / "scripts" / "ivy_writing_ops_v2.py"
    publisher = openclaw / "workspace-main" / "scripts" / "surfaces" / "publish_or_research_attention.py"
    refresh = openclaw / "workspace-main" / "scripts" / "surfaces" / "update_review_inbox.py"
    publish_control = openclaw / "scripts" / "lanes" / "ivy_writing_ops_publish_control.py"
    openclaw_bin = openclaw / "bin" / "openclaw"
    cli.parent.mkdir(parents=True, exist_ok=True)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    attention.parent.mkdir(parents=True, exist_ok=True)
    publisher.parent.mkdir(parents=True, exist_ok=True)
    refresh.parent.mkdir(parents=True, exist_ok=True)
    publish_control.parent.mkdir(parents=True, exist_ok=True)
    openclaw_bin.parent.mkdir(parents=True, exist_ok=True)
    (openclaw / "workspace" / "agents" / "ivy_writing_ops" / "handoffs" / "review_decisions").mkdir(
        parents=True,
        exist_ok=True,
    )
    (openclaw / "workspace" / "agents" / "ivy_writing_ops").mkdir(parents=True, exist_ok=True)
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
    ledger.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "args = sys.argv[1:]",
                "root = Path('.')",
                "if args[:2] == ['--root', 'workspace/agents/ivy_writing_ops']:",
                "    root = Path(args[1]); args = args[2:]",
                "cmd = args[0]",
                "if cmd == 'advance':",
                "    project = args[args.index('--project') + 1]",
                "    to_gate = args[args.index('--to') + 1]",
                "    path = root / 'projects' / project / 'project.json'",
                "    data = json.loads(path.read_text())",
                "    data['gate'] = to_gate",
                "    data['status'] = 'needs_suman' if to_gate in {'P3', 'P5'} else 'active'",
                "    data['needs_suman'] = to_gate in {'P3', 'P5'}",
                "    data['next_action'] = 'Suman P3 spine review before P4 draft package' if to_gate == 'P3' else ('Suman final review / publish decision' if to_gate == 'P5' else 'next machine step')",
                "    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\\n')",
                "    artifact = root / 'projects' / project / ('p' + to_gate[1:] + '_artifact.md')",
                "    artifact.write_text('# artifact\\n')",
                "    print(json.dumps(data))",
                "elif cmd == 'review-decision':",
                "    project = args[args.index('--project') + 1]",
                "    gate = args[args.index('--gate') + 1]",
                "    decision = args[args.index('--decision') + 1]",
                "    action = args[args.index('--action') + 1]",
                "    path = root / 'projects' / project / 'project.json'",
                "    data = json.loads(path.read_text())",
                "    data.setdefault('gate_reviews', []).append({'gate': gate, 'decision': decision, 'action': action})",
                "    if decision == 'approved' and gate == 'P3' and action == 'advance_to_p4':",
                "        data['status'] = 'active'",
                "        data['needs_suman'] = False",
                "    elif decision == 'revision_requested':",
                "        data['status'] = 'needs_suman'",
                "        data['needs_suman'] = True",
                "    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\\n')",
                "    print(json.dumps(data))",
                "elif cmd == 'editor-review':",
                "    project = args[args.index('--project') + 1]",
                "    status = args[args.index('--status') + 1]",
                "    summary = args[args.index('--summary') + 1]",
                "    recommendation = args[args.index('--recommendation') + 1] if '--recommendation' in args else ''",
                "    issues = []",
                "    cursor = 0",
                "    while '--issue' in args[cursor:]:",
                "        idx = args.index('--issue', cursor)",
                "        issues.append(args[idx + 1])",
                "        cursor = idx + 2",
                "    path = root / 'projects' / project / 'p4_editor_review.md'",
                "    lines = ['# Editor Review', f'- Status: `{status}`', f'- Summary: {summary}', f'- Recommendation: {recommendation}', '', '## Issues', *[f'- {item}' for item in issues]]",
                "    path.write_text('\\n'.join(lines).rstrip() + '\\n')",
                "    print(json.dumps({'ok': True, 'path': str(path)}))",
                "elif cmd == 'render':",
                "    project = args[args.index('--project') + 1]",
                "    gate = args[args.index('--gate') + 1]",
                "    path = root / 'projects' / project / ('p5_final_review.md' if gate == 'P5' else 'p3_research_brief.md')",
                "    path.write_text(f'# rendered {gate}\\n', encoding='utf-8')",
                "    print(json.dumps({'ok': True, 'path': str(path)}))",
                "elif cmd == 'status':",
                "    project = args[args.index('--project') + 1]",
                "    status = args[args.index('--status') + 1]",
                "    path = root / 'projects' / project / 'project.json'",
                "    data = json.loads(path.read_text())",
                "    data['status'] = status",
                "    data['needs_suman'] = status == 'needs_suman'",
                "    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\\n')",
                "    print(json.dumps(data))",
                "elif cmd in {'source-intake-plan', 'weekly-post-candidate', 'lint'}:",
                "    print(json.dumps({'ok': True, 'action': cmd}))",
                "else:",
                "    print(json.dumps({'ok': False, 'action': cmd}))",
                "    raise SystemExit(1)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    attention.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "args = sys.argv[1:]",
                "root = Path('.')",
                "if args[:2] == ['--root', 'workspace/agents/ivy_writing_ops']:",
                "    root = Path(args[1]); args = args[2:]",
                "artifact = args[args.index('--artifact-path') + 1]",
                "out = root / 'handoffs' / 'attention' / 'attention-flowr.json'",
                "out.parent.mkdir(parents=True, exist_ok=True)",
                "payload = {'ok': True, 'output_path': str(out), 'artifact_path': artifact}",
                "out.write_text(json.dumps(payload, indent=2) + '\\n')",
                "print(json.dumps(payload))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    publisher.write_text(
        "\n".join(
            [
                "import json",
                "print(json.dumps({'ok': True, 'published': True, 'already_published': False, 'review_note': '/tmp/flowr.md', 'review_note_rel': '03 Agent Org/ivy_writing_ops/Reviews/flowr.md', 'artifact_record': 'record.json'}))",
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
    publish_control.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "args = sys.argv[1:]",
                "or_root = Path(args[args.index('--or-root') + 1])",
                "project = args[args.index('--project') + 1]",
                "cmd = args[0]",
                "project_dir = or_root / 'projects' / project",
                "bundle_dir = project_dir / 'publish_bundle'",
                "bundle_dir.mkdir(parents=True, exist_ok=True)",
                "if cmd == 'prepare-bundle':",
                "    publish_ready = bundle_dir / 'publish-ready.md'",
                "    publish_ready.write_text('# publish ready\\n', encoding='utf-8')",
                "    staging = project_dir / 'publish-staging.json'",
                "    staging.write_text(json.dumps({'ok': True, 'projectId': project}) + '\\n', encoding='utf-8')",
                "    print(json.dumps({'ok': True, 'publish_ready': str(publish_ready), 'publish_staging': str(staging)}))",
                "elif cmd == 'browser-plan':",
                "    plan = project_dir / 'browser-plan.md'",
                "    plan_json = project_dir / 'browser-plan.json'",
                "    plan.write_text('# browser plan\\n', encoding='utf-8')",
                "    plan_json.write_text(json.dumps({'ok': True}) + '\\n', encoding='utf-8')",
                "    print(json.dumps({'ok': True, 'plan': str(plan), 'planJson': str(plan_json)}))",
                "else:",
                "    raise SystemExit(1)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    openclaw_bin.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, sys",
                "args = sys.argv[1:]",
                "agent = args[args.index('--agent') + 1]",
                "message = args[args.index('--message') + 1]",
                "payload = {'schema': 'unknown', 'outcome': 'block'}",
                f"mode = {action!r}",
                "if mode == 'native_editorial_accept' and agent == 'ivy_writing_ops' and 'draft_package_result.v1' in message:",
                "    payload = {'schema': 'draft_package_result.v1', 'outcome': 'ready', 'summary': 'draft ready', 'draft_package_markdown': '# P4 package\\n\\n## Recommended Visuals\\n\\n- Visual\\n', 'source_trail_markdown': '# Source trail\\n'}",
                "elif mode == 'native_editorial_accept' and agent == 'jonah_editor' and 'editorial_verdict.v1' in message:",
                "    payload = {'schema': 'editorial_verdict.v1', 'outcome': 'accepted', 'editor_review_status': 'publishable_with_minor_edits', 'summary': 'accepted', 'issues': ['Tighten one transition.'], 'recommendation': 'Advance to P5.'}",
                "elif mode == 'native_editorial_accept' and agent == 'ivy_writing_ops' and 'revision_result.v1' in message:",
                "    payload = {'schema': 'revision_result.v1', 'outcome': 'revised', 'summary': 'revised', 'revised_draft_package_markdown': '# Revised P4 package\\n'}",
                "result = {'status': 'done', 'runId': 'run-1', 'result': {'payloads': [{'text': json.dumps(payload)}], 'meta': {'agentMeta': {'sessionId': 'sess-1'}}}}",
                "print(json.dumps(result))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    openclaw_bin.chmod(0o755)


def write_project(openclaw: Path, *, project_id: str, gate: str, status: str, needs_suman: bool) -> None:
    project_dir = openclaw / "workspace" / "agents" / "ivy_writing_ops" / "projects" / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    artifact_name = {
        "P1": "p1_scout.md",
        "P2": "p2_deep_dive.md",
        "P3": "p3_research_brief.md",
        "P4": "p4_draft_package.md",
        "P5": "p5_final_review.md",
    }[gate]
    (project_dir / artifact_name).write_text("# review artifact\n", encoding="utf-8")
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "id": project_id,
                "title": "Flowr",
                "gate": gate,
                "status": status,
                "needs_suman": needs_suman,
                "target_channel": "brief",
                "article_type": "tool_teardown",
                "next_action": "review" if needs_suman else "advance",
                "events": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def write_handoff(
    openclaw: Path,
    *,
    artifact_id: str,
    project_id: str,
    handoff_type: str,
    action: str,
    decision: str,
    decision_label: str,
) -> None:
    path = openclaw / "workspace" / "agents" / "ivy_writing_ops" / "handoffs" / "review_decisions" / f"{artifact_id}.json"
    path.write_text(
        json.dumps(
            {
                "artifact_id": artifact_id,
                "agent_id": "ivy_writing_ops",
                "status": "pending",
                "project_id": project_id,
                "handoff_type": handoff_type,
                "action": action,
                "decision": decision,
                "decision_label": decision_label,
                "comments": [],
                "inline_comments": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
