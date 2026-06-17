# 06 — Validation Report

Branch `rearch/agent-workflow-kernel-end-to-end`. All commands run in the worktree
venv (`.venv/bin/python`, Python 3.13.7).

## Commands & results
| Command | Purpose | Result |
|---------|---------|--------|
| `./scripts/check.sh` | import-lint + `unittest discover` + `pytest` | **272 passed** (263 baseline + 9 new) |
| `python tools/import_lint.py` | kernel purity gate standalone | **OK** (pure) |
| `python -m agent_workflow_kernel.cli validate workflows/x_digest_post_review.yaml` | CLI smoke | `{"ok": true, "stages": 9, "transitions": 26, ...}` |
| `python -m agent_workflow_kernel.cli run-local workflows/deterministic_system_action.yaml` | E2E behavior | ran 4 stages, **28 events + 4 receipts written**, halted `waiting_on_human` at the human gate |

The `run-local` smoke is the strongest signal: the refactored kernel actually
drives a workflow through real stage execution, writes a SQLite ledger with
receipts, and stops correctly at a human gate — behavior preserved end to end,
not just at the unit level.

## Pre-existing vs introduced failures
- Baseline (`main` @ `6c65514`): **263 passed**, no failures.
- After all changes: **272 passed**, no failures. Zero introduced failures.
- The +9 are the two new test files (public-API guard ×4, import-lint ×5).

## Failures fixed during implementation (caught by the suite before commit)
1. `_internal_types` imported `HardGate` from the wrong module (`.contracts` →
   `.policy`). Caught at import; fixed.
2. Four helper-region module constants missing from `_helpers.__all__` →
   `NameError` in `test_workflow_kernel_run_once`. Fixed by exporting constants.
3. `local_adapters/*` relative imports needed `..` after moving a level deeper.
   Caught at import; fixed.
All three were found by running the suite/import immediately after each migration
and resolved before the phase was committed. No failure was left unresolved.

## Frozen-surface verification
`test_kernel_public_api.py` asserts `len(__all__) == 135`, no duplicates, every
name importable — green. The public API is provably unchanged.

## Coverage gaps (unchanged by this lane; logged, not introduced)
- Budget/timeout depth, adapter-exception paths, `local_adapters` internal render
  helpers, lease policy remain thin (see `02-orthogonal-reviews.md` §2.4). This
  lane did not change behavior, so it neither closed nor widened these gaps.

## Confidence
**High** that behavior is identical: a strong facade-level suite (263) plus a
real run-local E2E all pass, the public surface is pinned, and every change is a
verbatim code move behind a re-export. The residual risk is the usual one for
large mechanical moves (a missed symbol) — mitigated by the anchor-asserted
migration scripts and the green suite after each phase.
