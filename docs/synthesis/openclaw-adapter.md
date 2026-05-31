# OpenClaw Adapter Boundary

Status: Wave 1 synthesis

OpenClaw is the first reference host for the Agent Workflow Kernel. It has real
operator surfaces, real Work Ledger and Blackboard lessons, and real A2A proof
requirements. It should prove the kernel contracts, not become the kernel.

The OpenClaw adapter boundary keeps this split:

```text
Kernel: workflow graph, stage runs, receipts, policy, recovery contracts.
OpenClaw adapter: agents, sessions, Work Ledger compatibility, Blackboard,
host paths, launchd/cron, Telegram/Obsidian, lane engines.
```

## Boundary Requirements

The portable kernel must not contain:

- `/Users/sunny` or oldmac path assumptions;
- Northstar or Obsidian path assumptions;
- Telegram account, bot, or delivery assumptions;
- OpenClaw agent ids, session keys, workspace layout, cron ids, launchd labels,
  or command names;
- OR Research, Bumblebee, Ivy, Jonah, Radhe, Kamandal, Mala, or other lane names
  in generic code.

The OpenClaw adapter may know all of those things, but only behind adapter
configuration and wrapper calls.

## Current OpenClaw Assets To Ground The Adapter

Read-only inspection found these useful current paths in `openclaw-core`:

| Area | Current asset | Boundary use |
| --- | --- | --- |
| Work Ledger | `config/work_ledger.json` | Source of current compatibility config, agent ids, risk policy, legacy entrypoints, and backend defaults. |
| Work Ledger store | `workspace-main/scripts/work_ledger/store.py` | Proven SQLite state model for work items, receipts, handoffs, interactions, and turns. |
| Lane adapter shape | `workspace-main/scripts/work_ledger/adapters/base.py` | Existing OpenClaw lane adapter contract for work open, receipts, handoffs, stop-for-human, block-work. |
| OpenClaw agent bridge | `workspace-main/scripts/work_ledger/adapters/openclaw_agent.py` | Current shell/native-session wrapper plus trusted `native_session_proof.v1` parsing. |
| OR Research adapter | `workspace-main/scripts/work_ledger/adapters/or_research.py` | Compatibility layer from review handoffs and OR project ledger into Work Ledger. |
| A2A adapters | `workspace-main/scripts/work_ledger/adapters/bumblebee.py`, `ivy_jonah.py` | Current reviewer/doer loops and native A2A validation flows. |
| Work Ledger CLI | `workspace-main/scripts/work_ledger/cli.py` | Best compatibility entrypoint for current POCs and runner actions. |
| Decision ingestion | `workspace-main/scripts/ingest_agent_reviews.py` | Deterministic bridge from checked review notes to receipts and lane-local handoffs. |
| Blackboard bridge | `workspace-main/docs/blackboard_event_bridge.md` | Target separation of source artifacts, events, read model, generated Blackboard, decision receipts, and handoffs. |
| Artifact outbox | `workspace-main/scripts/artifact_outbox.py`, `workspace-main/docs/artifact_outbox.md` | Local receipt pattern for human-visible artifacts and delivery/readback metadata. |
| Host runner | `scripts/run_blackboard_decision_ingester.sh` | Current deterministic launchd-friendly path that ingests decisions and triggers routed work only when needed. |

## Adapter Components

### OpenClawHostAdapter

Owns OpenClaw host facts:

- OpenClaw root and workspace root resolution;
- local versus remote execution, including oldmac;
- safe path expansion and redaction;
- launchd and OpenClaw cron boundaries;
- Work Ledger state root;
- host healthchecks and capability checks;
- wrappers for deterministic scripts.

The kernel asks for logical operations such as `schedule decision ingester` or
`run compatibility CLI`. The host adapter resolves whether that means launchd,
OpenClaw cron, local shell, SSH, or a no-op fixture.

Host adapter configuration should carry:

```text
host_id
openclaw_root
workspace_main_root
state_root
agent_runtime_root
default_remote_host
allowed_commands
scheduler_policy
surface_roots
redaction_policy
```

None of those fields belong in generic kernel definitions.

### OpenClawRuntimeAdapter

Owns agent and runtime execution:

- `openclaw agent --agent ... --session-key ... --json` compatibility calls;
- future native `sessions_spawn`, `sessions_send`, `sessions_yield`, and Task
  Flow backends;
- shell/script runs when an OpenClaw wrapper is the runtime;
- native session proof collection;
- stale child session inspection.

Current compatibility can wrap `OpenClawAgentClient`. The important behavior to
preserve is not the class name; it is the receipt contract:

- capture agent id as adapter-local metadata;
- capture session id/session key as runtime refs;
- return raw OpenClaw run status and redacted output;
- collect `native_session_proof.v1` from trusted tool-call events or explicit
  `response_item` wrappers;
- reject prompt-only mentions of `sessions_send` as proof.

The runtime adapter should expose these operations to the kernel:

```text
invoke_agent(stage_run, agent_ref, context_packet) -> AdapterResult
invoke_script(stage_run, command_ref, input_packet) -> AdapterResult
collect_native_session_proof(runtime_ref, producer_runtime_ref) -> AdapterReceipt
wait_for_child(runtime_ref) -> AdapterResult
cancel_runtime(runtime_ref, reason) -> AdapterReceipt
```

### OpenClawSurfaceAdapter

Owns human-visible surfaces:

- Blackboard/Review Inbox generation and validation;
- Obsidian review notes and checked decisions;
- Telegram handoff messages and receipts;
- local Markdown packets and browser staging plans;
- artifact outbox records and readback.

The kernel should see surface operations as logical publishes and decision
ingests. OpenClaw decides which script, vault, account, or note path implements
the surface.

OpenClaw surface adapters should wrap current pieces:

- `scripts/ingest_agent_reviews.py --apply --refresh-blackboard --validate`
  for checked review decisions;
- `workspace-main/scripts/artifact_outbox.py record|verify-file` for visible
  artifact receipts;
- Blackboard refresh/validation scripts through host wrappers;
- Telegram handoff delivery only when the policy gate allows the exact send.

Surface adapters must preserve the current safety split:

- Blackboard is a generated attention view, not the source of truth;
- Telegram can deliver a handoff, but does not become the workflow ledger;
- P5 or review approval can authorize internal packet preparation without
  authorizing public publish;
- external send/publish remains a separate explicit approval.

### OpenClawLaneAdapter

Owns OpenClaw lane compatibility:

- OR Research P1-P5 project lifecycle;
- Bumblebee generic review work;
- Ivy/Jonah editorial A2A;
- Radhe/Kamandal/Mala-style future lanes;
- current Work Ledger handoff conventions;
- lane-specific domain engines.

The first lane adapters should wrap existing Work Ledger compatibility instead
of reimplementing domain logic.

Current compatibility calls the OpenClaw adapter should be able to make:

```bash
python3 workspace-main/scripts/work_ledger/cli.py config
python3 workspace-main/scripts/work_ledger/cli.py adapter-plan --adapter or_research
python3 workspace-main/scripts/work_ledger/cli.py audit-editorial-path
python3 workspace-main/scripts/work_ledger/cli.py run-next-or-review-handoff \
  --handoff-root workspace/agents/or_research/handoffs/review_decisions \
  --runtime-root workspace/agents/or_research
python3 scripts/ingest_agent_reviews.py --apply --refresh-blackboard --validate
```

The adapter may also call `scripts/run_blackboard_decision_ingester.sh` as a
host-level deterministic wrapper when running the whole current ingestion path.

## Mapping To Kernel Contracts

| Kernel contract | OpenClaw implementation |
| --- | --- |
| `HostAdapter` | `OpenClawHostAdapter` resolves OpenClaw root, remote host, scheduler, state roots, scripts, and safe command execution. |
| `RuntimeAdapter` | `OpenClawRuntimeAdapter` wraps current shell agent calls now and native session/Task Flow primitives later. |
| `SurfaceAdapter` | `OpenClawSurfaceAdapter` wraps Blackboard, Review Inbox, artifact outbox, review-note ingestion, Telegram handoffs, and browser staging packets. |
| `LaneAdapter` | `OpenClawLaneAdapter` wraps Work Ledger lane adapters and domain engines such as OR project ledger. |
| `AdapterReceipt` | Work Ledger receipts, artifact outbox records, review ingest receipts, native session proofs, and host health receipts are normalized into kernel receipt shape. |
| `SurfacePacket` | Review note, Blackboard item, Telegram handoff, local packet, or browser staging plan. |
| `PolicySnapshot` | Existing OpenClaw risk config plus kernel policy gate result. |

## Reuse, Wrap, Avoid

### Reuse

Reuse these ideas and contracts:

- Work Ledger's boring state model: work item, receipt, handoff, interaction,
  turn, next broker, final approval required.
- The `WorkLedgerLaneAdapter` shape: open work, record receipt, create handoff,
  stop for human, block work.
- The split between deterministic scripts, agent sessions, human gates, and
  generated surfaces.
- The Blackboard event bridge model: source artifacts -> append-only events ->
  read model -> generated surface -> decision receipt -> owning-agent handoff.
- Artifact outbox as the pattern for local visible-output receipts.
- `native_session_proof.v1` as the initial proof standard for OpenClaw A2A
  until the kernel defines a host-neutral proof envelope.

### Wrap

Wrap these in OpenClaw adapters:

- `workspace-main/scripts/work_ledger/cli.py` for current compatibility actions.
- `workspace-main/scripts/work_ledger/adapters/openclaw_agent.py` for current
  OpenClaw agent invocation and session proof.
- `workspace-main/scripts/ingest_agent_reviews.py` for checked review-note
  decisions.
- `workspace-main/scripts/artifact_outbox.py` for visible artifacts.
- `scripts/run_blackboard_decision_ingester.sh` for launchd-friendly decision
  ingestion and Work Ledger pickup.
- OR Research `or_project_ledger.py` through the existing OR Work Ledger
  adapter, not directly from the kernel.
- Blackboard refresh and validation behind a surface adapter.
- Telegram or browser-staging handoff code behind surface operations and
  explicit policy gates.

### Avoid

Do not move these into the kernel:

- OpenClaw command lines or `openclaw` CLI assumptions.
- `/Users/sunny`, oldmac, vault, or runtime-root paths.
- Northstar/Obsidian path logic.
- Telegram account ids, delivery retries, or message formatting.
- OpenClaw cron or launchd labels.
- Shell-based ping-pong as the generic A2A runtime.
- Direct use of OpenClaw's SQLite schema as the portable storage schema.
- Prompt-text parsing as runtime proof.
- Raw Obsidian markdown scraping as authorization.
- Lane-specific P1-P5, Bumblebee, Ivy/Jonah, Radhe, Mala, or Kamandal behavior
  in kernel core.
- External publish/send/trade/deploy behavior as a side effect of internal
  review approval.

## Compatibility Flow Examples

### Checked Review Decision To Work Ledger

```text
SurfaceAdapter.ingest_decisions
  -> OpenClaw wraps ingest_agent_reviews.py
  -> review decision receipt is written
  -> lane-local handoff appears
  -> OpenClawLaneAdapter calls Work Ledger runner
  -> Work Ledger receipt becomes kernel AdapterReceipt
  -> SurfaceAdapter refreshes/readbacks Blackboard if needed
```

### Native A2A Review

```text
RuntimeAdapter.invoke_agent starts/uses producer session
RuntimeAdapter.invoke_agent starts/uses reviewer session
Reviewer uses native sessions_send when asking producer
RuntimeAdapter.collect_native_session_proof verifies trusted tool event
LaneAdapter validates verdict and artifacts
Receipt records transcript refs, proof, checks, and next broker
Kernel transition moves to next stage or human gate
```

### OR Research P5 Publish Packet

```text
Human approval receipt authorizes packet preparation only
OpenClawLaneAdapter wraps Work Ledger OR path
Work Ledger prepares publish packet and browser staging plan
SurfaceAdapter emits Telegram/browser handoff if explicitly allowed
externalPublishAllowed remains false
Final public publish requires a separate explicit approval and receipt
```

## Parity And Migration Strategy

The OpenClaw adapter should be introduced as a compatibility facade first.

1. Fixture-run against current Work Ledger examples.
2. Dual-run low-risk Bumblebee review and Ivy/Jonah editorial A2A paths.
3. Compare normalized receipts, final states, and human-surface readback.
4. Document deltas as adapter bugs, kernel-contract gaps, or intentional
   OpenClaw-specific behavior.
5. Only replace a current OpenClaw path after Parity Gate P1 is green.

The first useful parity matrix should compare:

- work id/logical key;
- stage or phase;
- status and next broker;
- receipt kind and summary;
- prompt/context/provenance refs;
- artifact refs and hashes;
- transcript refs;
- native session proof;
- human gate state;
- surface readback.

## Open Issues For Later Waves

- Whether the kernel receipt schema should import any field names from
  OpenClaw Work Ledger or define a stricter host-neutral envelope.
- Whether OpenClaw's SQLite Work Ledger becomes a host adapter storage backend
  or only a legacy compatibility source.
- How the kernel should model Task Flow once the OpenClaw backend moves beyond
  the Python harness.
- How much surface readback should be required for routine summaries versus
  approval-gated decisions.
- What exact URI scheme should represent logical host, runtime, lane, artifact,
  and surface references.
