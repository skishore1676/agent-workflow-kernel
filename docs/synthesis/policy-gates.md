# Policy Gates

## Purpose

Policy gates define when the workflow kernel may continue automatically, when it
must stop for a human, and when an action is forbidden. They protect the user,
external systems, money, credentials, public surfaces, and production state from
being changed by agent enthusiasm or ambiguous approvals.

The kernel owns the generic policy model and enforcement points. Host adapters
own how approvals are collected from their surfaces. Lane adapters may add
domain-specific policy, but they cannot weaken global hard gates.

## Policy Layers

Policy is layered and the most restrictive applicable rule wins.

1. Global policy: always-on hard gates and forbidden zones.
2. Workflow policy: risk classes and allowed actions for one workflow
   definition.
3. Stage policy: allowed scope, forbidden actions, budgets, and proof required
   for one stage.
4. Adapter policy: what a runtime, surface, host, or lane adapter is allowed to
   invoke.
5. Human approval receipt: a bounded exception for one exact action, if the
   action is approvable.

If two layers disagree, the kernel stops with `needs_human` or `blocked`.

## Hard Human Gates

These actions always require explicit human approval before execution:

- public publish, including Substack, Medium, social posts, public repo
  releases, and public website changes;
- deploys, production mutations, migrations, service restarts, and production
  config changes;
- live trades, broker actions, order placement, order cancellation, account
  changes, and any action that can alter a trading position;
- auth and credential changes, including OAuth, API keys, tokens, permissions,
  account linking, secrets rotation, and login/session changes;
- money movement or spending, including purchases, transfers, subscriptions,
  paid compute above the configured threshold, invoices, and billing changes;
- external sends, including email, SMS, Telegram to others, Slack/Discord
  messages, DMs, calendar invitations, web forms, or API calls that notify an
  outside party;
- destructive changes, including deletion, archival without recovery, file
  overwrites, database mutation, cleanup jobs, and irreversible edits.

An agent, reviewer, script, or adapter receipt may recommend these actions, but
may not approve them. The workflow must create a human gate with the exact
decision ask and evidence refs.

## Risk Classes

| Class | Meaning | Default Action |
| --- | --- | --- |
| `read_only` | Inspect files, logs, docs, local state, or public sources without side effects. | Allow with receipt. |
| `local_draft` | Create or edit local draft artifacts inside declared scope. | Allow with receipt unless destructive. |
| `review_only` | Ask questions, produce verdicts, or mark review state without external effect. | Allow with receipt. |
| `internal_state` | Mutate durable workflow state, queues, receipts, or review surfaces. | Allow only through declared adapters and schemas. |
| `external_effect` | Notify or change something outside the local workflow boundary. | Require human approval. |
| `production_effect` | Deploy, migrate, restart, or mutate production/runtime behavior. | Require human approval. |
| `financial_effect` | Spend, move money, trade, or alter broker/account state. | Require human approval. |
| `auth_effect` | Create, refresh, rotate, expose, or change credentials and permissions. | Require human approval. |
| `destructive_effect` | Delete, overwrite, archive, prune, or irreversibly mutate data. | Require human approval or deny if unrecoverable. |
| `forbidden` | Action is outside policy even with approval in this workflow. | Deny and block. |

Workflows should name the smallest risk class that covers each action. Unknown
or ambiguous side effects are treated as `needs_human`.

## Gate Object

```text
policy_gate.v1
- gate_id
- workflow_id
- instance_id
- stage_id
- stage_run_id
- requested_action
- action_fingerprint
- actor_ref
- adapter_ref
- target_ref
- risk_classes
- allowed_scope
- forbidden_actions
- evidence_refs
- policy_layers
- decision: allow | allow_with_receipt | require_human | deny
- decision_reason
- approval_receipt_ref
- expires_at
- next_owner
- next_action
```

`action_fingerprint` should bind the approval check to the exact target, action
arguments, artifact hashes, and relevant context packet hashes. If those inputs
change, the prior decision is stale.

## Approval Receipt

Hard gates require an approval receipt before execution.

```text
human_approval_receipt.v1
- approval_id
- gate_id
- human_ref
- canonical_surface
- decision: approved | rejected | revise | park
- exact_action_approved
- action_fingerprint
- evidence_refs
- constraints
- created_at
- expires_at
- revoked_at
- transcript_or_message_ref
```

Approval must be explicit, bounded, and tied to the exact action. A general
comment such as "looks good" does not approve publish, deploy, trade, auth,
money, external send, or destructive changes unless the configured human source
records it as an exact approval for that action.

For local end-to-end tests, `Suman(test)` may be represented by a test-only
approval receipt from `local_test_fixture`. These receipts are only valid inside
fixtures, tests, and local review packets. They must be labeled `test_only` and
`non_live`, include an idempotency key and action fingerprint, and must never
authorize public publish, deploy, live trade, auth, money, external send, or
destructive actions.

## Enforcement Points

The kernel evaluates policy:

- when compiling or validating a workflow definition;
- before each stage run starts;
- before every adapter invocation that may create side effects;
- after an A2A verdict, before transitioning;
- before replaying, retrying, or resuming an interrupted action;
- before writing to a human-visible surface if that write is an external send;
- before marking a hard-gated action complete.

Adapters should expose their declared side effects so the kernel can check them
before invocation. If an adapter cannot describe the side effect, the kernel
should block or require a human gate.

## Canonical Human Source

Each workflow should declare the canonical human decision source for gates. It
may be a local Markdown receipt, Obsidian decision card, Telegram approval,
Google Sheet cell, browser approval flow, or another adapter-backed surface.

If surfaces disagree, the kernel does not guess. It should:

- preserve both evidence refs;
- mark the gate `needs_human` or `blocked`;
- ask for one concrete decision;
- avoid executing the side effect until a fresh canonical approval exists.

## Recovery And Replay

Approval receipts are not blanket permissions.

Replay is allowed only when:

- the action fingerprint is unchanged;
- the approval has not expired or been revoked;
- the stage run is still inside the approved scope;
- the adapter can make the action idempotent or prove it was already completed.

Approval becomes stale when source artifacts, target identifiers, action
arguments, risk class, or material context packet hashes change.

Retries after partial failure must record whether the side effect happened,
whether the adapter can safely retry, and whether fresh approval is required.

## Interaction With A2A

A2A stages can improve judgment; they cannot lower policy risk.

Reviewers may:

- identify that a hard gate is approaching;
- recommend approval, rejection, revision, or escalation;
- verify that evidence exists for a human decision;
- block when the producer asks for unsafe side effects.

Reviewers may not:

- approve public publish, deploy, live trade, auth, money movement, external
  send, or destructive changes;
- convert a hard gate into a routine transition;
- rely on transcript prose as proof that approval happened;
- expand the allowed scope because the producer answer sounded confident.

The A2A verdict's `risk_result`, `requested_actions`, and `side_effects` feed
policy evaluation. If they are missing or contradictory, the next transition is
`block` or `needs_human`.

## Example: Bumblebee Quality Review

Policy shape:

- allowed: read handoff, inspect artifacts, ask producer questions, write a
  structured verdict receipt;
- allowed with declared adapter: update internal workflow/review state;
- forbidden without human approval: code mutation, publish, deploy, push,
  merge, credential work, broker/trading action, external send, destructive
  cleanup;
- likely verdict on risky request: `needs_human` if a human decision is needed,
  `block` if the producer tries to cross scope.

Bumblebee can say that a change is ready for a human to approve. Bumblebee
cannot approve or execute the risky action.

## Example: Ivy And Jonah Editorial Loop

Policy shape:

- Ivy may produce P4 draft artifacts after the appropriate earlier gate;
- Jonah may review the P4 package, ask bounded questions, and request one
  bounded revision;
- Jonah may clear the artifact for P5 as `publishable` or
  `publishable_with_minor_edits`;
- P5 remains a human gate for public publish or external send;
- no agent may publish, send externally, mutate the browser publish path, push,
  or deploy as part of the A2A stage.

If Jonah asks for a taste/framing decision, the generic transition is
`needs_human`. If Ivy changes the draft materially after Jonah clearance, the
approval fingerprint changes and a fresh review or human decision is required.

## Example: Trading Research Gate

Policy shape:

- allowed: gather research, produce a thesis, inspect historical data, create a
  draft plan, run local simulations, and ask a reviewer to challenge risk;
- hard gate: live order placement, broker account mutation, order cancellation,
  position sizing that affects live execution, or money movement;
- required receipt: exact account/action/contract/order terms, risk evidence,
  and human approval bound to the action fingerprint.

Research approval is not trade approval. A research reviewer can recommend a
trade thesis for human review, but the execution adapter must still block until
the live-trade gate is approved.

## Example: Deterministic System Action

Policy shape:

- allowed: generate a cleanup plan, calculate affected paths, produce a dry-run
  receipt, and ask a reviewer to check recoverability;
- hard gate: deletion, irreversible archive, overwriting non-generated files, or
  mutation outside the declared worktree;
- approval receipt: exact paths, backup or recovery plan, dry-run hash, and
  expiration.

If the dry-run changes after approval, approval is stale.

## Stop Conditions

Policy evaluation stops automatic progress when:

- a hard human gate is reached without a matching approval receipt;
- requested action or side effect is ambiguous;
- a workflow tries to use a reviewer verdict as human approval;
- approval is stale, expired, revoked, or from the wrong surface;
- adapter side effects exceed declared scope;
- source artifacts changed after approval;
- a forbidden action is requested;
- surfaces disagree on the human decision.

The stop receipt should include the smallest unblock request and evidence refs.

## Minimum Acceptance Tests

An implementation slice for policy gates should prove:

- hard gate actions cannot run without `human_approval_receipt.v1`;
- approvals are bound to action fingerprints and become stale on material
  change;
- A2A verdicts cannot approve hard-gated actions;
- unknown adapter side effects block or require human approval;
- canonical-surface disagreement stops the workflow;
- Bumblebee, Ivy/Jonah, a trading research gate, and a deterministic destructive
  action are expressible without OpenClaw-specific kernel code.
