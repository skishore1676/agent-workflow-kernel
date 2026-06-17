"""Tests for the kernel purity import-lint (tools/import_lint.py).

Two guarantees:
1. The real kernel tree is clean today (and stays clean — this runs in CI).
2. The linter actually catches a violation (so the guard isn't a no-op).
"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import import_lint  # noqa: E402


class ImportLintTest(unittest.TestCase):
    def test_kernel_tree_is_pure(self) -> None:
        violations = import_lint.find_violations(import_lint.default_kernel_root())
        rendered = [v.render(import_lint.default_kernel_root()) for v in violations]
        self.assertEqual(violations, [], f"kernel purity violations: {rendered}")

    def test_main_returns_zero_on_clean_tree(self) -> None:
        self.assertEqual(import_lint.main([]), 0)

    def test_detects_forbidden_provider_import(self) -> None:
        with TemporaryDirectory() as tmp:
            offending = Path(tmp) / "bad.py"
            offending.write_text(
                "import agent_workflow_kernel_openclaw\n",
                encoding="utf-8",
            )
            violations = import_lint.find_violations(Path(tmp))
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].module, "agent_workflow_kernel_openclaw")

    def test_detects_forbidden_from_import(self) -> None:
        with TemporaryDirectory() as tmp:
            offending = Path(tmp) / "bad.py"
            offending.write_text(
                "from agent_workflow_kernel_codex_cli.runtime import Thing\n",
                encoding="utf-8",
            )
            violations = import_lint.find_violations(Path(tmp))
        self.assertEqual(len(violations), 1)
        self.assertTrue(violations[0].module.startswith("agent_workflow_kernel_codex_cli"))

    def test_allows_relative_and_kernel_imports(self) -> None:
        with TemporaryDirectory() as tmp:
            ok = Path(tmp) / "ok.py"
            ok.write_text(
                "from . import contracts\n"
                "from .policy import PolicyEngine\n"
                "import agent_workflow_kernel.contracts\n"
                "import json\n",
                encoding="utf-8",
            )
            violations = import_lint.find_violations(Path(tmp))
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
