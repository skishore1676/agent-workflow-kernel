#!/usr/bin/env python3
"""Kernel purity import-lint.

Enforces the agnosticism invariant the lane-host program depends on
(docs/rearchitecture-plan.md, Phase 1 acceptance gate):

    packages/kernel/agent_workflow_kernel/** must import nothing from any
    provider/adapter sibling distribution.

The kernel distribution is ``agent_workflow_kernel`` (no trailing underscore).
Every sibling provider/adapter package is ``agent_workflow_kernel_<name>``
(openclaw, codex_cli, codex_sdk, a2a, x_digest, ivy, artifact_validation, ...).
So the rule is simply: a kernel file may not import any top-level module whose
name starts with ``agent_workflow_kernel_``.

Pure stdlib (ast + pathlib). Exit code 0 when clean, 1 when violations exist.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN_PREFIX = "agent_workflow_kernel_"


@dataclass(frozen=True)
class Violation:
    path: Path
    lineno: int
    module: str

    def render(self, root: Path) -> str:
        try:
            rel = self.path.relative_to(root)
        except ValueError:
            rel = self.path
        return f"{rel}:{self.lineno}: forbidden import of provider/adapter package '{self.module}'"


def _is_forbidden(top_module: str) -> bool:
    return top_module.startswith(FORBIDDEN_PREFIX)


def _module_violations(tree: ast.AST, path: Path) -> list[Violation]:
    found: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if _is_forbidden(top):
                    found.append(Violation(path, node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative (within-package) import — always allowed.
            if node.level == 0 and node.module:
                top = node.module.split(".")[0]
                if _is_forbidden(top):
                    found.append(Violation(path, node.lineno, node.module))
    return found


def find_violations(root: Path) -> list[Violation]:
    """Return every forbidden import under ``root`` (recursively)."""
    violations: list[Violation] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - surfaced, not swallowed
            raise SystemExit(f"import_lint: cannot parse {path}: {exc}") from exc
        violations.extend(_module_violations(tree, path))
    return violations


def default_kernel_root() -> Path:
    # tools/import_lint.py -> repo root is parent of tools/.
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "packages" / "kernel" / "agent_workflow_kernel"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kernel purity import-lint.")
    parser.add_argument(
        "--root",
        type=Path,
        default=default_kernel_root(),
        help="Directory tree to check (default: the kernel package).",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.exists():
        print(f"import_lint: root does not exist: {root}", file=sys.stderr)
        return 2

    violations = find_violations(root)
    if violations:
        print("Kernel purity violations (kernel must not import provider/adapter packages):")
        for v in violations:
            print(f"  {v.render(root)}")
        print(f"\n{len(violations)} violation(s). The kernel must stay agent-agnostic.")
        return 1
    print(f"import_lint: OK — {root.name} is import-pure of provider/adapter packages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
