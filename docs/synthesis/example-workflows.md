# Example Workflows

These examples are validation slices for the portable kernel vision. They are
not product boundaries. Bumblebee is a useful first proof because it is
low-risk, but the kernel should also express editorial review, read-only trading
research, media pipeline review, and deterministic system actions with human
approval gates.

The common shape is:

```text
WorkflowDef
-> WorkflowInstance
-> StageRun
-> ArtifactRef + Receipt
-> Transition
-> Next stage, HumanGate, Done, or Blocked
```

Each example below names what the slice proves, what should be generic kernel
behavior, what should remain adapter or lane-specific, and what would block
adoption in OpenClaw.

## Example Summary

| Example | Workflow shape | Kernel proof | Final gate |
| --- | --- | --- | --- |
| Bumblebee quality review | Producer artifact -> probing reviewer -> verdict -> optional Suman gate | Generic `a2a_review_loop` with boring receipts | Human only when verdict is `needs_suman` or risk crosses policy |
| Ivy/Jonah editorial workflow | P3 approval -> Ivy P4 -> Jonah review -> bounded revision -> P5 gate | Specialized reviewer/doer loop with revision budget and publish boundary | Suman P5 approval before any publish packet handoff |
| Trading research gate | Hypothesis -> read-only research -> risk review -> human decision | Policy wall that allows research but forbids live execution | Suman gate before any broker or trade action, which is outside this workflow |
| Radhe review pipeline | Schedule/resume -> message review -> generation -> QA -> approval surface | Deterministic pipeline plus agent review and artifact-heavy receipts | Suman publish approval until lane policy explicitly changes |
| Deterministic system action | Inspect -> plan/dry-run -> human gate -> apply -> readback | Scripted side effects with approval, idempotency, and recovery | Human approval before external, destructive, deploy, auth, or production mutation |

## Bumblebee Quality Review

### Intent

Use Bumblebee as a generic probing reviewer for a work product that another
agent or script produced. The point is not "Bumblebee as the kernel"; the point
is to prove the kernel can run a bounded reviewer/producers loop, preserve proof,
and expose only the actionable result to the operator surface.

### Sketch

```text
trigger
-> build_review_contract
-> a2a_review_loop
-> validate_review_verdict
-> write_receipts
-> publish_review_surface_if_needed
-> done | human_gate | blocked
```

Suggested stages:

| Stage | Type | Output |
| --- | --- | --- |
| `build_review_contract` | `system_action` | `review_contract.v1` with work id, producer, reviewer, source artifacts, criteria, forbidden actions, and question budget |
| `seed_or_claim_producer` | `agent_work` or adapter call | Producer session or artifact packet reference |
| `probe_with_bumblebee` | `a2a_review_loop` | `review_question.v1`, `review_answer.v1`, transcript reference, and `review_verdict.v1` |
| `validate_verdict` | `system_action` | Schema and policy validation receipt |
| `surface_decision` | `human_gate` only when required | Obsidian, Telegram, or local Markdown review card |

### What It Proves

- The kernel can express a reusable review protocol without hard-coding
  Bumblebee.
- A reviewer can ask sequential questions within a budget instead of dumping a
  static checklist.
- Runtime proof and transcript references can be kept separate from human-facing
  summaries.
- A review can finish as `pass`, `refine`, `block`, or `needs_suman` without
  hiding the next owner or next action.
- Surface updates are effects of receipts, not the source of truth.

### Core Kernel Features

- `a2a_review_loop` stage type.
- `PromptRef` and `ContextPacket` for reviewer instructions and bounded inputs.
- `ArtifactRef` for work products, transcripts, and verdict receipts.
- Question, answer, and verdict schemas.
- Question budgets, timeouts, stop conditions, and revision limits.
- Policy checks that prevent reviewer escalation into publish, trade, deploy,
  auth, credential, or destructive actions.

### Adapter Or Lane-Specific Features

- OpenClaw `quality_reviewer` agent identity and Bumblebee prompt.
- OpenClaw-native A2A proof such as `sessions_send` evidence.
- Work Ledger compatibility records.
- Obsidian/Blackboard rendering and artifact-outbox paths.
- Lane rubrics, for example cleanup review, prompt review, or code review.

### OpenClaw Adoption Blockers

- Native A2A proof must be trusted only from structured runtime/tool events, not
  from prompt text or reviewer-returned JSON.
- Existing Work Ledger receipts need schema parity with the portable receipt
  model.
- Blackboard links must reliably point to review notes and artifact records.
- Bumblebee must stay a generic reviewer plus skills/rubrics, not become the
  place where lane ownership or domain truth is hidden.
- Stale child sessions need recoverable `blocked` or `needs_human` state rather
  than silent hanging work.

## Ivy/Jonah Editorial Workflow

### Intent

Model the OR Research editorial path where Ivy produces or revises a public
article package, Jonah reviews it, Ivy may make one bounded revision, and the
workflow stops at Suman's final P5 gate before any external publishing or handoff
to a browser staging path.

### Sketch

```text
p3_approved
-> ivy_build_p4_package
-> jonah_editor_review
-> ivy_revision_if_requested
-> validate_current_p4_and_review_hashes
-> p5_human_gate
-> done_for_publish_packet_workflow
```

Suggested stages:

| Stage | Type | Output |
| --- | --- | --- |
| `accept_p3_approval` | `human_gate` transition | Receipt that P3 was approved and exact source packet is selected |
| `ivy_build_p4_package` | `agent_work` | P4 draft package, source trail, caveats, and artifact refs |
| `jonah_editor_review` | `a2a_review_loop` | Editorial verdict, questions, requested revision, and transcript ref |
| `ivy_revision` | `agent_work` | Revised P4 package, bounded by `max_revisions` |
| `validate_editorial_state` | `system_action` | Hash match between current P4 and Jonah review receipt |
| `p5_final_approval` | `human_gate` | Suman decision: approve, revise, park, or kill |

Publishing is intentionally outside this workflow. P5 approval can feed a
separate publish-packet workflow, but the kernel should keep the external send
or browser staging action behind another explicit gate.

### What It Proves

- The kernel supports specialized domain agents, not only generic reviewers.
- A reviewer/doer loop can include bounded revision and re-review without
  unbounded ping-pong.
- Human decisions can be modeled as stage transitions, not ad hoc comments.
- A stale review can be blocked when it no longer matches the current draft.
- Public publishing remains a hard policy zone even when article quality passes.

### Core Kernel Features

- Versioned workflow graph with conditional transitions.
- `a2a_review_loop` plus revision budget.
- `human_gate` stage with named decisions and receipt fields.
- Artifact hash/provenance validation before gate advancement.
- Policy gate separation between "ready for final approval" and "allowed to
  publish externally."

### Adapter Or Lane-Specific Features

- OR Research P1-P5 gate names and project ledger semantics.
- Ivy and Jonah agent identities.
- Article packet fields, source-trail expectations, visual decision artifacts,
  and editorial rubric.
- Telegram handoff or browser staging plan after a later publish decision.
- OpenClaw Work Ledger and Blackboard compatibility surfaces.

### OpenClaw Adoption Blockers

- Current OR project-ledger state must map cleanly to kernel instances without
  losing existing P1-P5 semantics.
- Jonah review receipts must be tied to the exact current P4 draft hash; stale
  review guards should remain blockers, not be weakened.
- P5 approval must not silently publish. It should prepare or hand off a packet
  only when the publish workflow has its own explicit approval boundary.
- The canonical human decision source must be unambiguous when Obsidian,
  Telegram, and Work Ledger state disagree.
- Live Ivy/Jonah session failures need retry, repair, or `needs_human` receipts
  instead of hidden child-session drift.

## Trading Research Gate With No Live Execution

### Intent

Model a research-only trading lane that can collect evidence, challenge a
thesis, and ask Suman for a decision while making live execution impossible in
this workflow. This slice is for Mala, Bhiksha, Kamandal, or future trading
research adoption, but it does not call a broker or place trades.

### Sketch

```text
research_intake
-> normalize_hypothesis
-> gather_read_only_evidence
-> deterministic_risk_checks
-> reviewer_or_agent_debate
-> human_research_gate
-> done_without_execution
```

Suggested stages:

| Stage | Type | Output |
| --- | --- | --- |
| `normalize_hypothesis` | `agent_work` | Thesis, assumptions, instruments, time horizon, and forbidden actions |
| `gather_read_only_evidence` | `system_action` or adapter call | Market data snapshots, filings, option-chain summaries, backtest references, or research notes |
| `risk_check` | `system_action` | Buying-power, liquidity, thesis-exit, exposure, and data-freshness checks, all read-only |
| `challenge_thesis` | `a2a_review_loop` or `agent_gate` | Bull/bear critique, missing evidence, and residual risk |
| `suman_research_decision` | `human_gate` | Approve more research, park, reject, or explicitly route to a separate execution workflow |

The workflow definition should carry an explicit capability policy such as:

```text
allowed: read_market_data, read_research_docs, write_research_receipts
forbidden: place_order, cancel_order, modify_order, transfer_money,
           change_broker_auth, enable_live_strategy
```

### What It Proves

- The kernel can run high-value research in a high-risk domain without giving
  the workflow live execution capability.
- Policy is enforceable by stage capability, adapter allowlist, and receipt
  validation, not by trusting an agent instruction alone.
- Deterministic scripts can participate as evidence and risk checks.
- A human gate can route to a different workflow without smuggling live trading
  into the research graph.

### Core Kernel Features

- Policy gates and capability allowlists.
- Read-only adapter invocation receipts.
- Separation between research artifacts and execution actions.
- Artifact provenance, data freshness, and source timestamps.
- Human gate decisions that can stop, continue research, or spawn a separate
  explicitly-approved workflow.

### Adapter Or Lane-Specific Features

- Trading provider adapters, broker data adapters, and market-data schemas.
- Mala/Bhiksha/Kamandal strategy definitions, thesis-exit policy, and option
  chain interpretation.
- Google Sheets, Obsidian, or local Markdown research surfaces.
- Live runtime verification on oldmac when the lane touches active trading
  state.

### OpenClaw Adoption Blockers

- The kernel must not blur read-only research with Bhiksha or broker execution.
- Live-vs-local truth for active strategies, provider status, and trading state
  must be verified through the runtime surface before any adoption claim.
- Broker credentials and auth refresh paths must remain outside this workflow
  unless a separate human-gated auth workflow is created.
- Research receipts need enough structured data to be useful to trading systems
  without becoming a parallel trading engine.
- Existing no-capital shadow or research lanes should not be blocked by a new
  validation choke point once their existing pass criteria are satisfied.

## Radhe Review Pipeline

### Intent

Model Radhe as a supervised content-production lane with deterministic media
artifacts, resumable stages, quality checks, and a human approval surface before
public publishing. The highest-leverage review can happen before expensive
audio/video work, while final review uses the generated package.

### Sketch

```text
scheduled_or_manual_trigger
-> load_lane_contract
-> pre_generation_message_review
-> run_or_resume_media_pipeline
-> qa_and_package_review
-> publish_owner_note
-> human_publish_gate
-> done_without_public_publish | route_to_publish_workflow
```

Suggested stages:

| Stage | Type | Output |
| --- | --- | --- |
| `load_lane_contract` | `system_action` | Run contract, lane config, recent memory refs, and current run id |
| `pre_generation_message_review` | `a2a_review_loop` or `agent_gate` | Message verdict before expensive generation |
| `run_or_resume_pipeline` | `system_action` | Run manifest, stage artifacts, and media outputs |
| `qa_package` | `system_action` | `qa.json`, review markdown, publish metadata, and artifact refs |
| `owner_note` | `surface_adapter` | Obsidian or local Markdown owner brief and optional Blackboard pointer |
| `publish_approval` | `human_gate` | Approve, reject, revise, skip, or comment |

### What It Proves

- The kernel is not limited to text artifacts or A2A loops.
- Long deterministic stages can be represented with run manifests, leases,
  recovery state, and artifact receipts.
- Review can happen both before expensive work and after final package creation.
- Human review surfaces can stay sparse: show approval-needed or blocked states,
  not every routine successful stage.
- Public publish/upload remains a policy-gated workflow boundary.

### Core Kernel Features

- `system_action`, `wait_schedule`, `recovery`, and `human_gate` stages.
- Stage leases, resumability, retry metadata, and blocked-state receipts.
- Large artifact references with hashes and stable paths.
- Surface adapter contract for approval cards and owner notes.
- Policy gates for public publishing and high-cost compute.

### Adapter Or Lane-Specific Features

- Radhe run manifest, pipeline stage names, QA schema, and publish package.
- Launchd schedule and host wrapper details.
- Media generation, audio/video tools, Remotion rendering, and storage layout.
- Radhe Ops voice, lane memory, content constitution, and approval semantics.
- Telegram/Obsidian review card formatting.

### OpenClaw Adoption Blockers

- Runtime truth for Radhe lives in the live app and oldmac paths, not in a
  kernel fixture.
- Duplicate scheduling surfaces must be eliminated before kernel adoption, or
  the same run can produce duplicate approval messages.
- Long-running media stages need safe resume/readback semantics; a timed-out
  agent turn does not necessarily mean the pipeline failed.
- Review artifacts must identify the exact run id and package so approvals do
  not apply to stale output.
- Public publishing must stay disabled by default until Suman explicitly changes
  lane policy.

## Deterministic System Action With Human Final Gate

### Intent

Model a workflow where scripts inspect state, produce a dry-run plan, pause for
human approval, apply an idempotent mutation, and read back the actual effect.
This proves that the kernel coordinates deterministic side effects as first-class
workflow stages, not just agent conversations.

Example actions could include pruning stale generated artifacts, updating a
launchd job, moving a deprecated cron path to a cleanup-only wrapper, or applying
a safe config migration. The exact domain is adapter-specific; the gate pattern
is portable.

### Sketch

```text
inspect_current_state
-> propose_plan
-> dry_run
-> human_final_gate
-> apply_action
-> readback_verify
-> done | rollback_or_blocked
```

Suggested stages:

| Stage | Type | Output |
| --- | --- | --- |
| `inspect_current_state` | `system_action` | Read-only state snapshot and source-of-truth declaration |
| `propose_plan` | `agent_work` or `system_action` | Structured plan, risk class, rollback expectation, and affected paths |
| `dry_run` | `system_action` | No-op diff, commands to be run, and predicted changes |
| `approval` | `human_gate` | Approve, revise plan, reject, or defer |
| `apply_action` | `system_action` | Mutation receipt with command, inputs, outputs, and redacted sensitive fields |
| `readback_verify` | `system_action` | Host or surface readback proving the effect landed |

### What It Proves

- The kernel can represent deterministic work that must not proceed on agent
  judgment alone.
- Approval gates can sit immediately before a side effect and bind to an exact
  dry-run plan.
- Readback verification is part of done, not optional follow-up.
- Destructive, deploy, auth, external-send, and production mutation policies can
  be enforced consistently across lanes.

### Core Kernel Features

- `system_action` with dry-run/apply/readback modes.
- Policy gate binding to a specific plan hash.
- Idempotency keys and recovery state for retries.
- Receipt redaction rules for secrets and sensitive outputs.
- Rollback or blocked transitions when readback does not match the plan.

### Adapter Or Lane-Specific Features

- Shell, host, GitHub, browser, launchd, or filesystem adapters.
- Host-specific paths and service managers.
- Domain-specific validation commands.
- Surface-specific approval prompts.
- Rollback scripts and operational runbooks.

### OpenClaw Adoption Blockers

- The canonical runtime host and source of truth must be explicit before any
  mutation.
- Human approval must apply to an exact dry-run plan, not a broad class of future
  commands.
- Secrets must be redacted from receipts while preserving enough evidence for
  audit.
- Existing runtime dirt or user changes must be preserved; the adapter must
  distinguish intended mutation from unrelated local state.
- Readback failures need a blocked receipt with next owner and repair path, not
  a false success.

## Cross-Example Implications

These examples imply that the portable kernel should optimize for a small set of
boring primitives:

- versioned workflow graphs;
- typed stages and transitions;
- durable workflow instance state;
- prompt and context packet references;
- artifact and receipt provenance;
- policy gates with explicit approvals;
- runner claim, lease, retry, and recovery mechanics;
- adapter contracts for runtimes, surfaces, hosts, and lane-specific domain
  engines.

They also imply what should not move into the kernel:

- OpenClaw paths, agent names, and session APIs;
- OR Research P-gate semantics;
- Radhe media-generation internals;
- trading strategy logic or broker execution;
- Blackboard, Telegram, Obsidian, or Sheets formatting;
- lane prompts and rubrics beyond references and hashes.

The kernel is the generic rail. The examples are cargo that should fit without
forcing the rail to become OpenClaw, Bumblebee, Radhe, or a trading engine.
