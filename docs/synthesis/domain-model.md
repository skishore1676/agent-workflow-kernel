# Domain Model

Last updated: 2026-05-31

## Purpose

The portable kernel domain model describes auditable workflows without assuming
OpenClaw, oldmac, Northstar, Telegram, Radhe, trading systems, or any specific
agent runtime. It is the generic rail for domain-specific cargo.

The model must support:

- agent work;
- deterministic scripts;
- reviewer/doer loops;
- human approval gates;
- policy denial and blocked states;
- durable receipts and artifacts;
- recovery after runner or supervisor interruption.

It should not hide domain engines inside workflow state. The kernel coordinates
work; adapters and domain systems decide domain truth.

## Design Principles

- Workflows are versioned graphs, not arbitrary programs.
- Every execution creates durable state that can be resumed, audited, or
  compared against a prior implementation.
- Stages declare their required inputs, possible outcomes, policy class,
  adapter family, and expected outputs.
- Stage attempts are append-only. Current state is derived from stage run
  history plus the active workflow instance pointer.
- Receipts are immutable evidence packets. They should be useful even after
  the original runtime transcript or surface message is gone.
- Domain labels are allowed as data. Domain logic belongs in adapters, scripts,
  prompts, and validators, not in the generic kernel.

## Object Model

| Object | Owns | Does not own |
| --- | --- | --- |
| `WorkflowDef` | Stable workflow id, semantic version, stage graph, declared inputs, allowed actors, policy defaults, compatibility metadata. | Runtime state, lane code, prompt text, host paths. |
| `StageDef` | A named node in the graph with a stage type, adapter binding, input selectors, output contract, policy requirement, retry hints, and timeout hints. | How an adapter talks to OpenClaw, Radhe, Sheets, Telegram, broker APIs, or local shells. |
| `Transition` | Movement from one stage to another based on a small set of named outcomes and optional receipt fields. | General expression evaluation, domain calculations, free-form Python/YAML logic. |
| `WorkflowInstance` | One started workflow with definition version, input snapshot, current state, current stage, parent/child links, and history pointers. | Business-domain ledgers or source-of-truth artifacts. |
| `StageRun` | One attempt at a stage, including claim, lease, actor, adapter invocation, status, output references, receipt id, failure class, and retry lineage. | Stage definition changes or other attempts' state. |
| `ArtifactRef` | Hash, URI/path, media type, role, provenance, mutability, and visibility policy for generated or inspected material. | The artifact bytes themselves unless a storage adapter chooses inline storage. |
| `Receipt` | Immutable summary of what happened, inputs, outputs, checks, prompts/context hashes, actor/runtime metadata, policy result, residual risk, and next action. | Live status mutation after creation. Corrections are new receipts. |
| `PolicyGate` | Required approval class, approver role, decision source, decision state, expiry, and receipt linkage. | Human UI or exact surface implementation. |
| `AdapterInvocation` | Adapter family, adapter id, request digest, response digest, external ids, start/end time, and low-level status. | Domain interpretation beyond declared status fields. |

## Lifecycle

```text
WorkflowDef@version
  -> WorkflowInstance(input snapshot)
  -> StageRun(claimed attempt)
  -> AdapterInvocation(runtime, surface, script, or human)
  -> ArtifactRef(s)
  -> Receipt(immutable evidence)
  -> Transition(named outcome)
  -> next StageRun / Human Gate / Done / Blocked
```

`WorkflowInstance.status` should be a compact operational enum:

- `pending`
- `running`
- `waiting_on_agent`
- `waiting_on_human`
- `waiting_on_schedule`
- `retrying`
- `blocked`
- `policy_denied`
- `final_approval_required`
- `done`
- `cancelled`

`StageRun.status` should be more attempt-specific:

- `queued`
- `claimed`
- `started`
- `waiting`
- `succeeded`
- `failed`
- `invalid_output`
- `timed_out`
- `blocked`
- `approval_required`
- `approval_denied`
- `superseded`

Failures should be classified separately from status so recovery can distinguish:

- runtime failure;
- adapter unavailable;
- invalid output schema;
- deterministic validation failure;
- human rejection;
- policy denial;
- stale lease;
- missing dependency;
- domain blocked state.

## Stage Types

| Stage type | Meaning | Typical adapter |
| --- | --- | --- |
| `agent_work` | One agent or model produces an artifact, answer, plan, patch, or report. | Runtime adapter such as Codex, OpenClaw session, or model API. |
| `agent_gate` | One agent evaluates an artifact and returns a structured decision without owning final human approval. | Runtime adapter with read-only or scoped tools. |
| `a2a_review_loop` | Producer and reviewer exchange bounded questions, revisions, and verdicts. | Runtime adapter plus A2A adapter contract. |
| `human_gate` | The workflow waits for a human decision from an approved surface. | Human or surface adapter. |
| `system_action` | Deterministic local or remote action, usually script-backed. | Shell, browser, host, lane, or service adapter. |
| `wait_schedule` | Time, event, or dependency wait. | Runner or scheduler adapter. |
| `recovery` | Recovery, resume, stale-lease repair, or compatibility backfill step. | Runner, host, or lane adapter. |
| `blocked` | Terminal or parked blocked state with an explicit unblock request. | No work adapter required. |

## Minimal Schema Sketch

The first implementation can persist these as SQLite tables with JSON columns
for adapter-specific details. JSON export should mirror the same fields for
fixtures, receipts, and host compatibility.

| Table | Key fields |
| --- | --- |
| `workflow_defs` | `workflow_id`, `version`, `name`, `definition_hash`, `status`, `created_at`, `deprecated_at`, `source_uri` |
| `stage_defs` | `workflow_id`, `workflow_version`, `stage_id`, `stage_type`, `adapter_ref`, `input_spec_json`, `output_contract_json`, `policy_ref`, `retry_policy_json` |
| `transitions` | `workflow_id`, `workflow_version`, `from_stage_id`, `on_outcome`, `guard_ref`, `to_stage_id`, `terminal_status` |
| `workflow_instances` | `instance_id`, `workflow_id`, `workflow_version`, `status`, `current_stage_id`, `input_snapshot_hash`, `parent_instance_id`, `created_at`, `updated_at` |
| `stage_runs` | `run_id`, `instance_id`, `stage_id`, `attempt`, `status`, `failure_class`, `lease_owner`, `lease_expires_at`, `adapter_invocation_id`, `receipt_id`, `started_at`, `ended_at` |
| `adapter_invocations` | `adapter_invocation_id`, `adapter_family`, `adapter_id`, `request_hash`, `response_hash`, `external_ref`, `status`, `started_at`, `ended_at` |
| `artifact_refs` | `artifact_id`, `instance_id`, `stage_run_id`, `role`, `uri`, `content_hash`, `media_type`, `provenance_json`, `visibility`, `created_at` |
| `receipts` | `receipt_id`, `instance_id`, `stage_run_id`, `kind`, `status`, `outcome`, `summary`, `inputs_json`, `outputs_json`, `checks_json`, `policy_result_json`, `prompt_provenance_json`, `next_action_json`, `created_at` |
| `policy_gates` | `gate_id`, `instance_id`, `stage_run_id`, `risk_class`, `required_decision`, `decision_state`, `decision_source`, `approver_ref`, `expires_at`, `receipt_id` |

## Receipt Contract

A receipt should be the durable proof unit for all stage types. It should include
at least:

- `receipt_id`, `instance_id`, `stage_run_id`, `workflow_id`, `workflow_version`;
- `kind`, `status`, `outcome`, `summary`;
- actor, adapter, runtime, model, tool permission, and host metadata;
- input artifact refs and output artifact refs;
- prompt id, prompt version, prompt content hash, context packet hash, and
  rendered input digest when a prompt was used;
- checks run, validation result, and schema result;
- policy class, approval state, approver, and denial reason when relevant;
- transcript refs or summarized transcript hashes for agent and human loops;
- residual risk, next broker, next owner, and next action;
- timestamps and monotonic attempt number.

Receipts are never edited. If a run is corrected, superseded, or manually
overridden, the kernel writes a new receipt that points to the previous one.

## Transition Model

Transitions should be deliberately small. A `StageDef` emits a named outcome
from a declared set, for example:

- `pass`
- `refine`
- `block`
- `needs_human`
- `approval_granted`
- `approval_denied`
- `retry`
- `no_output`
- `done`

Transitions may additionally reference named guards implemented by the runner or
policy engine, such as `has_required_artifacts`, `within_retry_budget`, or
`policy_approved`. They should not contain arbitrary code.

Good transition:

```text
from: reviewer
on: refine
to: revise_draft
```

Acceptable guarded transition:

```text
from: publish_review
on: approval_granted
guard: policy_approved
to: publish_dry_run
```

Not acceptable:

```text
if qa.score > 0.82 and current_time < market_close and not user_is_busy:
```

That belongs in a validator, lane adapter, or domain engine that emits a named
outcome and receipt fields.

## Versioning

`WorkflowDef` versions should be immutable after activation. A workflow change
creates a new version with:

- semantic version or monotonically increasing integer;
- definition hash;
- migration notes;
- compatibility policy for in-flight instances;
- deprecation status for older versions.

Instances keep the definition version they started with unless a migration stage
explicitly moves them. Prompt versions and adapter versions are recorded in
receipts, not copied into the workflow definition.

## What Is Declarative Versus Adapter Code

Declarative workflow config owns:

- workflow id, name, version, description, and owner metadata;
- stages and stage types;
- allowed actors and adapter references;
- required inputs and artifact roles;
- expected output schema names;
- named outcomes and graph transitions;
- question or revision budgets;
- timeout and retry policy;
- policy classes and approval requirements;
- surface publication intent, such as "write owner brief" or "wait for Telegram
  approval";
- compatibility hints and fixture ids.

Adapter, runner, prompt, or domain code owns:

- OpenClaw session spawning, `sessions_send`, local paths, and oldmac details;
- Radhe run discovery, media QA interpretation, novelty checks, and publish
  package parsing;
- trading research engines, broker APIs, market data, and live execution rules;
- Obsidian, Telegram, Sheets, Slack, browser, filesystem, and service APIs;
- prompt rendering and context packet assembly;
- JSON schema validation mechanics;
- deterministic script implementation;
- human surface parsing and decision reconciliation;
- business-domain calculations and thresholds.

The dividing line: config says what shape of work is allowed and what outcome is
needed; adapter code proves what happened in the real system.

## Fit Against Required Workflows

| Workflow | Core model expression | Adapter/domain cargo |
| --- | --- | --- |
| Bumblebee review | `agent_work` creates an evidence packet, `a2a_review_loop` asks bounded questions, receipt outcome is `pass`, `refine`, `block`, or `needs_human`. | OpenClaw quality reviewer identity, skill loading, producer session key, specific review rubric, runtime artifact paths. |
| Ivy/Jonah review loop | Ivy producer stage creates P4 draft package, Jonah `a2a_review_loop` reviews with revision budget, `human_gate` represents P5 approval before public publish. | OR Research project ledger, P3/P4/P5 naming, Substack/Medium packet details, Jonah's editorial rubric. |
| Trading research gate | `agent_work` or `system_action` produces research packet, `agent_gate` validates evidence, `human_gate` blocks any promotion beyond research or shadow/no-capital recommendation. | Market data, strategy engines, broker/account state, live trade placement, buying-power checks, strategy-family details. |
| Radhe review pipeline | `system_action` starts or resumes generation, `system_action` validates QA package, `agent_gate` or `human_gate` classifies review readiness, hard `human_gate` before publish/upload. | Radhe repo paths, run.json semantics, media generation stages, novelty guard, video files, Telegram card sending, YouTube publish control. |

## Open Questions

- Should workflow definitions start as YAML only, or should Python builders be
  allowed to generate the same canonical schema for tests?
- Which output schema system should be canonical: JSON Schema, Pydantic-style
  generated schema, or a smaller kernel-owned schema format?
- How much transcript content should be stored versus hashed and summarized?
- Should policy defaults be global-first, workflow-first, or layered by host,
  lane, and workflow?
- What is the canonical human decision source when Telegram, Obsidian, and a
  local file disagree?
- How should in-flight workflow migrations work when a stage definition changes
  while an instance is waiting on a human?
- What is the minimum adapter version/provenance needed for parity comparisons
  against OpenClaw compatibility paths?

## Risks

- The DSL could grow into an accidental programming language if transitions
  accept arbitrary expressions.
- Too much domain meaning in generic enums would contaminate the portable
  kernel with OpenClaw, Radhe, or trading assumptions.
- Too little structure in receipts would make parity, recovery, and review
  handoffs unverifiable.
- Human decision reconciliation can become unsafe if surfaces disagree and the
  kernel guesses.
- Creative workflows may resist strict output schemas; the kernel needs schema
  discipline for decisions and evidence, not for every paragraph of generated
  prose.
- Versioning can become noisy unless definition hashes, prompt hashes, and
  adapter versions have clear separate roles.
