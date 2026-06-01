# Workflow DSL

Last updated: 2026-05-31

## Purpose

The workflow DSL is a portable graph definition format for the Agent Workflow
Kernel. It should be readable as YAML, canonicalizable as JSON, and small enough
that a runner can validate it before execution.

The DSL should define workflow structure, contracts, policies, and adapter
bindings. It should not become a scripting language or a hidden home for
OpenClaw lane logic.

## Format Recommendation

Start with YAML-authored definitions that compile into a canonical JSON
`WorkflowDef`:

- YAML is operator-readable and good for review.
- Canonical JSON is better for hashing, schema validation, fixture comparison,
  and storage.
- A Python builder can be added later only if it emits the same canonical schema
  and is used for tests or repetitive definitions, not as the primary product
  surface.

## Top-Level Shape

```yaml
schema: workflow.kernel.v1
workflow:
  id: bumblebee_quality_review
  version: 0.1.0
  name: Bumblebee Quality Review
  owner: openclaw_adapter
  description: Read-only producer/reviewer quality gate with bounded questions.

inputs:
  required:
    - work_item
    - source_artifacts
  optional:
    - acceptance_criteria
    - operator_surface

defaults:
  policy_class: read_only_review
  lease:
    seconds: 300
  timeout_seconds: 900
  retry:
    max_attempts: 1
    on_failure_class:
      runtime_failure: retry
      invalid_output: block

actors:
  producer:
    adapter: runtime.openclaw_agent
    role: producer
  reviewer:
    adapter: runtime.openclaw_agent
    role: quality_reviewer
    lease:
      seconds: 900
  operator:
    adapter: human.default
    role: owner

stages:
  - id: prepare_packet
    type: system_action
    adapter: lane.bumblebee.prepare_review_packet
    inputs:
      work_item: input.work_item
      source_artifacts: input.source_artifacts
    outputs:
      artifacts:
        - role: review_packet
          required: true
      outcome_schema: review_packet_result.v1
    outcomes: [ready, blocked]

  - id: review_loop
    type: a2a_review_loop
    adapter: runtime.a2a
    actors:
      producer: actors.producer
      reviewer: actors.reviewer
    inputs:
      review_packet: artifacts.prepare_packet.review_packet
      acceptance_criteria: input.acceptance_criteria
    budget:
      max_questions: 3
      max_revision_turns: 2
      max_ping_pong_turns: 7
    lease:
      seconds: 1200
    outputs:
      receipt_kind: probing_review_verdict
      outcome_schema: review_verdict.v1
    outcomes: [pass, refine, block, needs_human]

  - id: operator_gate
    type: human_gate
    adapter: surface.operator_review
    policy:
      class: external_effect_or_uncertain_review
      requires_explicit_approval: true
    inputs:
      verdict: receipts.review_loop
    outcomes: [approval_granted, approval_denied, revise]

transitions:
  - from: prepare_packet
    on: ready
    to: review_loop
  - from: prepare_packet
    on: blocked
    terminal: blocked
  - from: review_loop
    on: pass
    terminal: done
  - from: review_loop
    on: refine
    to: prepare_packet
    guard: within_revision_budget
  - from: review_loop
    on: block
    terminal: blocked
  - from: review_loop
    on: needs_human
    to: operator_gate
  - from: operator_gate
    on: approval_granted
    terminal: done
  - from: operator_gate
    on: approval_denied
    terminal: policy_denied
  - from: operator_gate
    on: revise
    to: prepare_packet
```

This example is intentionally generic. The phrase `lane.bumblebee` is an
adapter id, not a kernel import path.

## DSL Sections

| Section | Required | Purpose |
| --- | --- | --- |
| `schema` | yes | DSL schema id for validation and migration. |
| `workflow` | yes | Workflow identity, version, name, owner, and description. |
| `inputs` | yes | Named input slots, required/optional status, and optional schema refs. |
| `defaults` | no | Shared policy, timeout, retry, receipt, and adapter defaults. |
| `actors` | no | Named logical actors used by stages. |
| `artifacts` | no | Optional declared artifact roles for validation and documentation. |
| `stages` | yes | Stage definitions. |
| `transitions` | yes | Graph edges from named outcomes to next stages or terminal states. |
| `policies` | no | Workflow-local policy aliases that resolve to policy engine rules. |
| `compatibility` | no | Host-specific mapping hints for dual-run, fixture-run, or migration. |

## Stage Fields

| Field | Required | Notes |
| --- | --- | --- |
| `id` | yes | Stable within a workflow version. |
| `type` | yes | One of the kernel stage types, such as `agent_work`, `a2a_review_loop`, `human_gate`, or `system_action`. |
| `adapter` | yes | Logical adapter ref. Resolved by host configuration, not by the kernel DSL. |
| `actors` | stage-dependent | Named actors for agent or human stages. |
| `inputs` | no | Selectors from workflow input, prior artifacts, prior receipts, or constants. |
| `outputs` | no | Artifact roles, receipt kind, and output schema refs. |
| `policy` | no | Required risk class or explicit approval rule. |
| `budget` | no | Question, revision, tool, cost, or turn limits. |
| `lease` | no | Declarative claim lease, shaped as `seconds: <positive-int>`. Stage lease overrides actor lease, which overrides `defaults.lease`; runners may pass one explicit override for a claim. |
| `timeout_seconds` | no | Runner hint, not a hard policy substitute. |
| `retry` | no | Retry hints by failure class or outcome. |
| `outcomes` | yes | Allowed named outcomes emitted by the stage. |
| `surface` | no | Requested operator surface behavior, resolved by a surface adapter. |

## Selectors

Selectors should be references, not expressions. Recommended selector prefixes:

- `input.<name>`
- `actors.<name>`
- `artifacts.<stage_id>.<role>`
- `receipts.<stage_id>`
- `context.<name>`
- `constants.<name>`
- `policy.<name>`

The runner can validate selector existence statically. If a value needs runtime
calculation, call a validator or adapter that emits a receipt field.

## Transitions

Transitions must stay simple:

```yaml
transitions:
  - from: review_loop
    on: pass
    to: operator_gate
    guard: requires_final_approval
  - from: review_loop
    on: block
    terminal: blocked
```

Allowed transition fields:

| Field | Meaning |
| --- | --- |
| `from` | Source stage id. |
| `on` | Named outcome from the source stage's allowed outcomes. |
| `to` | Next stage id. Mutually exclusive with `terminal`. |
| `terminal` | Terminal instance status. Mutually exclusive with `to`. |
| `guard` | Optional named runner or policy guard from an allowlist. |
| `label` | Human-readable edge label. |

Disallow inline boolean expressions, loops, shell commands, Python snippets, and
domain calculations. This keeps the DSL reviewable and portable.

## Policy Classes

The DSL may name policy classes, but the policy engine decides their exact
approval mechanics. Initial policy classes should include:

- `read_only_review`
- `internal_generation`
- `external_send`
- `public_publish`
- `deploy_or_prod_mutation`
- `auth_or_secret_change`
- `money_or_broker_action`
- `destructive_change`
- `high_cost_compute`

`public_publish`, `deploy_or_prod_mutation`, `auth_or_secret_change`,
`money_or_broker_action`, `external_send`, and `destructive_change` must require
explicit human approval.

## Required Workflow Fit

### Bumblebee Review

Use `system_action` to assemble a review packet, then `a2a_review_loop` for
bounded reviewer questions. Terminal outcomes can be `done`, `blocked`, or
`waiting_on_human` depending on the verdict.

Declarative:

- review packet artifact role;
- producer and reviewer actors;
- max questions and ping-pong turns;
- verdict schema;
- pass/refine/block/needs_human transitions.

Adapter code:

- OpenClaw session keys;
- skill loading;
- source artifact discovery;
- transcript writing;
- exact reviewer prompt and rubric.

### Ivy/Jonah Editorial Loop

Use an Ivy `agent_work` or lane `system_action` stage to produce a P4 draft
package, a Jonah `a2a_review_loop` stage to challenge and revise it, and a P5
`human_gate` before any publish packet or public send.

Declarative:

- P4 draft package artifact role;
- Jonah reviewer actor;
- revision budget;
- P5 approval gate;
- hard public publish policy class.

Adapter code:

- OR Research project ledger mapping;
- P3/P4/P5 compatibility fields;
- Substack/Medium packet creation;
- current Jonah editorial identity and style rubric.

### Trading Research Gate

Use `agent_work` or `system_action` for research, `agent_gate` for evidence
review, and `human_gate` for any promotion beyond read-only research. Live trade
or broker actions should be outside this example unless a future workflow uses a
hard `money_or_broker_action` gate.

Declarative:

- research packet artifact role;
- no-live-execution policy;
- review outcomes;
- human approval gate before shadow/no-capital promotion or live handoff.

Adapter code:

- strategy engines;
- market data;
- option-chain/broker checks;
- account state;
- all live order placement mechanics.

### Radhe Review Pipeline

Use `system_action` stages for run/resume and QA package inspection, an optional
`agent_gate` for owner-quality classification, a `human_gate` for approval, and
a final `system_action` only for dry-run or publish paths allowed by policy.

Declarative:

- run package artifact roles such as `run_manifest`, `qa_report`, `review_note`,
  `video`, and `publish_packet`;
- owner states such as `approval_needed`, `blocked`, `retry_needed`, `done`;
- no auto-publish default;
- explicit human gate before `public_publish`.

Adapter code:

- Radhe run directory layout;
- `run.json`, `qa.json`, `review.md`, and `publish.json` parsing;
- novelty guard interpretation;
- Telegram review delivery;
- YouTube or platform upload mechanics.

## Smaller YAML Sketches

Trading research gate:

```yaml
workflow:
  id: trading_research_gate
  version: 0.1.0

stages:
  - id: produce_research
    type: agent_work
    adapter: lane.trading.researcher
    policy:
      class: read_only_review
    outputs:
      artifacts:
        - role: research_packet
          required: true
      outcome_schema: research_packet.v1
    outcomes: [ready, blocked]

  - id: evidence_review
    type: agent_gate
    adapter: lane.trading.evidence_reviewer
    inputs:
      packet: artifacts.produce_research.research_packet
    outcomes: [pass, revise, block, needs_human]

  - id: promotion_gate
    type: human_gate
    adapter: surface.operator_review
    policy:
      class: money_or_broker_action
      requires_explicit_approval: true
    outcomes: [approval_granted, approval_denied]

transitions:
  - from: produce_research
    on: ready
    to: evidence_review
  - from: evidence_review
    on: pass
    to: promotion_gate
  - from: evidence_review
    on: revise
    to: produce_research
  - from: evidence_review
    on: block
    terminal: blocked
  - from: promotion_gate
    on: approval_granted
    terminal: done
  - from: promotion_gate
    on: approval_denied
    terminal: policy_denied
```

Radhe review pipeline:

```yaml
workflow:
  id: radhe_review_pipeline
  version: 0.1.0

stages:
  - id: run_or_resume
    type: system_action
    adapter: lane.radhe.run_or_resume
    outcomes: [package_ready, retry_needed, blocked, no_output]

  - id: inspect_package
    type: system_action
    adapter: lane.radhe.inspect_review_package
    outputs:
      artifacts:
        - role: run_manifest
        - role: qa_report
        - role: review_note
        - role: video
        - role: publish_packet
    outcomes: [approval_needed, auto_continue, blocked, retry_needed, done]

  - id: approval
    type: human_gate
    adapter: surface.telegram_or_obsidian
    policy:
      class: public_publish
      requires_explicit_approval: true
    outcomes: [approval_granted, approval_denied, revise]

transitions:
  - from: run_or_resume
    on: package_ready
    to: inspect_package
  - from: run_or_resume
    on: retry_needed
    to: run_or_resume
    guard: within_retry_budget
  - from: run_or_resume
    on: blocked
    terminal: blocked
  - from: run_or_resume
    on: no_output
    terminal: blocked
  - from: inspect_package
    on: approval_needed
    to: approval
  - from: inspect_package
    on: auto_continue
    to: run_or_resume
    guard: adapter_declares_safe_recovery
  - from: inspect_package
    on: done
    terminal: done
  - from: inspect_package
    on: blocked
    terminal: blocked
  - from: approval
    on: approval_granted
    terminal: done
  - from: approval
    on: approval_denied
    terminal: policy_denied
  - from: approval
    on: revise
    to: run_or_resume
```

## Validation Rules

The compiler should reject a workflow definition when:

- `workflow.id`, `workflow.version`, `stages`, or `transitions` is missing;
- stage ids are duplicated;
- transitions reference unknown stages;
- a transition `on` value is not in the source stage's `outcomes`;
- a transition has both `to` and `terminal`, or neither;
- a required actor or selector cannot be resolved statically;
- a stage with a hard policy class lacks an approval path;
- a `system_action` with `public_publish`, `external_send`,
  `money_or_broker_action`, `auth_or_secret_change`, or `destructive_change`
  can run without a prior explicit approval gate;
- retry loops have no budget;
- adapter refs are not declared in the host adapter registry.

Warnings, not hard failures:

- no compatibility fixture id;
- missing human-readable stage labels;
- no receipt kind override;
- no timeout on long-running stages;
- creative output schema is loose but decision receipt schema is strict.

## Open Questions

- Should the canonical format be stored as one workflow file per version, or as
  one file with a version history list?
- Should actor definitions live in workflow files or host adapter registry
  files?
- Should guarded transitions be limited to kernel-owned guards, or may adapters
  register named guards?
- What is the smallest useful compatibility block for dual-running OpenClaw
  lanes during parity validation?
- How should a human gate specify multiple possible surfaces without making the
  workflow depend on Telegram or Obsidian?
- Should workflow-local constants be allowed, or should all domain constants
  live in adapter config?

## Risks

- YAML readability can tempt people to encode domain policy as stringly-typed
  mini-code.
- Adapter refs can leak host names into portable definitions unless the host
  registry owns resolution.
- Human gates can look declarative while still being unsafe if decision-source
  reconciliation is underspecified.
- Strict schemas may slow creative work if applied to prose artifacts instead of
  decision receipts and artifact metadata.
- Too many optional fields will make definitions inconsistent; the compiler
  should enforce a small required core early.
