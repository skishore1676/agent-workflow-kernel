import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "render_openclaw_prompt_profile.py"
    spec = importlib.util.spec_from_file_location("render_openclaw_prompt_profile_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = load_script()


class OpenClawPromptProfileRendererTest(unittest.TestCase):
    def test_renders_weekly_improvement_cargo_profile_with_provenance(self) -> None:
        payload = script.render_profile(
            "jarvis_weekly_improvement_cargo",
            inputs={
                "instance_id": "cargo-1",
                "objective": "produce one real weekly improvement artifact",
                "evidence_paths": ["cutover_receipt.json", "owned_completion_summary.json"],
            },
        )

        self.assertEqual(payload["profile"], "jarvis_weekly_improvement_cargo")
        self.assertTrue(payload["prompt_bundle_digest"].startswith("sha256:"))
        self.assertTrue(payload["rendered_input_digest"].startswith("sha256:"))
        self.assertIn("stage.jarvis_weekly.improvement_cargo", {ref["id"] for ref in payload["refs"]})
        self.assertIn("Jarvis Weekly Improvement Cargo", payload["rendered_input"])
        self.assertIn("produce one real weekly improvement artifact", payload["rendered_input"])

    def test_cli_writes_cutover_prompt_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "profile.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "render_openclaw_prompt_profile.py"),
                    "openclaw_cutover_review_weekly",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("stage.openclaw.cutover_review_artifact", {ref["id"] for ref in payload["refs"]})
            self.assertIn("OpenClaw Cutover Review Artifact", payload["rendered_input"])


if __name__ == "__main__":
    unittest.main()

