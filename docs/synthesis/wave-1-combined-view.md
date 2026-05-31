# Wave 1 Combined View

Status: supervisor synthesis
Last updated: 2026-05-31

## Bottom Line

Wave 1 converged on a robust shape: the Agent Workflow Kernel should be a
portable, durable workflow state machine. It is not OpenClaw, not an agent
chat harness, and not a surface-specific application.

The kernel owns the generic rail:

- versioned workflow graphs;
- workflow instances and stage attempts;
- prompt references and rendered context packet provenance;
- immutable receipts and artifact references;
- runner claim, lease, retry, and recovery state;
- policy gates and approval receipts;
- adapter contracts.

Adapters and lanes own the cargo:

- OpenClaw sessions, Work Ledger compatibility, Blackboard, Telegram,
  Obsidian/Northstar, launchd, and oldmac paths;
- OR Research P-gate semantics;
- Jonah/Ivy editorial rubrics;
- Bumblebee lane rubrics;
- Radhe media internals;
- trading research logic and broker execution boundaries.

## Kernel Shape

The product should be built around these core objects:

| Object | Kernel Responsibility |
| --- | --- |
| `WorkflowDef` | Versioned graph, declared inputs, stages, transitions, defaults, compatibility hints. |
| `StageDef` | Stage type, adapter binding, actors, input selectors, output contract, policy, budget, retry hints. |
| `WorkflowInstance` | One started workflow with input hash, current stage, status, history, and parent/child links. |
| `StageRun` | One attempt with claim/lease state, adapter invocation, output refs, receipt refs, failure class, retry lineage. |
| `PromptRef` | Versioned pointer to identity, policy, lane, or stage prompts. |
| `ContextPacket` | Per-run facts assembled from inputs, artifacts, receipts, approvals, and selected state. |
| `Receipt` | Immutable proof of what happened, what was seen, what changed, checks run, prompts used, residual risk, and next action. |
| `PolicyGate` | Risk decision and, when needed, a required human approval receipt for one exact action. |
| `AdapterInvocation` | Portable record of a runtime, surface, host, or lane adapter call. |

The runner should store this in SQLite first, with JSON exports for fixtures,
read models, and adapter interchange.

## Workflow Definition

Start with YAML-authored workflow files that compile into canonical JSON:

- YAML is the operator-editable surface.
- Canonical JSON is the hashable, schema-validated runtime format.
- A Python builder can come later only if it emits the same canonical schema.

The DSL should stay declarative. It can name stages, transitions, actors,
policies, outputs, prompt refs, budgets, and adapters. It should not embed
OpenClaw paths, Python logic, trading logic, Radhe internals, or prompt text.

## Stage Types

Wave 1 recommends these first stage types:

| Stage type | Purpose |
| --- | --- |
| `agent_work` | One agent or model produces an artifact, answer, plan, patch, draft, or report. |
| `agent_gate` | One agent evaluates an artifact and returns a structured decision. |
| `a2a_review_loop` | Producer and reviewer exchange bounded questions, revisions, proof, and verdict. |
| `human_gate` | Workflow waits for a human decision from a configured surface. |
| `system_action` | Deterministic script, command, browser action, or host action. |
| `wait_schedule` | Time, event, dependency, or backoff wait. |
| `recovery` | Stale lease repair, interrupted child-session analysis, parity backfill, or resume step. |
| `blocked` | Parked terminal state with a precise unblock request. |

## Prompt And Context Contract

Prompts should move out of code and into a registry. A stage run resolves a
set of prompt refs, renders a context packet, invokes a runtime, and writes a
receipt with prompt provenance.

Recommended prompt layers:

1. standing identity prompt;
2. policy envelope;
3. lane prompt;
4. stage prompt;
5. per-run context packet.

Receipts must capture prompt ids, versions, content hashes, rendered input
digest, context packet digest, model/runtime, and tool permissions. OpenClaw
`AGENTS.md` files and skills can be imported by an adapter as registered
prompt sources without becoming kernel primitives.

## Runner And Storage

The runner is the durable executor, not the brain of the domain. Its loop is:

1. sweep stale leases and child sessions;
2. atomically claim one eligible stage run;
3. render context from immutable definitions and current state;
4. invoke one adapter;
5. validate output, artifacts, policy, and deterministic checks;
6. write receipts and artifact refs;
7. transition, retry, block, or wait for human approval.

The failure taxonomy must distinguish:

- runtime failure;
- invalid output;
- human rejection;
- policy denial;
- dependency unavailable;
- deterministic test failure;
- stale lease or interrupted child work;
- unknown side-effect state.

Retries are safe only when the adapter can prove idempotency or no external
effect occurred. Otherwise the workflow stops for human approval or blocks.

## Adapter Boundary

The kernel should define four adapter families:

| Adapter family | Responsibility |
| --- | --- |
| Runtime adapter | Execute agents, model calls, shell processes, browser sessions, or child sessions. |
| Surface adapter | Create and read back human-facing review packets and decisions. |
| Host adapter | Resolve environment, scheduler, locks, filesystem roots, local capabilities, and remote host details. |
| Lane adapter | Translate domain cargo to and from kernel work without leaking domain engines into the kernel. |

This boundary lets the same kernel run in OpenClaw first, and later in a
different Codex setup or standalone agent harness.

## A2A Position

A2A is useful as a stage type, not as the whole architecture. Use it when a
producer has an artifact, a reviewer has a distinct rubric, and the interaction
can be bounded by question and revision budgets.

Do not use A2A for deterministic checks, unbounded brainstorming, rubber
stamps, final approval, or actions that must be approved by Suman.

The A2A receipt should include producer, reviewer, contract, proof, transcript
refs, artifact hashes, verdict, questions, answers, revisions, stop condition,
and residual risk.

## Policy Gates

Global policy must be stronger than workflow and lane policy. These actions
always require explicit human approval before execution:

- public publish;
- deploy or production mutation;
- live trade or broker/account action;
- auth, token, credential, or permission change;
- money movement or spending;
- external send;
- destructive or irreversible change.

Approval must bind to one exact action fingerprint. A general "looks good"
does not approve a hard gate unless the configured human source records that
exact action approval.

## Vision Versus MVP

The right posture is:

Build for the full portable kernel vision, validate through low-risk slices.

That means the first implementation should not be a Bumblebee-only tool. It
should implement generic workflow, stage, receipt, prompt, policy, and adapter
contracts, then prove them with Bumblebee as the first low-risk slice.

## Wave 2 Implementation Plan

Use the AWK Codex project for new threads. Do not migrate the completed Wave 1
threads; their work is merged and recorded.

Recommended Wave 2 worktrees:

| Worktree | Goal |
| --- | --- |
| `core-schema-dsl` | Implement Pydantic/dataclass schema models, YAML loader, canonical JSON compiler, and validation for the five example workflows. |
| `sqlite-ledger-runner` | Implement SQLite migrations, repository API, atomic claim/lease, append-only event/receipt storage, and recovery sweep skeleton. |
| `prompt-context-receipts` | Implement prompt registry layout, context packet renderer, content hashing, and receipt provenance structs. |
| `policy-engine` | Implement risk classes, gate decisions, action fingerprints, approval receipts, and hard-gate enforcement tests. |
| `adapter-spi-local` | Implement adapter interfaces plus local fake runtime, fake surface, and deterministic script adapters for tests. |
| `examples-fixtures` | Convert the five example workflows into runnable fixtures and acceptance tests. |

OpenClaw integration should be Wave 3 unless a Wave 2 test needs a read-only
compatibility fixture. The first OpenClaw adapter should wrap existing behavior
instead of replacing it.

## Acceptance Gate Result

Architecture Gate A1 passes at design level. The merged docs can express:

- Bumblebee quality review;
- Ivy/Jonah editorial A2A loop;
- trading research human gate with no live execution;
- Radhe-style generation/review pipeline;
- deterministic system action with human final gate.

Implementation Gate I1 is not started yet. It becomes the first Wave 2 target.

Parity Gate P1 is intentionally deferred until the OpenClaw adapter exists.

## Open Decisions

- Choose exact Python package shape and dependency policy.
- Decide whether schema models use Pydantic or standard dataclasses plus
  JSON Schema.
- Decide canonical human decision source when multiple surfaces disagree.
- Set transcript retention policy for A2A.
- Decide how strict output schemas should be for creative artifacts.
- Decide when OpenClaw Work Ledger compatibility becomes a wrapper versus a
  migration target.
