---
title: Split a god-module with zero behavior change via an import-preserving facade + anchor-asserted migration script
type: pattern
area: packages/kernel/agent_workflow_kernel
date: 2026-06-17
tags: [refactor, packaging, circular-imports, migration, tests]
refs: [packages/kernel/agent_workflow_kernel/_internal_types.py, packages/kernel/agent_workflow_kernel/_helpers.py, packages/kernel/agent_workflow_kernel/local_adapters/__init__.py]
---

# Import-preserving god-module split

## Context
`kernel.py` (3779L) and `local_adapters.py` (4207L) were unnavigable. They had to
shrink with **zero behavior change** and a **frozen public surface** (the kernel
is being vendored; 135 names in `agent_workflow_kernel.__all__`). Tests import
~93% via the top-level facade, which is what makes this safe.

## What we learned
Two shapes, both keeping every call site working:
1. **Module → package facade.** `local_adapters.py` → `local_adapters/` with one
   file per cohesive group (`fakes/dry_run/sandbox/live/review`) + a `_shared.py`
   for constants/helpers, and an `__init__.py` that **re-exports every previously
   module-level name**. External imports are unchanged.
2. **Helper extraction behind `import *`.** `kernel.py`'s ~1500 trailing stateless
   helpers → `_helpers.py`; the class re-imports them with `from ._helpers import
   *`. Shared private dataclasses/constants that BOTH the class and the helpers use
   go in a **leaf** `_internal_types.py` (imports only `contracts`/`policy`) to
   avoid a circular import.

Do it with a **defensive migration script** that asserts each anchor line matches
expected content before slicing (drift aborts instead of corrupting), moving code
verbatim. Run the full suite after each split.

## Why / when it applies
Any oversized module where the public surface must not move and you have a
facade-level test suite as the net. Prefer this over a hand rewrite: the diff is
"deletions from the big file + verbatim additions", trivially reviewable.

## Specifics — gotchas that cost a red suite (all caught before commit)
- **`import *` only re-exports `__all__`.** Underscore helpers AND module-level
  constants must be listed in the new module's `__all__`, or the class can't see
  them (`NameError: _APPROVING_HUMAN_DECISIONS`). Extract **constants**, not just
  `def`s, into `__all__`.
- **Know where a symbol actually lives.** `HardGate` is exported by `.policy`, not
  `.contracts` — a wrong re-import fails at import time.
- **Moving a file one level deeper bumps relative imports.** `from .adapters` must
  become `from ..adapters` (and `..contracts`) inside `local_adapters/*`.
- Freeze the surface with a test (`tests/test_kernel_public_api.py` pins
  `len(__all__) == 135`) so any dropped/renamed export fails loudly.

## Apply it next time
Use the anchor-asserted slice script (kept under the goal's `.super-goal/` scratch)
as a template. Sequence: pin the public surface in a test → write the split via
script with `at(line, prefix)` assertions → run suite → fix import sources/levels
and `__all__` → commit one file-move per phase. See also [[kernel-purity-import-lint]].
