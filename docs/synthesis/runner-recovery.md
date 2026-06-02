# Runner, Storage, And Recovery

## Purpose

The runner is the kernel's durable executor. It turns a `WorkflowInstance` and
its pending `StageRun`s into receipts, transitions, human gates, blocked states,
or retryable work without depending on any one host runtime.

SQLite is the operational ledger. JSON is the interchange and evidence format:
context packets sent to workers, immutable receipt exports, recovery snapshots,
fixtures, and host-specific read models. Agents, scripts, and host adapters
should use a CLI or adapter API; they should not mutate raw SQL directly.

## Design Commitments

- The kernel owns workflow state, stage attempts, leases, retry budgets,
  validation results, receipts, child-session audit records, and recovery state.
- Host adapters own concrete process/session mechanics such as Codex thread
  creation, OpenClaw session dispatch, browser automation, launchd, or Telegram.
- Every execution attempt produces either a receipt or an explicit recovery
  record explaining why a receipt could not be trusted.
- Claiming work is atomic. Completing work is append-only first, state update
  second.
- External effects require idempotency evidence or human approval before retry.

## Runner Loop

The minimal runner loop is:

1. Open SQLite with foreign keys enabled and WAL mode preferred.
2. Sweep expired leases and stale child sessions into recovery analysis.
3. Atomically claim one eligible `stage_runs` row with a fresh lease token.
4. Write an `attempt_started` event and, when useful, a preflight receipt with
   the idempotency key and allowed scope.
5. Render the prompt, context packet, script input, or human gate packet from
   immutable definitions and current instance state.
6. Invoke exactly one adapter: runtime, surface, host, or deterministic script.
7. Record the adapter invocation and raw status.
8. Run validation hooks: schema checks, artifact checks, policy checks,
   deterministic tests, and optional reviewer verdict checks.
9. Write receipts and artifact references before moving the stage.
10. Transition the workflow instance to the next stage, a human gate, retry,
    blocked, or done.

The runner may process a batch, but each stage claim and completion must be
transactional so another runner can resume after interruption.

The owned kernel runner path is the generic batch loop over these primitives:
it discovers the next queued instance from the ledger, calls the kernel stage
stepper until no automatic stage remains, and treats waiting human gates as
first-class resumable work. When configured for local review surfaces, it
publishes a review packet through the registered surface adapter, reads that
surface back, ingests exactly one structured human decision, and then resumes
the workflow from the transition selected by that decision. Reruns reuse an
already published waiting-gate surface instead of creating duplicate review
notes, so an interruption between publish and decision ingest is recoverable
from ledger events alone.

## Stage Statuses

Recommended `StageRun.status` values:

| Status | Meaning |
| --- | --- |
| `queued` | Eligible when dependencies and policy preconditions are met. |
| `leased` | Claimed by a runner but adapter work has not started. |
| `running` | Adapter invocation or deterministic script is active. |
| `waiting_on_child` | A delegated child session owns the next response. |
| `validating` | Output exists and validators are running. |
| `waiting_on_human` | Human decision or approval is required. |
| `waiting_on_dependency` | External dependency is unavailable or delayed. |
| `retry_scheduled` | Safe retry is planned after backoff. |
| `succeeded` | Stage output and receipts passed validation. |
| `rejected_human` | Human rejected the work or requested non-automatic revision. |
| `denied_policy` | Policy gate denied the action without approval. |
| `failed_runtime` | Runtime or adapter failed before trusted output. |
| `invalid_output` | Output failed contract validation. |
| `failed_test` | Deterministic validation or fixture test failed. |
| `blocked` | No safe automatic path remains. |
| `canceled` | Superseded or intentionally stopped. |

`WorkflowInstance.status` can be smaller: `running`, `waiting_on_human`,
`waiting_on_dependency`, `blocked`, `succeeded`, `failed`, `canceled`.

## Failure Taxonomy

The runner must classify failures before deciding whether retry is safe.

| Failure class | Definition | Default retry posture | Human approval required when |
| --- | --- | --- | --- |
| `runtime_failure` | Adapter crash, process exit, timeout, context overflow, gateway error, worker interruption, or unreadable transport result before a trusted output exists. | Retry if the stage is side-effect-free or protected by an idempotency key, the lease expired cleanly, and retry budget remains. | The prior attempt may have caused an external effect, the adapter state is unknowable, or retry budget is exhausted. |
| `invalid_output` | Output was produced but violates schema, required fields, artifact existence, receipt shape, or declared stage contract. | Retry if no external effect occurred and the error is likely repairable by re-prompting or regenerating. | Repeated invalid output suggests ambiguous instructions, creative judgment, or contract mismatch. |
| `human_rejection` | A human explicitly rejected, requested revision, or withheld approval. | Do not replay the same attempt automatically. Create a new attempt only from the human's requested revision path. | Always. The human decision is the source of truth unless superseded by a newer human decision. |
| `policy_denial` | Risk policy forbids or pauses the action: public publish, deploy, live trade, auth, money, external send, destructive cleanup, high-cost compute, or host-specific restricted action. | No automatic retry. | Always, unless a policy/config change has already been approved and recorded. |
| `dependency_unavailable` | Required service, model, file, credential, market data, browser session, surface, host, or network dependency is unavailable. | Retry with backoff when the operation is read-only or idempotent and the dependency is expected to recover. | Auth repair, credential changes, paid services, broker/live systems, or persistent outage needs operator action. |
| `deterministic_test_failure` | A verifier, unit test, fixture comparison, policy test, or deterministic check fails after output exists. | Retry only after a material input, code, config, or artifact change. Blind rerun is not useful. | The failed check protects a human gate or external effect, or repeated failure leaves uncertain correctness. |

Unknown failures should be treated as `runtime_failure` only when no output or
side effect is observable. If side-effect state is uncertain, classify as
`blocked` with `human_required=true`.

## Retry And Approval Rules

Retries are safe only when all of these are true:

- The stage declares `retry_policy.enabled=true`.
- Attempt count is below `max_attempts`.
- The failure class is retryable under the table above.
- The adapter declares the operation replay-safe, cancellable, or idempotent.
- The runner can prove no external effect occurred, or the same idempotency key
  will dedupe the effect.
- Required artifacts and receipts from prior attempts are preserved.
- The next attempt has a distinct `stage_run_id` or incremented `attempt`
  number, while preserving the same logical idempotency key when appropriate.

Human approval is required before retry when:

- The action is in a policy zone requiring explicit approval.
- The prior attempt may have partially completed an external effect.
- A human rejected or revised the work.
- The retry would mutate auth, secrets, money, trades, production systems,
  public publishing, external sends, or destructive cleanup.
- The failure is repeated and suggests ambiguity rather than transient failure.
- Recovery cannot audit a delegated child session or adapter invocation.

Backoff should be stored in the database, not hidden in runner memory:
`retry_after_at`, `retry_count`, `last_failure_class`, and
`last_failure_summary`.

## Minimal SQLite Sketch

This is a portability sketch, not a final migration file.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE workflow_instances (
  instance_id TEXT PRIMARY KEY,
  workflow_def_id TEXT NOT NULL,
  workflow_version TEXT NOT NULL,
  status TEXT NOT NULL,
  current_stage_id TEXT,
  idempotency_key TEXT,
  input_hash TEXT NOT NULL,
  recovery_epoch INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE stage_runs (
  stage_run_id TEXT PRIMARY KEY,
  instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
  stage_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  status TEXT NOT NULL,
  failure_class TEXT,
  failure_summary TEXT,
  approval_required INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT,
  input_hash TEXT NOT NULL,
  output_hash TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  retry_after_at TEXT,
  lease_owner TEXT,
  lease_token TEXT,
  lease_expires_at TEXT,
  parent_stage_run_id TEXT REFERENCES stage_runs(stage_run_id),
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(instance_id, stage_id, attempt)
);

CREATE TABLE runner_leases (
  lease_id TEXT PRIMARY KEY,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  lease_token TEXT NOT NULL,
  status TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  UNIQUE(resource_type, resource_id)
);

CREATE TABLE adapter_invocations (
  invocation_id TEXT PRIMARY KEY,
  stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id),
  adapter_family TEXT NOT NULL,
  adapter_name TEXT NOT NULL,
  external_id TEXT,
  status TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  response_hash TEXT,
  error_class TEXT,
  error_summary TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE child_sessions (
  child_session_id TEXT PRIMARY KEY,
  parent_stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id),
  invocation_id TEXT REFERENCES adapter_invocations(invocation_id),
  source_thread_id TEXT,
  external_session_id TEXT,
  delegate_kind TEXT NOT NULL,
  delegate_owner TEXT NOT NULL,
  goal_hash TEXT NOT NULL,
  context_packet_hash TEXT NOT NULL,
  allowed_scope_json TEXT NOT NULL,
  expected_receipts_json TEXT NOT NULL,
  status TEXT NOT NULL,
  audit_status TEXT NOT NULL,
  transcript_ref TEXT,
  last_seen_at TEXT,
  deadline_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE receipts (
  receipt_id TEXT PRIMARY KEY,
  instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
  stage_run_id TEXT REFERENCES stage_runs(stage_run_id),
  receipt_kind TEXT NOT NULL,
  actor TEXT NOT NULL,
  status TEXT NOT NULL,
  failure_class TEXT,
  summary TEXT NOT NULL,
  inputs_json TEXT NOT NULL,
  outputs_json TEXT NOT NULL,
  checks_json TEXT NOT NULL,
  artifacts_json TEXT NOT NULL,
  policy_json TEXT NOT NULL,
  receipt_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE artifact_refs (
  artifact_id TEXT PRIMARY KEY,
  stage_run_id TEXT REFERENCES stage_runs(stage_run_id),
  uri TEXT NOT NULL,
  role TEXT NOT NULL,
  media_type TEXT,
  sha256 TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE approvals (
  approval_id TEXT PRIMARY KEY,
  stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id),
  gate_id TEXT NOT NULL,
  risk_class TEXT NOT NULL,
  status TEXT NOT NULL,
  approver TEXT,
  decision_source TEXT,
  decision_ref TEXT,
  decision_summary TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT
);

CREATE TABLE workflow_events (
  event_id TEXT PRIMARY KEY,
  instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
  stage_run_id TEXT REFERENCES stage_runs(stage_run_id),
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_stage_runs_claim
  ON stage_runs(status, retry_after_at, lease_expires_at, updated_at);
CREATE INDEX idx_child_sessions_audit
  ON child_sessions(status, audit_status, deadline_at, updated_at);
CREATE INDEX idx_receipts_stage
  ON receipts(stage_run_id, created_at);
```

`runner_leases` can be collapsed into lease columns on `stage_runs` for a tiny
implementation. Keeping it separate makes child-session and workflow-level
locks easier without inventing host-specific lock files.

## Atomic Claim Semantics

A runner claims work by updating one eligible row in a transaction:

- `status in ('queued', 'retry_scheduled')`
- `retry_after_at is null or retry_after_at <= now`
- dependencies are satisfied
- no active lease exists, or the lease is expired
- policy preconditions are met

The update sets `status='leased'`, `lease_owner`, `lease_token`,
`lease_expires_at`, and `updated_at`. Every later write must include the same
lease token. If the token no longer matches, the runner must stop and write a
local diagnostic rather than overwriting newer state.

Long-running adapters heartbeat the lease. If they cannot heartbeat, they must
write enough invocation metadata first for recovery to inspect the external
state.

## Idempotency

Each workflow instance may have a top-level idempotency key. Each stage run may
also have a stage-specific key derived from:

```text
workflow_def_id + workflow_version + instance_id + stage_id + logical_input_hash
```

Adapters that can cause side effects must either accept this key or expose a
dedupe/readback mechanism. If neither is true, the stage must be guarded by a
human gate before execution and by human review before retry after uncertainty.

The runner should record idempotency data in both the database and receipts so
JSON exports remain auditable outside SQLite.

## Validation Hooks

Validation is a stage of execution, not optional logging.

- Shape validation: required output fields, JSON schemas, receipt schemas.
- Artifact validation: referenced paths or object URIs exist and hashes match.
- Policy validation: requested action fits allowed scope and approval state.
- Deterministic validation: unit tests, fixture comparisons, static checks, or
  command exit codes.
- Human verdict validation: expected decision source, approver identity, and
  decision timestamp are present.
- Child-session validation: delegated output has a matching parent id, goal
  hash, transcript reference, and final receipt.

Validation failure never erases the produced output. It writes a receipt with
`status='fail'`, classifies the failure, and transitions according to retry and
approval rules.

## Recovery After Supervisor Or Thread Interruption

Recovery is a normal runner mode. A new supervisor, runner process, or host
thread should be able to resume from SQLite and receipt exports without access
to the interrupted process memory.

On startup:

1. Sweep expired `runner_leases`.
2. For `leased`, `running`, `validating`, and `waiting_on_child` runs with an
   expired lease, load the latest `adapter_invocations`, `child_sessions`,
   receipts, and artifacts.
3. If a trusted terminal receipt exists, import it, mark the stage terminal,
   and continue transitions.
4. If the adapter exposes readback by `external_id`, inspect it before retry.
5. If a child session is still active and within deadline, reattach by writing
   a fresh lease and keep waiting.
6. If the child session completed, audit transcript and receipts before
   accepting output.
7. If state is unknowable but the action was side-effect-free, schedule retry.
8. If side effects are possible or audit evidence is missing, mark `blocked`
   with `approval_required=1`.

Supervisor interruption is handled the same way as worker interruption. The
supervisor's source thread id and goal packet hash should be recorded in
`workflow_events` and, for delegated work, in `child_sessions.source_thread_id`.
If the original thread disappears, the next supervisor reads the current ledger,
loads incomplete stage runs, and either reattaches to child sessions through the
host adapter or blocks with a specific missing-audit receipt.

## Child-Session And Delegated-Work Auditing

Any child session, subagent, delegated Codex thread, OpenClaw session, or
agent-to-agent reviewer/doer loop must be represented in `child_sessions`.

The parent stage must record:

- source thread id and external child/session id when available;
- delegate kind and owner;
- goal packet hash and context packet hash;
- allowed scope and forbidden actions;
- expected receipt kinds;
- deadline and budget;
- transcript reference or adapter readback pointer.

The child must return a receipt that includes:

- parent `stage_run_id`;
- child session id or external session id;
- artifacts created or inspected;
- checks run;
- side effects requested or performed;
- residual risk and next action.

The audit job flags:

- `stale_delegate`: no heartbeat, transcript update, or receipt by deadline;
- `orphan_output`: child output exists without a matching parent stage;
- `missing_receipt`: terminal child session lacks a receipt;
- `scope_violation`: child requested or performed actions outside allowed
  scope;
- `unverified_artifact`: child references artifacts that cannot be read or
  hashed;
- `thread_interrupted`: source or child thread ended before terminal receipt.

Delegated output is not accepted just because a child thread says it is done.
The parent runner must verify the child receipt, transcript pointer, artifacts,
and policy scope before transitioning the parent stage.

## JSON Export And Import

The kernel should export:

- context packets passed to workers;
- receipt packets for each attempt;
- artifact manifests;
- child-session audit packets;
- recovery snapshots for debugging or database rescue;
- host read models for surfaces such as Obsidian, Telegram, Sheets, or local
  Markdown.

Imports should be append-only and hash-checked. A JSON receipt can complete a
stage only if it references a known `stage_run_id` or accepted external
correlation id, has a valid receipt hash, satisfies schema validation, and does
not conflict with a newer terminal database state.

## First Implementation Slice

A small useful v0 can ship with:

- SQLite schema for instances, stage runs, leases, invocations, child sessions,
  receipts, artifacts, approvals, and events.
- CLI commands: `init`, `enqueue`, `claim`, `heartbeat`, `complete`, `fail`,
  `sweep`, `audit-children`, `export-receipts`, and `import-receipt`.
- One deterministic script adapter and one mock child-session adapter.
- Fixture tests for each failure class and retry decision.
- A recovery test that kills a runner after claim and proves another runner can
  classify, retry, reattach, or block according to evidence.

This is enough to satisfy the architecture gate without baking OpenClaw paths
or lane names into the portable kernel.
