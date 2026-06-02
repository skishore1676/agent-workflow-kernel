import json
import os
import stat
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import codex_runtime_lane_comparison as comparison  # noqa: E402


SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def write_fake_codex(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            prompt = sys.stdin.read()
            output_path = args[args.index("--output-last-message") + 1]
            payload = {{
                "schema": "draft_package_result.v1",
                "outcome": "ready",
                "title": "Agent-to-Agent Communication Needs Boring Receipts",
                "lede": "A local fixture-backed draft package.",
                "outline": ["Receipts", "Handoffs", "Approval gates"],
                "draft_package": "Fixture-only draft.",
                "source_trail": {{
                    "fixture_id": "ivy-jonah-p3-approval-to-p5-shadow",
                    "project_id": "agent-to-agent-communication-live",
                    "receipt_ids": ["receipt:p3_review_selected"]
                }},
                "public_publish_blocked": True,
                "next_action": "Keep P5 approval gated."
            }}
            if "Remember this fixture id" in prompt:
                payload = {{"ok": True, "seeded": True}}
            Path(output_path).write_text(json.dumps(payload), encoding="utf-8")
            resumed = "resume" in args
            print(json.dumps({{
                "type": "turn.completed",
                "session_id": "{SESSION_ID}",
                "usage": {{"input_tokens": 11, "output_tokens": 7}},
                "resumed": resumed
            }}))
            """
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class CodexRuntimeLaneComparisonTest(unittest.TestCase):
    def test_comparison_runs_with_fake_codex_and_writes_packet(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake = temp / "codex"
            output = temp / "packet"
            write_fake_codex(fake)

            packet = comparison.run_comparison(
                output_dir=output,
                workflow_path=comparison.DEFAULT_WORKFLOW,
                fixture_path=comparison.DEFAULT_FIXTURE,
                codex_executable=str(fake),
                timeout_seconds=10,
                model=None,
                stage_id=comparison.DEFAULT_STAGE_ID,
            )

            by_id = {item["path_id"]: item for item in packet["metrics"]}
            self.assertEqual(packet["stage"]["experiment_adapter"], "runtime.codex_cli_session")
            self.assertTrue(by_id["codex_cli_session"]["session"]["session_reused"])
            self.assertEqual(by_id["codex_cli_session"]["token_usage"]["combined_total_tokens"], 36)
            self.assertEqual(by_id["codex_cli_session"]["quality"]["grade"], "excellent")
            self.assertEqual(by_id["direct_script"]["token_usage"]["total_tokens"], 0)
            self.assertEqual(by_id["openclaw_fixture"]["quality"]["grade"], "receipt-parity-only")
            self.assertTrue((output / "codex_cli_session" / "stage_payload.json").exists())

    def test_cli_requires_real_codex_opt_in_without_fake_executable(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            comparison.main(["--output-dir", os.devnull])
        self.assertIn("Real Codex CLI execution requires", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
