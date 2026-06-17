# 04 — Implementation Plan

Invariant for every phase: `./scripts/check.sh` = **263 passed**; public
`agent_workflow_kernel.*` surface unchanged. Each phase is its own commit.

## Phase A — Characterization & safety net (mostly pre-existing)
- **Goal:** lock current behavior before restructuring.
- **Scope:** test inventory; add a public-surface guard test.
- **Steps:** confirm 263 baseline (done); add `tests/test_kernel_public_api.py`
  asserting `__all__` size + that every exported name imports.
- **Validation:** new test passes on baseline.
- **Rollback:** delete the test. **Exit:** surface guard in place.

## Phase B — Purity import-lint (the plan's Phase-1 gate)
- **Goal:** mechanically enforce kernel agnosticism.
- **Scope:** `tools/import_lint.py`, `tests/test_kernel_import_lint.py`, `Makefile`/
  `scripts/check.sh` wiring.
- **Steps:** ast-walk every file under `packages/kernel/agent_workflow_kernel`,
  flag any import of a provider/adapter sibling distribution; CLI exits non-zero on
  violation. Test: clean tree passes; a planted violation string fails the checker.
- **Validation:** `python tools/import_lint.py` exits 0; test green; `make check`
  runs the lint.
- **Rollback:** revert tool + wiring. **Exit:** lint green and wired.

## Phase C — Split `local_adapters.py` → `local_adapters/` package
- **Goal:** kill the 4,207-line god-module, import-preserving.
- **Scope:** new `local_adapters/` package; `__init__.py` re-exports all names.
- **Steps:** group the ~10 classes + render helpers into `fakes/dry_run/sandbox/
  live/review/_render`; `__init__` re-exports exactly the prior public names.
- **Validation:** 263 green; `from agent_workflow_kernel import <each name>` works;
  `from agent_workflow_kernel.local_adapters import <name>` works.
- **Rollback:** restore single module from git. **Exit:** no file > ~1.5k lines;
  suite green.

## Phase D — Extract `kernel.py` helpers
- **Goal:** shrink the 3,779-line orchestrator to the class + dataclasses.
- **Scope:** `_internal_types.py`, `_policy.py`, `_gates.py`, `_stages.py`;
  re-import into `kernel.py`.
- **Steps:** move shared private dataclasses to `_internal_types`; move trailing
  stateless helpers into the three modules (peers import only contracts/
  `_internal_types`/each other, never `kernel`); kernel imports them back.
- **Validation:** 263 green after each extraction; no circular imports.
- **Rollback:** revert per-module. **Exit:** `kernel.py` ≈ class body only.

## Phase E — (budget-permitting) `storage.py` export helpers + API doc
- **Goal:** trim the 1,840-line ledger module; document the public surface.
- **Scope:** optional `_ledger_export.py`; `docs/rearchitecture/public-api.md`.
- **Validation:** 263 green. **Exit:** API doc written; storage trimmed if safe.

## Phase F — Simplify / verify no obsolete paths
- **Goal:** ensure the new structure is primary, not layered debris.
- **Steps:** grep for dead re-exports, stray `# moved` comments, unused imports in
  touched files. **Validation:** 263 green; import-lint green.

## Phase G — Proof & handoff
- **Goal:** reviewable branch. **Steps:** full suite, `git diff --stat`, self-review,
  write `05-implementation-log`, `06-validation-report`, `07-final-handoff`; run the
  compound gate. **Exit:** branch ready for review; STATE/INDEX closed.
