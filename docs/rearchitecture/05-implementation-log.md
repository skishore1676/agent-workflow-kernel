# 05 — Implementation Log

Branch `rearch/agent-workflow-kernel-end-to-end`, worktree
`/Users/suman/code/agent-workflow-kernel-rearch`. Invariant held throughout:
`./scripts/check.sh` green; public surface frozen (135 names).

## Commit trail
| Commit | Phase | What |
|--------|-------|------|
| `a94aca5` | P0–P3 | analysis artifacts (00–04 docs) |
| `7417013` | P-A/P-B | import-lint + frozen public-API guard + check.sh wiring |
| `f72740f`/amend | P-D | split `kernel.py` 3779→2133 (`_internal_types.py`, `_helpers.py`) |
| `91ae1d5` | P-C | split `local_adapters.py` 4207L → `local_adapters/` package |
| (this) | P-E/F/G | public-api doc, validation report, handoff |

## Phase A — safety net
Added `tests/test_kernel_public_api.py` pinning `len(__all__)==135`, no dupes,
all names importable. Confirmed 263 baseline. **Result:** green.

## Phase B — purity import-lint
`tools/import_lint.py` (ast-based): flags any import of `agent_workflow_kernel_*`
under the kernel package; relative + `agent_workflow_kernel.*` imports allowed.
`tests/test_kernel_import_lint.py` proves it passes clean and catches planted
`import`/`from`-imports. Wired into `scripts/check.sh`. **Result:** 272 passed.

## Phase D — kernel.py split
Ran via a defensive migration script (anchor-asserted line slices), then
verified. **Two bugs caught by the suite and fixed before commit:**
1. `_internal_types` imported `HardGate` from `.contracts`; it lives in `.policy`.
   Fixed the import source.
2. Four module-level constants in the helpers region (`_POLICY_CLASS_MAP`,
   `_HARD_GATE_RISK_MAP`, `_KNOWN_HUMAN_DECISIONS`, `_APPROVING_HUMAN_DECISIONS`)
   were not `def`s, so they weren't in `_helpers.__all__` and the class couldn't
   see them via `import *`. Fixed by exporting top-level constants too.
After fixes: import OK, **272 passed**. `kernel.py` 3779→2133; `_helpers.py` 1707;
`_internal_types.py` 116.

## Phase C — local_adapters.py split
Risk-checked first: 12 adapter classes are mutually independent (no cross-group
body references), helpers never reference classes (no cycle). Migration script
(anchor-asserted) scattered classes into `fakes/dry_run/sandbox/live/review` with
shared constants+helpers in `_shared.py`, re-exported by `__init__.py`.
**One bug caught and fixed:** moved files sit one level deeper, so
`from .adapters`/`from .contracts` had to become `from ..adapters`/`..contracts`.
After fix: import OK, all 12 classes present, **272 passed**. Largest file 1346.

## Deviations from plan
- `_helpers.py` (1707) and `local_adapters/sandbox.py` (1346) remain >1500/near it.
  `_helpers.py` is a cohesive "kernel helpers" module; further sub-splitting into
  `_policy/_gates/_stages` is a **low-risk follow-up** (deferred to protect the
  green finish, not because it's hard). `sandbox.py` is two cohesive sandbox
  classes. `kernel.py` (2133) and `storage.py` (1840) are dominated by the
  irreducible `WorkflowKernel`/`WorkflowLedger` class bodies — left intact per the
  ADR (class-body splits are higher risk, lower reward).
- `storage.py` export-helper extraction (P-E code change): skipped — it would not
  bring the file under target (the class is what's large) and adds risk for little
  gain. The API doc portion of P-E was done.

## Behavior-change items intentionally NOT touched (logged as follow-ups)
- Surface-method duplication (`_surface_op` consolidation) in `WorkflowKernel`.
- Broad `except Exception` in the adapter path collapsing failure classes.
- The implicit `state`-dict contract threaded through `run_once`.
All three are behavior-affecting and out of scope for a behavior-preserving lane.
