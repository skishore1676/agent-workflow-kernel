# 07 — Final Handoff

## 1. Summary
Made the `agent_workflow_kernel` package **vendor-ready** for the approved
`lane-host` repo: enforced its purity invariant, killed its two god-modules, froze
and documented its public surface — with **zero behavior change** (263→272 tests,
all green; public API pinned at 135 names). Scope was deliberately the kernel
package only (confirmed with Suman), because the lane-host plan vendors in the
kernel and treats the rest of this repo as a recovery archive.

The architecture's boundaries were already sound (clean acyclic module graph,
`contracts` as the pure-types hub). The problems were oversized files and an
unenforced agnosticism invariant — both now addressed without re-wiring boundaries.

## 2. Major design decisions
- **Import-preserving extraction over rewrite.** Module→package facades and
  helper-extraction-behind-`import *` keep all 135 public names and every call
  site working. Tradeoff: 9 deliberate wildcard imports (documented) in exchange
  for a near-zero-risk, behavior-identical refactor.
- **Shared private types isolated** in `_internal_types.py` so the class body and
  the extracted helpers share dataclasses/constants without a circular import.
- **Class bodies left intact.** `WorkflowKernel` (2133) and `WorkflowLedger`
  (1840) are the irreducible cores; splitting a 2k-line stateful class via
  mixins is higher risk for lower reward — rejected.
- **Purity as a tested lint**, not a convention: `tools/import_lint.py` is wired
  into `make check` and has a test that plants a violation and asserts failure.

## 3. Phases completed
| Phase | Deliverable | Commit |
|-------|-------------|--------|
| P0–P3 | analysis: baseline, current arch, 7 orthogonal reviews, ADR, plan | `a94aca5` |
| A+B | public-API guard + purity import-lint, wired into CI | `7417013` |
| D | `kernel.py` 3779→2133 (`_internal_types.py`, `_helpers.py`) | `f72740f` |
| C | `local_adapters.py` 4207L → 7-file package | `91ae1d5` |
| E/F/G | public-api doc, validation report, this handoff | (final) |

## 4. Validation
- `make check`: **272 passed** (263 baseline + 9 new); zero introduced failures.
- `import_lint`: kernel is pure.
- CLI E2E (`run-local`): real stage execution + ledger receipts + human-gate halt.
- Public surface: pinned at 135, verified by test.
- Full detail in `06-validation-report.md`.

## 5. Reviewer guide
Suggested order:
1. **`docs/rearchitecture/03-target-architecture.md`** — the decision and why
   (especially the scope reconciliation vs `docs/rearchitecture-plan.md`).
2. **`tools/import_lint.py`** + **`tests/test_kernel_import_lint.py`** — small,
   self-contained, the highest-leverage new asset (the lane-host Phase-1 gate).
3. **`packages/kernel/agent_workflow_kernel/_internal_types.py`** — the seam that
   makes the kernel split work (read this before `_helpers.py`).
4. **`kernel.py` head** (imports + the two `import *` lines) and
   **`local_adapters/__init__.py`** — confirm the facades preserve the surface.
5. **`tests/test_kernel_public_api.py`** — the frozen-surface guard.

**Risky areas to scrutinize:** the wildcard imports in `kernel.py` and the
`local_adapters/` submodules (deliberate, but verify no shadowing); the
`..adapters`/`..contracts` relative-import depth in `local_adapters/*`.
**Reassurance:** every moved line is verbatim; `git diff main -- kernel.py` shows
deletions only (the helpers moved out), and the new modules are additions.

## 6. Follow-up work (all low-risk, none blocking)
- Sub-split `_helpers.py` (1707) into `_policy/_gates/_stages` per the grouping in
  `02-orthogonal-reviews.md` §2.7 / the design-review agent's proposal.
- (Behavior-changing, separate PRs) consolidate the three near-duplicate surface
  methods into a `_surface_op` helper; differentiate failure classes at the broad
  `except Exception`; make the `run_once` `state`-dict contract explicit.
- (lane-host) decide whether `local_adapters` (sandbox/live surfaces) travels with
  the vendored kernel or moves to a host/dev layer.

## 7. Confidence assessment
- **Confident:** behavior is identical (facade suite + E2E green; verbatim moves;
  pinned surface); the purity gate works (tested both directions).
- **Needs human judgment:** the wildcard-import style (acceptable for
  import-preservation, but a reviewer may prefer explicit lists — easy to change);
  whether to land the `_helpers.py` sub-split now or in lane-host.
- **Known limitations:** thin coverage of error/budget/timeout paths is unchanged;
  this lane did not add behavior tests beyond the two structural guards.
