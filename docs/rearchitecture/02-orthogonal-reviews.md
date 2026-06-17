# 02 — Orthogonal Reviews (synthesis)

Seven perspectives on the kernel package. Two were run as independent read-only
review agents (design/lifecycle; testing/coverage); the rest are structured
passes over the same evidence. Scope: `packages/kernel/agent_workflow_kernel/`.

## 2.1 Architecture mapping
- **Layering is already clean & acyclic** (see `01-current-architecture.md` §5),
  `contracts` as the pure-types hub. No dependency inversion needed.
- **Cohesion failures are file-level, not boundary-level:** `kernel.py`,
  `local_adapters.py`, `storage.py` each bundle multiple concerns in one file.
- Action: split files along existing seams; do **not** re-wire module boundaries.

## 2.2 Runtime & lifecycle
- `run_once` is **idempotent and re-entrant**: stale-lease sweep →
  `claim_next_queued_run` with a lease resolver; every mutation carries a
  `lease_token`, so a stale worker's write raises `LedgerConflict`
  (`runner.py:130-219`, `storage.py:~1629`).
- Transitions are an **explicit** decision→status ladder (`kernel.py:305-409`).
- **Implicit-ordering smell:** `run_once` threads a shared `state` dict that
  `_handle_stage` populates as a side effect (keys `adapter_result`,
  `failure_summary`, `receipt_id`). Works, but is the main hidden contract.
  *Out of scope for behavior-preserving refactor; logged as follow-up.*

## 2.3 API & abstraction
- Core lifecycle (`start` / `run_once` / `ingest_human_decision`) is tight.
- The three surface methods (`publish_waiting_human_gate` /
  `readback_human_gate_surface` / `ingest_human_gate_surface_decision`) have
  near-duplicate ~100-line bodies → a shared `_surface_op` helper is a clean
  future simplification (behavior-preserving, **not** in this lane).
- Naming nit: `ingest_human_decision` vs `ingest_human_gate_surface_decision`
  (direct vs surface-routed) — document the distinction in the API doc.

## 2.4 Testing & validation
- **Well covered:** ledger/runner (`test_sqlite_ledger_runner`), run_once
  (`test_workflow_kernel_run_once`, 400+L), policy/gates (`test_policy_engine`),
  dsl/contracts (`test_core_schema_dsl`, `test_contracts`), sessions, surface
  profiles.
- **Thin:** budget/timeout depth, adapter-exception/error paths, `local_adapters`
  internal render helpers, lease policy.
- **~93% facade imports** (27/29) → import-preserving splits are safe. The 2 deep
  imports are `.dsl` / `.validation` (untouched).
- **No brittle private-attribute or file-layout assertions found.** Low refactor
  risk. Largest integration test (`test_workflow_kernel_run_once`) exercises
  run_once end-to-end — the key regression guard.

## 2.5 Reliability & observability
- Typed failures: `FailureClass` (10 variants), `LedgerConflict`. Retry helpers
  correctly distinguish `UNKNOWN_SIDE_EFFECT_STATE` vs `RUNTIME_FAILURE`.
- **Silent-failure risk:** a broad `except Exception` (~`kernel.py:1159`) collapses
  all adapter errors into one summary string without failure-class
  differentiation. Logged as a follow-up (behavior change → out of scope).

## 2.6 Developer experience
- `make setup` / `make check` work; README documents CLI entrypoints.
- **Biggest DX drag is navigability:** a contributor opening `kernel.py` (3.8k) or
  `local_adapters.py` (4.2k) cannot find anything. Splitting these is the single
  highest DX win and the core of this lane.
- Missing: a documented "this is the public API" surface. Added in this lane.

## 2.7 Simplification / clean-sheet
- **Survives a rewrite:** `contracts`, `WorkflowKernel` lifecycle, `WorkflowLedger`,
  adapter protocols, policy/budget model, receipts. This is the irreducible core
  worth vendoring.
- **Would disappear / move:** `local_adapters` (sandbox/live surfaces belong in a
  host/dev layer, not the vendored kernel); compatibility cargo for legacy hosts.
- **Essential vs accidental:** the lifecycle and ledger are essential; the
  4.2k-line surface-adapter pile and the 1.5k-line helper tail are accidental
  *packaging*, not accidental *logic* — so split, don't rewrite.

## Synthesis
- **Agreement:** boundaries are good; the problem is oversized files and an
  unenforced purity invariant. Splits must be import-preserving; tests are a
  strong net.
- **Disagreement / tension:** how far to split `kernel.py` helpers (one helper
  module vs several). Resolution: extract incrementally, tests green between each,
  starting from the safest cut.
- **Design constraints:** behavior identical; 263 green per phase; public surface
  frozen; shared private dataclasses → `_internal_types` to dodge circular imports.
- **Priorities:** (1) import-lint, (2) `local_adapters` package, (3) `kernel.py`
  helper extraction, (4) `storage.py` helpers + API doc.
