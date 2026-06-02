# Kernel Durable Actor-Session Model

Date: 2026-06-01

## Summary

AWK should model durable actor sessions as kernel-owned session bindings, not as
lane-specific runtime conveniences. The kernel should choose, reuse, replace,
and audit sessions by stable workflow/program scope plus actor/profile binding;
runtime adapters should own only the concrete host session mechanics such as
Codex thread ids, OpenClaw session keys, browser sessions, or future task-flow
handles.

The default should be conservative: reuse the same actor session inside one
`WorkflowInstance` whenever the actor binding remains valid. Reuse across
recurring workflow occurrences should require an explicit `program_instance`
session scope so Jarvis weekly, Radhe, trading research programs, and future
lanes do not accidentally inherit stale context from one another.

This report adds a small portable key helper and tests for the canonical
`actor_session_key` contract. It does not add a storage migration or runtime
reattachment behavior; those should be a follow-on implementation slice after
the supervisor accepts the model.

## Model Objects

Durable actor sessions should sit between `StageRun` and `AdapterInvocation`:

```text
WorkflowDef
  -> WorkflowInstance or RecurringProgramInstance
  -> ActorSessionBinding
  -> ActorSession generation
  -> StageRun usage
  -> AdapterInvocation
  -> Receipt
```

Recommended kernel records:

- `actor_sessions`: durable session identity and lifecycle.
- `actor_session_uses`: one row per `StageRun` that selected, reused, replaced,
  or rejected a session.
- `actor_session_events`: append-only audit events for selection, heartbeat,
  compaction, corruption, stale checks, replacement, reattachment, and closure.

`child_sessions` remains useful for delegated child work, but it is not enough:
ordinary actor continuity across stages and retries needs a parent-owned actor
session even when no child thread was spawned.

## Scope

There are two supported scopes.

### Workflow-Instance Sessions

`workflow_instance` scope is the default. The same logical actor should reuse
one session across stages, retries, and reviewer loops inside one
`WorkflowInstance` when validation passes.

Examples:

- Ivy writer reused for `build_draft_package` and `revise_draft` inside one
  editorial instance.
- Jonah/editor reviewer reused inside the same editorial review loop, separately
  from Ivy's writer session.
- Bumblebee producer and reviewer each get their own sessions in one quality
  review instance.
- Trading researcher and risk reviewer sessions stay inside the read-only
  research gate instance and do not bleed into execution workflows.

### Recurring Program Sessions

`program_instance` scope is explicit opt-in. It supports a long-lived actor for
a recurring program whose identity and mandate are stable across workflow
occurrences.

Examples:

- Jarvis weekly/improvement can reuse a Jarvis owner session across weekly
  occurrences when the program contract and prompt/profile binding are
  unchanged.
- Radhe may reuse a reviewer or owner-quality session across scheduled runs if
  the lane declares a stable program instance and readback validation is green.
- Future research programs can reuse an analyst session across read-only
  research occurrences while still requiring a separate workflow for live trade
  or money actions.

Program sessions must not be inferred from workflow id alone. The workflow or
host adapter should provide a stable `program_id` and `program_instance_id`, and
the kernel should record which workflow occurrence used the session in
`actor_session_uses`.

## Canonical `actor_session_key`

The portable key format is:

```text
ask:v1:<64 lowercase hex chars>
```

The 64 hex chars are:

```text
sha256(canonical_json(actor_session_binding)).removeprefix("sha256:")
```

The canonical binding payload is:

```json
{
  "schema_version": "actor-session-key.v1",
  "scope": "workflow_instance | program_instance",
  "scope_id": "workflow instance id or program instance id",
  "workflow_id": "portable workflow id",
  "workflow_version": "exact workflow version for workflow_instance; null for cross-version program reuse",
  "program_id": "required for program_instance; null for workflow_instance",
  "actor_ref": "actors.writer",
  "adapter_id": "runtime.agent",
  "runtime_namespace": "default | host/runtime namespace",
  "profile_binding_digest": "sha256:<64 lowercase hex chars>"
}
```

Rationale:

- The key is opaque enough for storage indexes and safe logs.
- The binding payload is receipt-readable and deterministic.
- Unknown host-local fields cannot change the key accidentally.
- Workflow-instance sessions change when the workflow version, instance,
  actor, adapter, runtime namespace, or profile binding changes.
- Program-instance sessions are keyed to the program instance rather than one
  workflow occurrence, but still change when actor, adapter, runtime namespace,
  or profile binding changes.

The helper added in this slice is:

- `ActorSessionScope`
- `ActorSessionBinding`
- `digest_actor_session_profile(profile)`
- `canonical_actor_session_binding(binding)`
- `canonical_actor_session_key(binding)`

## Prompt And Profile Binding

The `profile_binding_digest` is the boundary between harmless per-run context
and standing actor identity. It should digest the actor's standing binding:

- workflow actor declaration, including role and adapter ref;
- runtime adapter id and runtime namespace;
- identity prompt ids, versions, and content hashes;
- standing lane prompt when it changes the actor's stable mandate;
- adapter-source prompts such as imported host instructions or skills when they
  alter standing behavior;
- policy envelope id, version, content hash, and effective permission digest;
- model/runtime family when it materially affects session compatibility.

It should not digest ordinary per-run facts, current artifacts, prior receipts,
stage inputs, or stage-specific instructions that are sent as the next turn in
the same session. If a stage prompt permanently changes persona or mandate, the
adapter should classify it as standing profile input and the digest should
change.

Receipts for stage execution should record:

- `actor_session_key`;
- `actor_session_id`;
- session generation;
- `profile_binding_digest`;
- selected scope and scope id;
- runtime external ref, redacted where needed;
- prompt provenance and rendered context digest already required by the prompt
  registry contract.

## Reuse Rules

The runner may reuse an actor session only when all checks pass:

- The existing `actor_session_key` equals the candidate key.
- Session status is `active` or `reattachable`.
- Session generation is the latest non-replaced generation for the key.
- The last audit status is green or explicitly waivable by a recorded human
  decision.
- The session is within configured TTL, lease, budget, and stale thresholds.
- The adapter can reattach or continue by the stored runtime ref.
- No unresolved side-effect uncertainty exists on the prior invocation.
- The prior output and transcript refs needed for continuity are readable or
  summarized in trusted receipt artifacts.
- Policy class and effective permissions have not expanded without approval.
- The workflow/program scope still matches the current stage.

Reuse should be preferred within a scope, but never at the cost of losing audit
clarity. If the runner cannot prove a session is reusable, it should choose
replacement or block according to side-effect risk.

## Replacement Rules

Replacement is append-only. The kernel should never overwrite an old session
row to pretend it is the same healthy session.

Create a new generation when:

- prompt/profile binding changes;
- the runtime says the session is corrupted or unrecoverable;
- transcript or runtime readback is missing;
- context compaction removed required working state and no trusted summary
  receipt exists;
- the session exceeded TTL or stale threshold;
- the actor drifted semantically from the requested role;
- policy or tool permissions changed materially;
- the session requested or performed an out-of-scope action;
- side-effect state is uncertain after interruption;
- a human explicitly resets the actor.

Replacement receipt fields should include:

- prior `actor_session_id` and generation;
- new `actor_session_id` and generation;
- unchanged or changed `actor_session_key`;
- replacement reason enum;
- evidence refs: audit receipt, transcript ref, adapter readback, policy diff,
  prompt/profile diff, or human decision ref;
- whether trusted state summary was carried forward;
- whether retry/continuation is safe.

Recommended reason enum:

```text
profile_binding_changed
policy_binding_changed
runtime_corrupted
compaction_lost_required_state
stale_session
semantic_drift
scope_violation
unknown_side_effect_state
transcript_unreadable
missing_terminal_receipt
adapter_unavailable
human_reset
```

If replacement follows an unsafe or unknowable external-effect attempt, the
runner should block with `approval_required=1` instead of silently starting a
new session.

## Corruption And Compaction Handling

Compaction is not automatically corruption. A compacted session can be reused
when the adapter can show that required continuity is preserved by a trusted
summary or receipt chain.

The runner should classify compacted or suspect sessions as:

- `compact_ok`: adapter reports compaction, but a trusted summary and receipt
  chain preserve all required state.
- `compact_rehydrate`: reuse is paused while the runner injects a bounded state
  summary from receipts and artifacts.
- `compact_replace`: required state is missing; create a new generation with a
  carried-forward trusted summary if available.
- `corrupt_replace`: runtime state is inconsistent, poisoned, or semantically
  wrong; replace and do not carry forward untrusted transcript state.
- `unsafe_block`: external-effect state or authorization state is unknowable;
  block for human recovery.

The compaction/readback evidence should come from adapter APIs or trusted
transcript artifacts, not from prompt-only claims inside the actor's prose.

## Provenance Receipts

Actor-session selection should emit receipt-grade provenance before adapter
work starts. Minimum receipt kinds:

- `actor_session_selected.v1`: no prior session existed or the latest session
  was selected for a stage.
- `actor_session_reused.v1`: existing session passed reuse validation.
- `actor_session_replaced.v1`: a new generation superseded an old session.
- `actor_session_recovery_audit.v1`: startup or stale-lease recovery inspected
  session health and decided reattach, retry, replace, or block.

Every adapter invocation receipt should include the selected session identity.
This makes a later verifier able to answer: which actor was used, why that
session was safe to reuse, what prompt/profile it was bound to, and whether
replacement happened before the stage changed workflow state.

## Validation Requirements

Compile-time validation:

- Actor refs in stage `actors` must resolve to workflow `actors`.
- Optional future `session` declarations should have known scopes only:
  `workflow_instance` or `program_instance`.
- Program-scope declarations must identify a stable program id.
- Hard policy classes must not be allowed to inherit old sessions without an
  approval path.

Run-time validation:

- Candidate binding hashes to the expected `actor_session_key`.
- Prompt/profile digest matches the resolved prompt and policy provenance.
- Runtime namespace and adapter id match the current adapter registration.
- Reuse is blocked if audit, transcript, or adapter readback is missing.
- Session generation is latest for the key.
- Replacement reasons are enum values with evidence refs.
- External-effect ambiguity fails closed into human recovery.

Recovery validation:

- Startup sweep inspects active sessions with stale leases or missing heartbeats.
- Reattach only when adapter readback confirms the external runtime ref.
- Completed child or delegated sessions must still satisfy child-session audit
  requirements before parent session reuse.
- Orphan outputs cannot advance a stage just because the actor claims it is
  done.

Test/fixture validation:

- Key canonicalization is stable across field ordering.
- Keys change when scope id, actor ref, adapter id, runtime namespace, or
  profile binding changes.
- Program-scope keys ignore per-occurrence workflow instance ids.
- Invalid scope or missing required binding fields fails closed.
- Receipts expose session key, generation, profile digest, and replacement
  reason.

## Storage Sketch

Future migration sketch:

```sql
CREATE TABLE actor_sessions (
  actor_session_id TEXT PRIMARY KEY,
  actor_session_key TEXT NOT NULL,
  scope TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  workflow_id TEXT NOT NULL,
  workflow_version TEXT,
  program_id TEXT,
  actor_ref TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  runtime_namespace TEXT NOT NULL,
  profile_binding_digest TEXT NOT NULL,
  generation INTEGER NOT NULL,
  status TEXT NOT NULL,
  health_status TEXT NOT NULL,
  external_session_ref TEXT,
  transcript_ref TEXT,
  replacement_of_session_id TEXT REFERENCES actor_sessions(actor_session_id),
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(actor_session_key, generation)
);

CREATE TABLE actor_session_uses (
  use_id TEXT PRIMARY KEY,
  actor_session_id TEXT NOT NULL REFERENCES actor_sessions(actor_session_id),
  instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
  stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id),
  adapter_invocation_id TEXT REFERENCES adapter_invocations(invocation_id),
  decision TEXT NOT NULL,
  replacement_reason TEXT,
  receipt_id TEXT REFERENCES receipts(receipt_id),
  created_at TEXT NOT NULL,
  UNIQUE(stage_run_id, actor_session_id)
);
```

`adapter_invocations` should later gain `actor_session_id` and
`actor_session_key`. `child_sessions` should later reference `actor_session_id`
when a delegated child is part of a parent actor's session lifecycle.

## Lane Fit

The model stays lane-neutral:

- Ivy/Jonah: two actor sessions, writer and editor, inside an editorial
  workflow instance unless a future editorial program explicitly opts in to
  program scope.
- Bumblebee: producer and reviewer sessions stay separate and are reused across
  bounded ping-pong only while the review contract and profile binding match.
- Jarvis weekly/improvement: program-scope Jarvis session can survive weekly
  workflow occurrences with a stable program id and read-only shadow policy.
- Radhe: deterministic system stages do not need actor sessions, but reviewer
  or owner-quality agent gates can use workflow or program scope based on the
  lane contract.
- Trading research: researcher and reviewer sessions are read-only; any route
  toward execution must cross a separate human-gated workflow and should not
  reuse research-session authorization.
- Future lanes: declare actor role, adapter id, session scope, and profile
  binding; the kernel does not need lane names in core code.

## Implementation Notes

This slice intentionally implemented only the portable key helper and tests.
The next implementation slice should:

1. Add `actor_sessions` and `actor_session_uses` to `WorkflowLedger`.
2. Select or create an actor session before `AdapterInvocation`.
3. Record actor-session provenance in receipts.
4. Add recovery sweep behavior for stale active sessions.
5. Extend workflow DSL validation with optional actor/session declarations.
6. Add fixture workflows for workflow-instance and program-instance reuse.

That sequence keeps the storage migration, runner behavior, and adapter
reattachment work reviewable while anchoring all future code on the same
canonical key contract.
