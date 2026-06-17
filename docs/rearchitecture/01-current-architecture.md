# 01 — Current Architecture (kernel package)

## 1. Executive summary
`agent_workflow_kernel` is a **portable workflow-control kernel**: it coordinates
auditable workflows that mix LLM agents, deterministic scripts, human approval
gates, reviewer/doer (A2A) loops, versioned prompts, durable receipts, and
operator surfaces (Obsidian/Telegram/Markdown/Sheets). It is deliberately
**independent of any agent runtime** — OpenClaw is the first reference host, but
the kernel stays portable. Consumers drive it through a small lifecycle API and
plug runtimes/surfaces in via adapter protocols.

## 2. Repository map (kernel only; adapters/scripts out of scope)
```
packages/kernel/agent_workflow_kernel/
  __init__.py        135-name public facade (the frozen API surface)
  contracts.py       (289) foundational dataclasses/enums — the type vocabulary
  dsl.py             (324) YAML workflow loader + canonical JSON compiler
  validation.py      ( ~ ) workflow-definition validation
  policy.py          (335) policy classes, risk classes, hard gates
  prompts.py         (530) prompt registry + context packets
  receipts.py        ( ~ ) receipt provenance helpers
  lease.py           ( ~ ) stage lease policy
  sessions.py        ( ~ ) durable actor sessions
  adapters.py        (336) adapter protocols (runtime/surface/host/lane) + results
  adapter_registry.py(  ) registry resolving adapters by family/role
  storage.py         (1840) SQLite WorkflowLedger + export/DDL helpers
  runner.py          (477) adapter-neutral WorkflowRunner (owned execution loop)
  kernel.py          (3779) WorkflowKernel orchestrator + policy/budget/stage helpers
  local_runner.py    (608) local end-to-end run wiring
  local_adapters.py  (4207) local/dev/sandbox/live fakes + renderers
  parity.py          ( ~ ) fixture parity reporting
  reviewers.py       (526) automated reviewer loop
  surface_profiles.py(  ) semantic surface profile resolution
  cli.py             (  ) operator CLI (validate/compile/run-local)
```

## 3. Runtime flow (lifecycle)
The consumer-facing lifecycle on `WorkflowKernel`:
1. **`start(...)`** — create a workflow instance in the ledger from a workflow def.
2. **`run_once(...)`** — advance the instance by executing the next runnable
   stage(s): resolve effective policy, enforce budgets/guards, invoke the stage
   adapter (agent/script/surface), record receipts, compute the next transition.
   Idempotent and re-entrant (lease-guarded) — the core "tick".
3. **Human gates** — when a stage needs a human decision:
   `publish_waiting_human_gate` (render to a surface) →
   `readback_human_gate_surface` (read the surface state) →
   `ingest_human_gate_surface_decision` / `ingest_human_decision` (apply the
   decision, advance). Decisions are receipt-bound with action fingerprints.
4. Terminal status is mapped from the workflow's terminal transition.

Side effects are funneled through **adapters** (runtime invocations, surface
writes) and the **ledger** (SQLite). The kernel core itself performs no network
or provider-specific I/O.

## 4. Core abstractions
- **`contracts.py`** — the type vocabulary: `StageDef`, `Transition`,
  `AdapterResult`, `ArtifactRef`, `RiskClass`, `HardGate`, `WorkflowStatus`,
  `StageRunStatus`, `FailureClass`, runtime/surface refs. Foundational leaf.
- **`WorkflowKernel`** — the orchestrator (a single ~2,000-line class). Owns the
  run loop, policy resolution, budget enforcement, gate lifecycle.
- **`WorkflowLedger`** — durable state (instances, stage runs, receipts,
  invocations, artifacts, gate decisions) over SQLite.
- **Adapter protocols** (`adapters.py`) — `RuntimeAdapter`, `SurfaceAdapter`,
  `HostAdapter`, `LaneAdapter`; the extension points. `adapter_registry`
  resolves them by family/role.

## 5. Dependency graph (intra-kernel, acyclic)
```
contracts  (leaf; imports nothing)
  ← adapters, policy, prompts, lease, sessions, validation, dsl, runner
  ← receipts(→prompts), adapter_registry(→adapters), storage(→policy), surface_profiles
  ← kernel(→adapter_registry,adapters,dsl,lease,policy,prompts,receipts,runner,storage)
  ← local_runner(→adapters,dsl,local_adapters,runner,storage), reviewers(→storage)
  ← cli(→dsl,local_runner)
```
No cycles. `contracts` is the hub everyone depends on (healthy: it's pure types).
`kernel` is the widest consumer (9 sibling imports) — appropriate for an
orchestrator, but its 3.8k-line size mixes the class with stateless helpers.

**Test coupling:** tests import almost entirely via the top-level facade
(`from agent_workflow_kernel import ...` ×27; only 3 direct submodule imports).
⇒ Import-preserving splits are safe: keep `__init__` exports stable and tests
don't notice.

## 6. Configuration model
- Workflows are authored in **YAML**, loaded by `dsl.py`, validated by
  `validation.py`, compiled to canonical JSON. Typed into `contracts` dataclasses.
- `KernelRuntimeConfig` (frozen dataclass) carries runtime knobs.
- Policy/risk/budget config is resolved per-stage by helpers in `kernel.py`
  (`_effective_policy_for_stage`, `_resolve_budget_limit`, …).

## 7. Error model
- `LedgerConflict(RuntimeError)` for optimistic-concurrency/lease conflicts.
- `WorkflowValidationError` for bad workflow defs.
- `FailureClass` enum classifies stage failures (retriable vs terminal); retry
  logic in `kernel.py` (`_retry_result_for_adapter_failure`, `_retry_enabled`).
- Receipts capture failure provenance for audit.

## 8. Testing model
- 263 tests, unittest + pytest, almost all via the public facade — behavior-level,
  not implementation-coupled. Strong safety net for refactors.
- Fixtures under `fixtures/`, example workflows under `workflows/`.

## 9. Architectural smells (in scope)
- **S1 God-module `kernel.py` (3,779L):** one ~2,000-line class plus ~1,500 lines
  of *stateless* private helpers (policy, budget, gate, stage-execution, retry).
  The helpers are mechanically separable; the file is hard to navigate.
- **S2 God-module `local_adapters.py` (4,207L):** ~10 unrelated adapter classes
  (fakes, dry-run, sandbox Obsidian/Telegram, live Obsidian, local review) plus
  render helpers crammed into one file. Lowest cohesion in the package.
- **S3 God-module `storage.py` (1,840L):** one ~1,630-line `WorkflowLedger` class
  + export/DDL helpers; DDL/schema and row-export helpers are separable.
- **S4 Purity is unenforced:** the kernel is import-pure today, but nothing stops
  a future edit from importing a provider — the plan's Phase-1 gate (import-lint)
  does not exist yet.
- **S5 Layering implicit:** the clean acyclic layering exists but is undocumented
  and unenforced; `local_adapters` (sandbox/live surfaces) arguably belongs in a
  host/dev layer, not the vendored kernel.

## 10. Refactoring opportunities
- **Low risk / high value:** import-lint (S4); `local_adapters.py` module→package
  (S2); extract `kernel.py` trailing helpers (S1); export/DDL helper extraction
  from `storage.py` (S3); public-API doc (S5).
- **Medium / deferred:** splitting the `WorkflowKernel` and `WorkflowLedger`
  *class bodies* (mixins/delegation) — higher risk, lower reward; defer.
- **Speculative (lane-host, not here):** moving `local_adapters` out of the kernel
  into a host/dev layer; provider-boundary v2 (`invoke(...)`); single host ledger.
