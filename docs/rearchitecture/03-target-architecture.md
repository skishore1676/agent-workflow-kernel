# 03 — Target Architecture (ADR)

## Problem statement
The kernel is the one asset the approved `lane-host` program vendors in. Before it
is vendored it should be: (a) **purity-enforced** (the agnosticism invariant the
program depends on), (b) **navigable** (no 3–4k-line god-modules), and (c)
**documented** (an explicit public surface). None of these hold today, though the
underlying boundaries are already sound.

## Design goals
- Enforce kernel purity mechanically (regression-proof the agnosticism invariant).
- Make every kernel file navigable (cohesive modules, ~≤1,500 lines except the
  irreducible orchestrator/ledger class bodies).
- Document the public API surface.
- **Zero behavior change. 263 tests green at every step. Public imports frozen.**

## Non-goals (explicitly deferred)
- Rewriting adapters/scripts/runtime (this repo is an archive; that lands in
  lane-host).
- Splitting the `WorkflowKernel` / `WorkflowLedger` **class bodies** via
  mixins/delegation (higher risk, lower reward).
- Behavior changes: the surface-method dedup (`_surface_op`), the broad
  `except Exception` failure-class gap, and the implicit `state`-dict contract are
  **logged as follow-ups**, not touched here.
- Moving `local_adapters` out of the kernel package (a lane-host layering call).

## Target module structure (kernel package)
```
agent_workflow_kernel/
  __init__.py            frozen 135-name public facade (unchanged surface)
  contracts.py           (unchanged) type vocabulary
  kernel.py              WorkflowKernel class + dataclasses ONLY (~2.2k → goal)
  _internal_types.py     NEW: shared private dataclasses
                         (_PolicyComponents,_BudgetLimit,_EffectivePolicy,
                          _GuardDecision,_TransitionResult)
  _policy.py             NEW: policy resolution + budget helpers
  _gates.py              NEW: human-gate + outcome helpers
  _stages.py             NEW: stage adapter-invoke + contract + inputs + queries
  local_adapters/        NEW package (was local_adapters.py 4207L):
    __init__.py            re-exports every public name (import-preserving)
    fakes.py               LocalFake{Runtime,Surface,Host,Lane}Adapter
    dry_run.py             DryRunSurfaceAdapter + Obsidian/Telegram/Sheets
    sandbox.py             Sandbox{Obsidian,Telegram} adapters
    live.py                LiveObsidianMarkdownSurfaceAdapter
    review.py              LocalMarkdownHumanReviewSurfaceAdapter
    _render.py             shared render/extract/redact helpers
  storage.py             WorkflowLedger + (optionally) _ledger_export.py helpers
  tools/import_lint.py    NEW (repo tools/): kernel purity checker
```

## Core invariant: kernel purity (the import-lint)
`packages/kernel/agent_workflow_kernel/**` must not import from any provider/
adapter sibling distribution:
`agent_workflow_kernel_{openclaw,codex_cli,codex_sdk,a2a,x_digest,ivy,artifact_validation}`.
Enforced by an `ast`-based checker wired into `make check`, with a test that
plants a violation and asserts the linter fails (so the guard itself is tested).

## Migration strategy (all import-preserving)
1. **Facade for `local_adapters`:** convert the module to a package whose
   `__init__.py` re-exports the exact same names. Every `from
   agent_workflow_kernel import X` and `from agent_workflow_kernel.local_adapters
   import X` keeps working.
2. **Helper extraction for `kernel.py`:** move shared private dataclasses to
   `_internal_types.py`, move trailing stateless helpers to `_policy/_gates/_stages`,
   and re-import them into `kernel.py`. Internal call sites unchanged; no external
   call site references these privates.
3. **Tests green between every slice.** Each module extraction is its own commit.

## Validation strategy
- `./scripts/check.sh` (263) after each slice and at the end.
- New `tests/test_kernel_import_lint.py`: passes on clean tree; fails on a planted
  violation (proves the guard works).
- Public-surface assertion: `len(agent_workflow_kernel.__all__) == 135` and a
  smoke import of every name (a tiny test) to prove the facade is intact.

## Risks & mitigations
- **Dropped/renamed symbol in a split** → import-preserving facades + full suite +
  a `__all__` count/import smoke test. Low.
- **Circular import** from helper extraction → shared dataclasses isolated in
  `_internal_types.py`; helper modules import only `contracts`/`_internal_types`/
  peers, never `kernel`. Medium, mitigated by incremental test runs.
- **Scope creep into the archive** → fenced by the confirmed scope (A1).

## Alternatives considered
1. **Minimal local cleanup** (just the import-lint): leaves the god-modules. Too
   little — DX and vendoring readiness unaddressed.
2. **Full in-place repo rewrite** (the prompt's literal template): conflicts with
   the approved fresh-repo direction; most output discarded. Rejected (confirmed
   with Suman).
3. **Split the class bodies** (mixins/delegation for WorkflowKernel/Ledger):
   high risk to a 2k-line stateful class, low marginal benefit. Deferred.
4. **Move `local_adapters` out of the kernel entirely:** correct end-state but a
   lane-host layering decision; would churn test imports for no in-repo gain.
   Deferred to lane-host.
5. **Adapter/facade migration (chosen):** import-preserving module→package and
   helper-extraction behind re-exports. Safe, incremental, carries forward.

## Decision
Adopt **Alternative 5**: import-preserving extraction + a tested purity lint +
an API doc. It is the minimum-viable re-architecture that makes the kernel
vendor-ready, is provably behavior-identical (263 green, frozen surface), and
directly advances the approved lane-host Phase-1 acceptance gate.
