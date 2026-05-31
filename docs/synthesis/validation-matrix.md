# Validation Matrix

This matrix turns the example workflows into adoption criteria for the Agent
Workflow Kernel. A validation slice passes only if it proves portable kernel
behavior without moving OpenClaw-specific paths, prompts, lane semantics, or
domain engines into the kernel.

## Validation Slices

| Slice | Minimum proof | Non-goal |
| --- | --- | --- |
| Bumblebee quality review | A producer artifact can be reviewed through a bounded probing loop, validated, receipted, and surfaced only when needed. | Making Bumblebee the product center or hiding lane truth in a reviewer prompt. |
| Ivy/Jonah editorial workflow | Ivy can produce/revise, Jonah can review, stale draft/review mismatches block advancement, and P5 stops at Suman. | Publishing externally as part of the editorial loop. |
| Trading research gate | Read-only research and risk checks can produce a decision packet while live execution remains impossible. | Building or replacing a broker execution engine. |
| Radhe review pipeline | A scheduled/resumable deterministic media pipeline can add review gates, QA receipts, owner notes, and publish approval. | Reimplementing Radhe media generation in the kernel. |
| Deterministic system action | Inspect, dry-run, human approval, apply, and readback can run as a single auditable workflow. | Letting agents mutate runtime state without exact approval. |

## Capability Matrix

| Kernel capability | Bumblebee | Ivy/Jonah | Trading research | Radhe | Deterministic action | Validation expectation |
| --- | --- | --- | --- | --- | --- | --- |
| Versioned `WorkflowDef` | Review contract graph | P3 -> P4 -> P5 graph | Research-only graph | Scheduled pipeline graph | Dry-run/apply graph | Same kernel object model describes all five. |
| Recoverable `WorkflowInstance` | Review can block or retry | Stale child sessions are visible | Research can pause at human gate | Long media run can resume | Apply can recover after interruption | Runner state is durable enough to resume or mark blocked. |
| Typed `StageRun` | Review, validate, surface | Draft, review, revision, gate | Research, checks, gate | Pipeline, QA, note, gate | Inspect, dry-run, apply, readback | Every attempt records status, timestamps, inputs, outputs, and next action. |
| `PromptRef` and `ContextPacket` | Reviewer prompt and evidence packet | Ivy/Jonah prompts and packet hashes | Research prompt with read-only policy | RadheOps review prompt and run context | Optional plan-generation prompt | Prompt text is referenced and hashed; rendered context is bounded. |
| `ArtifactRef` | Work product, transcript, verdict | P4 package, Jonah receipt, P5 packet | Data snapshots, thesis, risk memo | Run manifest, video, QA, review package | State snapshot, dry-run diff, readback | Artifacts are addressable, hashed where practical, and tied to receipts. |
| Immutable receipts | `review_verdict.v1` | P4/P5 transition receipts | Research decision packet | QA and owner-note receipts | Mutation and readback receipts | Receipts explain what happened, why, by whom/what, and what remains risky. |
| `a2a_review_loop` | Generic reviewer probes producer | Jonah reviews Ivy | Optional thesis debate | Optional message-quality review | Usually not needed | A2A is available when critique adds value and bounded by budget. |
| `human_gate` | Only for `needs_suman` or risky outcome | P3 and P5 decisions | Required before any execution route | Required before publish by default | Required before mutation | Gate decisions are structured, auditable transitions. |
| `system_action` | Contract/verdict validation | Hash and gate validation | Read-only evidence and risk checks | Pipeline run, QA, package | Inspect, dry-run, apply, readback | Scripts are first-class stages with receipts, not invisible helpers. |
| Policy gates | Reviewer cannot publish or mutate | P5 cannot publish by itself | Broker actions forbidden | Public publish forbidden by default | Mutation forbidden before approval | Policy enforcement is expressed in config and adapter capability checks. |
| Surface adapters | Blackboard/Obsidian review card | OR review card or Telegram handoff | Sheets/Obsidian decision packet | Owner note and approval card | Approval prompt and completion receipt | Surfaces display decisions and receipts; they are not the state store. |
| Host/runtime adapters | OpenClaw native A2A | OpenClaw Work Ledger path | oldmac/live trading read-only context | oldmac Radhe runtime | local/oldmac shell or service manager | Runtime details stay behind adapter contracts. |
| Lane adapters | Rubric and evidence packet | OR P-gate semantics | Strategy/thesis schemas | Run contract and QA schema | Host maintenance schema | Lane adapters prepare domain cargo without changing kernel rail. |
| Parity strategy | Fixture and native smoke | Dual-run current P4 path | Read-only dry-run against current state | Dev run plus live readback | Dry-run/apply/readback fixture | No replacement before equivalent receipts or documented deltas exist. |

## Core Versus Adapter-Specific

### Core Kernel

These are portable and should be owned by the kernel:

| Area | Core responsibility |
| --- | --- |
| Domain model | `WorkflowDef`, `WorkflowInstance`, `StageDef`, `StageRun`, `Transition`, `ArtifactRef`, `Receipt`, `PolicyGate`, and `AdapterInvocation`. |
| Workflow graph | Versioned stages, transitions, stop states, revision budgets, retries, and blocked/done semantics. |
| Runner state | Claiming, leases, attempts, idempotency keys, stale-run handling, retry policy, recovery state, and final status. |
| Prompt/context registry | Prompt references, prompt versions, rendered context packet hashes, and input-size boundaries. |
| Receipts | Immutable evidence with stage, actor, adapter, artifact, policy, provenance, and residual-risk fields. |
| Stage types | `agent_work`, `agent_gate`, `a2a_review_loop`, `human_gate`, `system_action`, `wait_schedule`, `recovery`, and `blocked`. |
| A2A contract | Producer/reviewer roles, question and revision budgets, structured questions/answers/verdicts, transcript refs, and proof requirements. |
| Policy model | Risk classes, hard approval boundaries, capability allowlists, denied actions, and approval binding to plan or artifact hashes. |
| Adapter contracts | Runtime, surface, host, and lane adapter interfaces plus invocation receipts. |
| Validation hooks | Schema validation, artifact existence/hash checks, policy checks, and parity comparison hooks. |

### Adapter Or Lane-Specific

These should stay out of generic kernel code:

| Area | Adapter or lane owner |
| --- | --- |
| OpenClaw runtime | `sessions_send`, `sessions_spawn`, `sessions_yield`, Work Ledger compatibility, Blackboard compatibility, OpenClaw agent ids, and oldmac runtime paths. |
| Surfaces | Obsidian/Northstar rendering, Telegram formatting, Google Sheets ranges, browser staging, Slack later, and local Markdown layout. |
| OR Research | Ivy/Jonah identities, P1-P5 semantics, article packet fields, source trail expectations, and publish-packet handoff behavior. |
| Bumblebee | `quality_reviewer` prompt, skill/rubric selection, review profiles, and native proof extraction details. |
| Trading lanes | Mala/Bhiksha/Kamandal strategy schemas, thesis-exit rules, option-chain logic, broker/provider adapters, and live execution systems. |
| Radhe | Run manifests, media pipeline implementation, QA schema, launchd schedule, Remotion/audio/video tooling, content memory, and publish package details. |
| Host actions | Shell commands, launchd jobs, git/GitHub operations, filesystem mutation plans, rollback scripts, and host-specific verification commands. |

## Adoption Blockers By OpenClaw Lane

| Lane | Blocker | Why it matters | Unblock signal |
| --- | --- | --- | --- |
| Bumblebee / quality review | Native proof validation is not yet a portable primitive. | A reviewer can claim it asked a producer questions; the kernel must trust only structured runtime proof. | Fixture and native smoke produce `review_contract`, trusted proof, transcript ref, and `review_verdict` receipts with matching hashes. |
| Bumblebee / quality review | Blackboard and artifact links can become hard to trace. | Human surfaces need clickable review evidence without becoming the source of truth. | Review card links resolve to the exact artifact-outbox record and review note from a fresh run. |
| Bumblebee / quality review | Reviewer identity could absorb domain ownership. | A generic reviewer should challenge evidence, not own Radhe, trading, OR, or runtime truth. | Lane adapters pass rubrics and criteria while domain agents/systems retain final domain ownership. |
| Ivy/Jonah / OR Research | P1-P5 project ledger semantics must survive migration. | The kernel cannot flatten article workflow gates into generic "review done" state. | A current P3 -> P4 -> Jonah -> P5 fixture maps to kernel stages with equivalent receipts. |
| Ivy/Jonah / OR Research | Stale P4/Jonah hash mismatches must remain hard blockers. | Passing an old editor review against a changed draft is a correctness bug. | Validation blocks stale draft/review pairs and names the repair path. |
| Ivy/Jonah / OR Research | Publish approval and external publishing are separate gates. | P5 quality approval should not silently publish or externally send. | P5 can prepare a packet or spawn a publish workflow, but `externalPublishAllowed` remains false until the explicit publish step. |
| Trading research | Research and live execution must be physically separated. | A workflow that can call a broker is not a no-execution research gate. | Adapter capability policy denies order placement, cancellation, broker auth mutation, and money movement. |
| Trading research | Live runtime truth can differ from local source. | Active strategies, provider status, and trading state are high-risk and drift-prone. | Any adoption claim that touches live trading state includes oldmac/live readback evidence. |
| Trading research | Kernel receipts must not become a second trading engine. | Strategy logic belongs to Mala/Bhiksha/Kamandal or lane adapters, not generic orchestration. | Research receipts carry thesis/risk/evidence fields and route execution only to a separate approved workflow. |
| Radhe | Duplicate schedulers or stale cron paths can create duplicate outputs. | Kernel adoption should reduce operational ambiguity, not add another wakeup source. | A single scheduling owner is declared and verified before enabling kernel-driven runs. |
| Radhe | Long media work can outlive the agent turn. | Timeout is not failure; restarting blindly can waste money or duplicate artifacts. | Run manifests, leases, and readback can distinguish running, failed, complete, and approval-needed states. |
| Radhe | Approvals must bind to exact run ids and packages. | Suman should never approve one video while the system publishes another. | Approval surfaces include run id, artifact refs, QA result, and publish package hash. |
| Deterministic system action | Approval must bind to exact dry-run plan. | Broad approval for "cleanup" or "fix config" is too ambiguous for mutation. | The approved plan hash matches the apply-stage input. |
| Deterministic system action | Readback verification is not optional. | "Command exited 0" does not prove runtime behavior changed. | Done state requires host/surface readback receipt or a blocked receipt with next owner. |
| Deterministic system action | Receipts must preserve audit value without leaking secrets. | Auth, env, broker, and host outputs can contain sensitive material. | Redaction policy is tested on dry-run/apply/readback receipts. |

## Parity Expectations

| Slice | Before replacing current OpenClaw path | Acceptable first proof |
| --- | --- | --- |
| Bumblebee | Current Work Ledger Bumblebee smoke still runs or has an equivalent kernel fixture. | Same artifact packet produces equivalent verdict, status, and human-facing decision card. |
| Ivy/Jonah | Current OR P4 -> Jonah -> P5 path remains available. | Kernel fixture reproduces accept, revise-once, block, and stale-review cases. |
| Trading research | Existing trading runtime remains untouched. | Read-only workflow writes a research packet and proves broker actions are denied. |
| Radhe | Existing Radhe launchd/app path remains the source of truth. | Dev or fixture run proves manifest/QA/approval receipts; live readback proves the adapter can observe a real run. |
| Deterministic action | Existing manual or script path remains available. | Dry-run fixture plus one low-risk live action proves approval binding and readback. |

## Minimum Acceptance Tests For The Kernel Skeleton

- Parse or construct all five `WorkflowDef` examples without custom kernel code.
- Create a `WorkflowInstance` and append multiple `StageRun` attempts.
- Store receipts with prompt/context/artifact/policy/provenance fields.
- Enforce a denied action in the trading research workflow.
- Enforce human approval before deterministic apply.
- Represent a blocked stale-review state in Ivy/Jonah.
- Represent a long-running Radhe stage as running, resumed, complete, or blocked.
- Render a surface-neutral human gate packet that a surface adapter can translate
  to Obsidian, Telegram, local Markdown, or Sheets.
- Produce a parity report that names equivalent receipts or documented deltas for
  an OpenClaw fixture.

## Adoption Order

The safest order is not the same as the product vision. The product vision is the
full portable harness. The adoption order should start where risk and blast
radius are low:

1. Bumblebee quality review as the first A2A receipt/proof slice.
2. Ivy/Jonah editorial flow as the first specialized reviewer/doer loop.
3. Deterministic system action with a harmless apply/readback proof.
4. Radhe review pipeline with dev-mode or no-expensive-generation proof before
   live media runs.
5. Trading research gate as read-only only, with live execution explicitly out of
   scope until a separate approval and broker-adapter design exists.

This sequence keeps OpenClaw behavior intact while proving that the kernel is a
portable rail for multiple kinds of cargo.

## Open Questions

- Should human gate decisions be stored in the kernel state first and mirrored to
  surfaces, or should a host adapter broker the canonical decision source?
- How strict should output schemas be for creative artifacts like articles and
  Radhe messages?
- What is the minimum transcript retention policy for A2A loops?
- Should policy be declared globally, per workflow, per stage, or layered with
  host overrides?
- Which parity report format is most useful for comparing existing OpenClaw
  receipts to kernel receipts?
