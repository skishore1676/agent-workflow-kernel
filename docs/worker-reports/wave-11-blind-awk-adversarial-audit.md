# Wave 11 Blind AWK Adversarial Audit

Date: 2026-06-01

Scope: generic Agent Workflow Kernel only. This audit intentionally avoided
host-specific adapters, fixtures, lane docs, live surfaces, remote hosts, auth,
deploys, and external systems.

## Executive Recommendation

**Fix before manual E2E review.**

The kernel has a promising generic shape: typed stage definitions, a SQLite
ledger, prompt hashes, local/dry-run surfaces, and a standalone policy engine.
It is not yet robust enough to call an independent workflow harness. The
highest-risk gaps are that declared workflow policy and transition guards are
not enforced by the execution path, and stale-lease recovery can requeue a stage
after adapter work may already have started.

## Critical Findings

### C1. Execution ignores workflow/stage policy and transition guards

Evidence:
- `StageDef.policy`, workflow `policies`, and `Transition.guard` are parsed into
  contracts, but the runtime preflight builds its `ActionRequest` only from the
  adapter registration side effects and basic stage metadata
  (`packages/kernel/agent_workflow_kernel/kernel.py:787-800`).
- The stage policy is only placed into prompt/context constraints
  (`packages/kernel/agent_workflow_kernel/kernel.py:963-968`), which an adapter
  may ignore.
- `Transition.guard` is stored by the DSL
  (`packages/kernel/agent_workflow_kernel/dsl.py:104-112`), but `_advance_after_outcome`
  queues or completes transitions without evaluating the guard
  (`packages/kernel/agent_workflow_kernel/kernel.py:1231-1315`).
- The only guard hits in the generic tests/code are field definitions and local
  surface wording; there is no generic guard evaluator or guard test coverage.

Why it matters:
The design says the most restrictive policy layer wins and guarded transitions
such as approval, retry-budget, or artifact checks must fail closed. Today, a
stage can declare a high-risk policy but still run if its adapter registration
claims low risk, and a guarded transition is treated as an unguarded transition.
That is a fail-open path before any arbitrary workflow review.

Concrete fix:
Add an effective-policy compiler that merges global, workflow, stage, adapter,
and human-approval policy before invocation. Add a small allowlisted guard
registry (`policy_approved`, `within_retry_budget`, `has_required_artifacts`,
etc.) and make unknown or false guards block with a receipt. Transition advance
must evaluate guards before queueing the next stage or terminal status.

Suggested test:
Create a workflow whose runtime adapter is registered `read_only` but whose
stage policy declares an external/destructive/public effect; `run_once` must
stop before adapter invocation. Create a transition with `guard:
policy_approved`; without a matching approval receipt it must not queue the next
stage.

### C2. Stale lease recovery can replay work after adapter invocation started

Evidence:
- Claiming a queued run sets the stage status to `claimed`
  (`packages/kernel/agent_workflow_kernel/storage.py:451-511`).
- `WorkflowRunner.run_once` calls the handler immediately after claim, and
  `WorkflowKernel._handle_stage` invokes the adapter without first moving the
  stage to a `started`/`running` state
  (`packages/kernel/agent_workflow_kernel/runner.py:124-139`,
  `packages/kernel/agent_workflow_kernel/kernel.py:851-874`).
- Stale-lease sweeping treats expired `claimed` rows as safe pre-start work and
  requeues them automatically
  (`packages/kernel/agent_workflow_kernel/storage.py:996-1032`).

Why it matters:
If a runner crashes or is suspended after entering the handler but before
completion, the ledger still says `claimed`. Recovery will requeue the same
stage as if no adapter work started, even though an adapter may have performed
an external, destructive, paid, or otherwise non-idempotent effect. This breaks
the ledger's idempotency and recovery contract.

Concrete fix:
Before any adapter invocation, transactionally write `started`/`running` plus an
adapter preflight event or preflight receipt containing the idempotency key and
declared side-effect scope. Recovery should only requeue expired pre-start
claims that have no start event/invocation. Once adapter work may have begun,
default to `blocked` or adapter `recover(idempotency_key)` proof, not replay.

Suggested test:
Simulate a claimed stage that records a start event or adapter invocation, then
expires. `sweep_stale_leases` must block it with
`unknown_side_effect_state`, not requeue it. Add a separate test proving a truly
pre-start expired claim can still requeue.

## High Findings

### H1. Resumability depends on in-memory inputs and in-memory workflow definitions

Evidence:
- `WorkflowKernel` keeps `_instance_inputs` only in process memory
  (`packages/kernel/agent_workflow_kernel/kernel.py:127-128`) and repopulates it
  only during `start` (`packages/kernel/agent_workflow_kernel/kernel.py:167-174`).
- The ledger stores only `input_hash`, not the input snapshot
  (`packages/kernel/agent_workflow_kernel/storage.py:97-108`).
- Prompt context rendering reads workflow facts from `_instance_inputs`, falling
  back to `{}` after a process restart
  (`packages/kernel/agent_workflow_kernel/kernel.py:952-956`).
- The ledger does not persist canonical workflow definitions, stage definitions,
  transitions, or definition hashes; it stores only workflow id/version on the
  instance (`packages/kernel/agent_workflow_kernel/storage.py:97-108`).

Why it matters:
A durable kernel must resume after interruption with the same inputs and
definition that created the instance. Today a restarted process can render an
empty context packet for an in-flight prompt stage, or run the same
`workflow_id`/`version` against a changed in-memory graph.

Concrete fix:
Persist canonical workflow JSON, definition hash, source URI, and input snapshot
at instance start. On resume, verify the supplied in-memory workflow hash
matches the ledger or require an explicit migration stage. Context rendering
should read original inputs from the ledger, not process memory.

Suggested test:
Start a workflow, close/reopen the ledger, construct a fresh `WorkflowKernel`,
then run a prompt-backed queued stage. The rendered context must include the
original input values. A second test should prove same id/version with a
different definition hash blocks or enters a migration path.

### H2. Retry policy is not implemented as append-only stage attempts

Evidence:
- Stage retry configuration is parsed into `StageDef.retry`
  (`packages/kernel/agent_workflow_kernel/dsl.py:85-101`) but
  `WorkflowKernel._handle_stage` blocks every non-succeeded adapter result
  (`packages/kernel/agent_workflow_kernel/kernel.py:899-911`).
- `WorkflowRunner` can schedule a retry only when a handler returns `"retry"`
  (`packages/kernel/agent_workflow_kernel/runner.py:149-160`), but the kernel
  facade does not produce that decision.
- `schedule_retry` mutates the same `stage_runs` row back to `queued` and
  increments `retry_count` instead of creating a new attempt row
  (`packages/kernel/agent_workflow_kernel/storage.py:726-772`).

Why it matters:
The design says stage attempts are append-only and retry safety depends on
failure class, budget, idempotency evidence, and approval. Current behavior
either blocks without using declared retry policy or mutates one row, which
collapses attempt history and makes replay audits weaker.

Concrete fix:
Implement retry decision logic in the kernel facade: classify adapter failures,
check `StageDef.retry`, adapter `replay_safe`, idempotency proof, and policy
approval. Queue a new `StageRun` attempt with `parent_stage_run_id` while
preserving the logical idempotency key where required; never overwrite the prior
attempt's terminal evidence.

Suggested test:
Use a replay-safe adapter that fails once with a retryable runtime failure and
then succeeds. Assert there are two stage-run attempts with preserved receipts.
Use an external-effect adapter without idempotency proof and assert retry blocks
with human approval required.

### H3. Output contracts and required artifacts are not enforced before transition

Evidence:
- `StageDef.outputs` is part of the contract
  (`packages/kernel/agent_workflow_kernel/contracts.py:160-174`), but the
  runtime success path only checks adapter status and records checks
  `("adapter_registered", "policy_preflight")`
  (`packages/kernel/agent_workflow_kernel/kernel.py:883-904`).
- `_outcome_for_stage_result` accepts `outputs["outcome"]`, `next_hint`, or a
  single declared outcome without schema or artifact validation
  (`packages/kernel/agent_workflow_kernel/kernel.py:1431-1442`).

Why it matters:
An arbitrary adapter can return `succeeded` while omitting required artifact
roles, receipt fields, or schema fields, and the kernel can still transition.
That makes the kernel a dispatcher rather than a workflow harness.

Concrete fix:
Add a validation registry for declared `outputs.outcome_schema`, required
artifact roles, receipt kinds, and required fields. Treat validation failures as
`invalid_output`, preserve the receipt, and do not transition.

Suggested test:
Define a stage requiring an output artifact role and schema field. Make the
adapter return `succeeded` without them. The stage must become `invalid_output`
or `blocked`, no next stage should be queued, and the receipt should name the
missing contract fields.

### H4. Direct human-decision ingestion is not bound to the configured canonical source

Evidence:
- Direct ingestion validates that `human_ref` and `canonical_surface` are
  non-empty, but does not compare them to a workflow/stage-declared canonical
  source or expected human (`packages/kernel/agent_workflow_kernel/kernel.py:1457-1488`).
- Surface ingestion converts adapter outputs into a `HumanApprovalReceipt`, but
  the final direct validation still only checks gate id, exact action,
  fingerprint, expiry, and revocation
  (`packages/kernel/agent_workflow_kernel/kernel.py:1626-1673`).

Why it matters:
The policy design says approvals must come from the configured canonical human
source and surface disagreements must block. Today a receipt with the correct
fingerprint and gate id but the wrong surface or human can pass the kernel-level
check.

Concrete fix:
Add canonical decision source and authorized human/role fields to the workflow
or human-gate stage. Validate `canonical_surface`, `human_ref`, and
decision-source provenance during `ingest_human_decision`. If a non-canonical
surface exists, preserve evidence and block for a fresh canonical decision.

Suggested test:
Put `canonical_surface: surface.a` and `human_ref: owner` on a human gate.
Attempt direct ingestion with the right fingerprint but `surface.b` or another
human. It must block and must not queue the next stage.

## Medium Findings

### M1. Non-runtime adapter families exist but the main kernel cannot execute them

Evidence:
- The registry maps human gates to surface adapters, host refs to host adapters,
  runtime refs or agent stages to runtime adapters, and otherwise defaults to
  lane adapters (`packages/kernel/agent_workflow_kernel/adapter_registry.py:108-119`).
- `WorkflowKernel._handle_stage` blocks any non-human stage whose resolved
  registration is not `runtime`
  (`packages/kernel/agent_workflow_kernel/kernel.py:774-779`).
- Host and lane adapter protocols plus local fakes exist
  (`packages/kernel/agent_workflow_kernel/adapters.py:277-336`), but they are
  only unit-tested directly, not routed by the kernel execution path.

Why it matters:
Generic workflows need deterministic system actions, host operations, lane
translation, and validation adapters. Requiring everything to be disguised as a
runtime adapter narrows the kernel before arbitrary workflow review.

Concrete fix:
Add family-specific dispatch for `system_action`, `wait_schedule`, `recovery`,
host, and lane adapter operations, with the same invocation, policy, receipt,
and validation envelope.

Suggested test:
Run a `system_action` stage with a local lane adapter and a host healthcheck
stage with a host adapter through `WorkflowKernel.run_once`. Both should record
adapter invocations and receipts without routing through `RuntimeAdapter.invoke`.

### M2. Prompt registry records status but does not enforce lifecycle state

Evidence:
- Prompt records carry a `status` field
  (`packages/kernel/agent_workflow_kernel/prompts.py:37-48`), and resolved
  prompts preserve it (`packages/kernel/agent_workflow_kernel/prompts.py:241-248`).
- `PromptRegistry.resolve` resolves by exact key and returns the bundle without
  checking whether the record is active, deprecated, blocked, or otherwise
  allowed (`packages/kernel/agent_workflow_kernel/prompts.py:200-216`).

Why it matters:
Prompt provenance is strong on hashes, but weak on lifecycle control. A
deprecated or disabled prompt can still execute as long as the file hash
matches, which undermines versioning and prompt rollback safety.

Concrete fix:
Define allowed prompt statuses and enforce them at resolve time. Default to
blocking inactive/deprecated/revoked prompt versions unless a workflow migration
or compatibility flag explicitly permits them and records that exception.

Suggested test:
Add a registry entry with `status: deprecated` or `status: revoked` and a stage
that references it. Resolution should block before adapter invocation and record
a prompt-context failure receipt.

### M3. Readback is not a hard prerequisite for all decision-ingest paths

Evidence:
- The owned runner performs readback before ingest only when
  `publish_human_gate=True` (`packages/kernel/agent_workflow_kernel/runner.py:269-304`).
- Direct `ingest_human_gate_surface_decision` resolves the most recent
  published surface ref and ingests from it without checking that a successful
  readback event exists first
  (`packages/kernel/agent_workflow_kernel/kernel.py:621-639`).

Why it matters:
The surface contract says publish without readback is attempted delivery, not
confirmed operator visibility. A recovery run that ingests an existing surface
without readback can treat an unseen or stale surface as an authoritative
decision source.

Concrete fix:
Make `readback_required` an enforced gate. Decision ingest should require a
successful readback receipt after the latest publish and before the decision
timestamp or source revision, unless the surface contract explicitly says
readback is not required.

Suggested test:
Publish a human gate, edit/check a decision, then call ingest without any
readback. It must block with `readback_required`. After readback succeeds, the
same decision may be ingested.

## Low Findings

### L1. DSL validation allows ambiguous transition tables

Evidence:
- `WorkflowKernel` builds a dict keyed by `(from_stage, outcome)`, so duplicate
  transition edges silently overwrite earlier edges
  (`packages/kernel/agent_workflow_kernel/kernel.py:129-132`).
- `validate_workflow_mapping` checks referenced stages, outcomes, and
  `to`/`terminal` exclusivity, but does not reject duplicate transition keys or
  unknown guard names (`packages/kernel/agent_workflow_kernel/validation.py:87-124`).

Why it matters:
Duplicate edges and unknown guards are review hazards in YAML-authored
workflows. A human may review one edge while the kernel executes the last one in
the file.

Concrete fix:
Reject duplicate `(from, on)` transitions at validation time. Once a guard
registry exists, reject unknown guard names during validation or require an
explicit compatibility mode.

Suggested test:
Load a workflow with two transitions from the same stage on the same outcome and
assert validation fails. Load a workflow with `guard: typo_guard` and assert it
fails closed.

## Readiness Scores

| Design principle | Score | Notes |
| --- | ---: | --- |
| Workflow graph | 5/10 | Basic stages and transitions work, but guards, duplicate detection, and output-contract-aware transitions are missing. |
| Prompt registry | 6/10 | Hash/provenance path is useful; lifecycle status, migration semantics, and resume input durability are incomplete. |
| Work Ledger | 5/10 | SQLite transactions, events, receipts, and leases exist; schema/version storage, append-only retries, input snapshots, and safe stale-lease semantics need work. |
| Runner | 4/10 | Single-step and owned local human-gate loop work; recovery, retry, started-state tracking, and non-runtime dispatch are not ready. |
| Surface adapters | 6/10 | Local/dry-run publish/readback/decision ingest are solid scaffolding; canonical source binding and readback enforcement are incomplete. |
| Policy layer | 4/10 | The standalone engine handles fingerprints and hard-gate classes, but integration ignores stage/workflow policy and transition guards. |

## Testing And Operational Gaps Before Manual Review

- Add guard enforcement tests, especially `policy_approved`,
  `within_retry_budget`, and missing/unknown guard fail-closed behavior.
- Add crash/recovery tests around adapter invocation start, receipt-written but
  completion-missing, and idempotent adapter recovery.
- Add restart/resume tests using a fresh process/kernel object and the same
  SQLite ledger.
- Add schema/artifact validation tests that block missing required outputs.
- Add non-runtime adapter execution tests for lane, host, recovery, and
  deterministic system-action stages.
- Add canonical human source tests, surface disagreement tests, and readback
  prerequisite tests.
- Add migration tests for ledger schema version and workflow definition hash
  compatibility.

## Final Recommendation

Do not proceed to manual E2E review as an independent generic kernel yet.
Proceed after the critical and high findings are fixed and covered by tests.
