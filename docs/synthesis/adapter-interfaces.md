# Adapter Interfaces

Status: Wave 1 synthesis

This document defines the adapter boundary for the portable Agent Workflow
Kernel. The kernel owns workflow state, stage transitions, prompt/context
provenance, receipts, policy gates, and recovery semantics. Adapters own every
runtime, host, surface, and lane-specific dependency.

The design rule is simple:

```text
Kernel stores portable intent and evidence.
Adapters translate that intent into local execution and local surfaces.
```

## Portability Invariants

The kernel must never assume:

- host paths such as `/Users/sunny`, oldmac home directories, or OpenClaw roots;
- a specific human surface such as Northstar, Obsidian, Telegram, Apple Notes,
  or Google Sheets;
- OpenClaw agent ids, session paths, cron ids, launchd labels, or workspace
  layout;
- lane names such as OR Research, Bumblebee, Radhe, Kamandal, Mala, or Ivy/Jonah
  as kernel-level concepts;
- that a shell, browser, message tool, agent session, or local filesystem is
  available.

The kernel may store logical references such as:

```text
host:openclaw-reference
runtime:agent/or_research
surface:human-review/main
lane:or-research
artifact:sha256:...
```

Only adapters resolve those references to paths, session keys, message accounts,
vault notes, Sheet ranges, or command lines.

## Adapter Families

The first adapter families are:

| Family | Kernel reason to call it | Adapter-owned reality |
| --- | --- | --- |
| Runtime adapter | Execute a stage or collect runtime proof. | Agent runtimes, shell commands, browser tasks, model calls, human waits, subprocesses. |
| Surface adapter | Publish or read back human-visible state. | Obsidian, Telegram, local Markdown, Sheets, Slack, browser staging, review notes. |
| Host adapter | Resolve environment and durable host services. | Host roots, scheduler, locks, local capabilities, remote execution, filesystem layout. |
| Lane adapter | Translate domain cargo to/from kernel work. | Domain ledgers, gate names, rubrics, project ids, local engines, approval semantics. |

Adapters are deliberately narrow. A stage may use more than one adapter, but the
kernel should see each interaction as an `AdapterInvocation` with a portable
result and receipt.

## Shared Envelope

Every adapter call should use the same top-level envelope so runner, recovery,
and audit code can stay generic.

```text
AdapterInvocation
- invocation_id
- workflow_id
- instance_id
- stage_run_id
- adapter_family
- adapter_id
- operation
- input_ref
- context_packet_ref
- prompt_ref
- policy_gate_ref
- idempotency_key
- timeout_seconds
- requested_at
```

```text
AdapterResult
- invocation_id
- status: succeeded | failed | blocked | needs_human | timed_out | cancelled
- outputs
- artifact_refs
- receipt_ref
- surface_refs
- runtime_refs
- residual_risk
- next_hint
- completed_at
```

```text
AdapterReceipt
- schema_version
- invocation_id
- adapter_family
- adapter_id
- operation
- inputs
- outputs
- checks_run
- artifact_refs
- surface_refs
- runtime_refs
- transcript_refs
- policy_snapshot
- provenance
- idempotency_key
- status
- summary
- residual_risk
- created_at
```

Receipts should be immutable once written. If a later readback changes the
state, write another receipt that supersedes or corrects the earlier one.

## References

Adapters return references, not hidden assumptions.

```text
ArtifactRef
- artifact_id
- kind
- uri
- content_hash
- mime_type
- size_bytes
- created_by
- created_at
```

```text
RuntimeRef
- runtime_id
- kind: agent_session | shell_process | browser_session | model_call | human_wait
- external_id
- host_ref
- redacted_locator
- status
```

```text
SurfaceRef
- surface_id
- kind: review_note | message | sheet_range | local_markdown | browser_plan
- external_id
- title
- readback_required
- status
```

```text
HostRef
- host_id
- host_kind: local | remote | managed
- capability_set
- state_root_ref
- scheduler_ref
```

`uri` values must be logical or adapter-owned. A kernel fixture may include a
path-like string for test data, but production workflow definitions should not.

## Runtime Adapter Contract

Runtime adapters execute work. They do not decide whether the work should be
allowed; policy gates are evaluated before runtime execution and included in the
invocation.

Minimum operations:

```text
RuntimeAdapter.capabilities() -> CapabilitySet
RuntimeAdapter.invoke(invocation, runtime_input) -> AdapterResult
RuntimeAdapter.poll(runtime_ref) -> AdapterResult
RuntimeAdapter.cancel(runtime_ref, reason) -> AdapterReceipt
RuntimeAdapter.collect_proof(runtime_ref, proof_request) -> AdapterReceipt
RuntimeAdapter.recover(idempotency_key) -> AdapterResult
```

`runtime_input` may contain:

- rendered context packet hash and location;
- prompt reference and prompt version;
- stage objective;
- required output schema;
- artifact inputs;
- allowed actions and forbidden actions;
- tool policy snapshot;
- budget and timeout;
- expected proof.

Runtime adapters should be able to represent:

- a model or agent turn;
- a shell/script invocation;
- a browser interaction;
- a human wait as a runtime state;
- a native reviewer/doer loop;
- a deterministic validation command.

Runtime adapters must return structured evidence. For agent-to-agent work, a
plain transcript is not enough. The receipt must identify the native session,
trusted tool-call proof, bounded turn budget, verdict, and output artifacts.

## Surface Adapter Contract

Surface adapters make work visible to humans and ingest explicit human
decisions. They are presentation and control bridges, not canonical workflow
state.

Minimum operations:

```text
SurfaceAdapter.publish(invocation, surface_packet) -> AdapterResult
SurfaceAdapter.readback(surface_ref) -> AdapterReceipt
SurfaceAdapter.ingest_decisions(surface_query) -> list[AdapterReceipt]
SurfaceAdapter.clear(surface_ref, reason) -> AdapterReceipt
SurfaceAdapter.validate(surface_ref) -> AdapterReceipt
```

`surface_packet` may contain:

- title and concise human ask;
- source artifacts and receipt links;
- allowed decisions;
- current status;
- owner and next action;
- final approval boundary;
- read/clear versus approve/reject semantics.

Surface adapters must make these distinctions explicit:

- read-only summary versus actionable decision;
- approval to continue internal work versus approval for external effect;
- comments as context versus comments as an authorized command;
- generated view versus source of truth.

The kernel should require readback for high-value human surfaces. A publish call
without readback is only a queued or attempted delivery, not confirmed operator
visibility.

## Host Adapter Contract

Host adapters resolve where and how work runs. They provide the local facts that
the kernel is not allowed to know.

Minimum operations:

```text
HostAdapter.describe() -> HostDescriptor
HostAdapter.resolve(ref) -> ResolvedRef
HostAdapter.prepare_state(instance_id) -> HostState
HostAdapter.acquire_lease(idempotency_key, ttl_seconds) -> LeaseReceipt
HostAdapter.release_lease(lease_id) -> LeaseReceipt
HostAdapter.schedule(schedule_request) -> AdapterReceipt
HostAdapter.unschedule(schedule_ref) -> AdapterReceipt
HostAdapter.healthcheck(scope) -> AdapterReceipt
```

Host adapters own:

- root directories and path expansion;
- remote execution details;
- local command availability;
- scheduler choice, such as launchd, cron, managed job, or no scheduler;
- runtime-specific state stores;
- host-specific drift checks;
- safe redaction of local locators and logs.

Host adapters do not own workflow graph transitions. They expose host facts and
run host operations when the kernel asks for an approved operation.

## Lane Adapter Contract

Lane adapters translate domain work into the generic kernel shape. They should
be thin. The domain engine remains outside the kernel.

Minimum operations:

```text
LaneAdapter.describe() -> LaneDescriptor
LaneAdapter.open_work(domain_input) -> WorkflowInstanceSeed
LaneAdapter.build_stage_input(stage_run, domain_state) -> RuntimeInput
LaneAdapter.validate_artifacts(stage_run, artifact_refs) -> AdapterReceipt
LaneAdapter.interpret_result(stage_run, adapter_result) -> TransitionHint
LaneAdapter.prepare_human_gate(stage_run, gate_request) -> SurfacePacket
LaneAdapter.apply_decision(decision_receipt) -> AdapterResult
```

Lane adapters own:

- domain vocabulary and gate names;
- project or campaign ids;
- domain artifact formats;
- local validation rules;
- lane-specific risk interpretations;
- compatibility with existing lane ledgers and scripts.

Lane adapters must not own:

- generic runner leases;
- generic policy gate classes;
- generic receipt persistence;
- host path resolution;
- surface delivery details;
- hidden expansion of approved scope.

## Policy Boundary

Adapters execute only after policy has been evaluated. The policy snapshot must
travel with the invocation and receipt.

Required fields:

```text
PolicySnapshot
- risk_class
- allowed_actions
- forbidden_actions
- approval_required
- approved_by
- approval_ref
- approval_scope
- expires_at
```

Actions that should stay human-gated include public publish, external sends,
deploys, production mutation, credential or auth repair, broker/trading actions,
money movement, high-cost compute, and destructive cleanup.

A lane adapter may recommend a risk class. The kernel decides whether execution
is allowed. A surface adapter may capture a human decision. The kernel decides
what that decision authorizes.

## Failure And Recovery

Adapters must fail with inspectable state rather than prose-only errors.

```text
AdapterError
- error_class: transient | policy_blocked | invalid_input | stale_state | missing_capability | external_failure | unknown
- message
- retryable
- suggested_next_action
- evidence_refs
- partial_outputs
```

Recovery rules:

- Use `idempotency_key` for every mutation or external-effect preparation.
- A retry must either resume the same external work or produce a receipt that
  explains why it could not.
- Stale runtime sessions become runtime receipts, not silent reruns.
- Surface disagreement fails closed into a human gate or blocked state.
- Host capability drift is a host receipt, not a kernel code path.

## Compatibility Strategy

The first implementation may wrap existing OpenClaw Work Ledger, A2A, and
Blackboard paths. That compatibility must stay outside the portable kernel.

Kernel-side objects should map to compatibility paths like this:

| Kernel object | Compatibility target |
| --- | --- |
| `WorkflowInstance` | Existing work item or lane-local project id. |
| `StageRun` | Work Ledger phase, native session run, or deterministic script run. |
| `AdapterInvocation` | CLI call, native agent session, script call, or surface publish. |
| `AdapterReceipt` | Work Ledger receipt, artifact outbox record, review ingest receipt, or exported JSON receipt. |
| `SurfacePacket` | Blackboard/Review Inbox note, Telegram handoff message, local Markdown packet, or Sheet row. |
| `TransitionHint` | Work Ledger next broker/status or lane-specific next action. |

The direction of travel is not to copy OpenClaw internals into the kernel. The
kernel should define the stable contract. OpenClaw adapters should wrap current
internals until parity proves the boundary.

## Acceptance Checklist

- Kernel definitions contain no hardcoded OpenClaw, Northstar, Telegram,
  oldmac, or `/Users/sunny` path assumptions.
- Runtime, surface, host, and lane adapter contracts are distinct.
- Human-visible surfaces are not treated as canonical workflow state.
- Agent-to-agent proof requires trusted runtime evidence, not prompt text.
- Lane adapters translate domain cargo without leaking domain names into the
  kernel core.
- Host adapters resolve paths, schedulers, and local capabilities.
- Receipts carry prompt/context/policy/provenance enough for replay and audit.
- Compatibility wrappers can call current OpenClaw Work Ledger, A2A, and
  Blackboard paths without making those paths kernel dependencies.
