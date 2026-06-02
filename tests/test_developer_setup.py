import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeveloperSetupTest(unittest.TestCase):
    def test_dev_setup_uses_safe_shell_flags_and_declared_dev_extra(self) -> None:
        script = (ROOT / "scripts" / "dev_setup.sh").read_text(encoding="utf-8")

        self.assertIn("#!/usr/bin/env bash", script)
        self.assertIn("set -euo pipefail", script)
        self.assertIn("python3 -m venv .venv", script)
        self.assertIn(".venv/bin/python -m pip install --upgrade pip", script)
        self.assertIn(".venv/bin/python -m pip install -e '.[dev]'", script)

    def test_check_prefers_venv_python_when_available(self) -> None:
        script = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")

        self.assertIn("#!/usr/bin/env bash", script)
        self.assertIn("set -euo pipefail", script)
        self.assertIn('PYTHON="${PYTHON:-python3}"', script)
        self.assertIn("if [[ -x .venv/bin/python ]]", script)
        self.assertIn('PYTHON=".venv/bin/python"', script)
        self.assertIn('"$PYTHON" -m unittest discover -s tests', script)
        self.assertIn('"$PYTHON" -m pytest', script)
        self.assertIn("Run ./scripts/dev_setup.sh first", script)

    def test_makefile_exposes_one_command_setup_and_check_targets(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn(".PHONY: setup check", makefile)
        self.assertIn("setup:\n\t./scripts/dev_setup.sh", makefile)
        self.assertIn("check:\n\t./scripts/check.sh", makefile)

    def test_generated_development_outputs_remain_ignored(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        for pattern in ("__pycache__/", ".pytest_cache/", ".venv/", "*.egg-info/"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignore)


if __name__ == "__main__":
    unittest.main()
