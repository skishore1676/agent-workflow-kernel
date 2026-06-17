---
title: The kernel package must import no provider/adapter package — enforced by tools/import_lint.py
type: pattern
area: packages/kernel/agent_workflow_kernel
date: 2026-06-17
tags: [agnosticism, lint, vendoring, lane-host, ci]
refs: [tools/import_lint.py, tests/test_kernel_import_lint.py, scripts/check.sh, docs/rearchitecture-plan.md]
---

# Kernel purity is a tested invariant, not a convention

## Context
The lane-host program vendors in only the kernel and treats the rest of this repo
as a recovery archive. The kernel's whole value is being **agent-agnostic** — it
must not couple to OpenClaw/Codex/Claude. That was true by habit but nothing
enforced it.

## What we learned
The agnosticism rule has a precise, mechanical form: a file under
`packages/kernel/agent_workflow_kernel/` may not import any top-level module whose
name starts with `agent_workflow_kernel_` (the kernel itself is
`agent_workflow_kernel` — no trailing underscore; every provider/adapter sibling
is `agent_workflow_kernel_<openclaw|codex_cli|codex_sdk|a2a|x_digest|ivy|artifact_validation>`).
`tools/import_lint.py` enforces it with an `ast` walk and is wired into
`scripts/check.sh` (so `make check` fails on a violation). The lint has a test
(`tests/test_kernel_import_lint.py`) that plants a violation and asserts the
checker catches it — the guard itself is guarded.

## Why / when it applies
This is the lane-host plan's Phase-1 acceptance gate. Any time you add code to the
kernel package, the lint keeps the boundary honest. Relative imports (`from .`)
and `agent_workflow_kernel.*` imports are fine; only the sibling distributions are
forbidden.

## Specifics
- Rule: `top_module.startswith("agent_workflow_kernel_")` on every `import` /
  `from ... import` (level 0 only; relative imports skipped).
- `find_violations(root: Path) -> list[Violation]` is importable for tests;
  `main()` is the CLI (exit 1 on any violation).

## Apply it next time
Adding a runtime/provider integration to the kernel? You can't — put it in a
provider package and have the kernel depend on the adapter **protocol**
(`RuntimeAdapter`/`SurfaceAdapter` in `adapters.py`), resolved via
`adapter_registry`. If `make check` prints "Kernel purity violations", you crossed
the boundary. See also [[import-preserving-god-module-split]].
