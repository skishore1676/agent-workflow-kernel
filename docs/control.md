# Agent Workflow Kernel Control

Last updated: 2026-05-31

## Supervisor Contract

This document is the project brain. The supervisor thread owns:

- project direction and sequencing;
- worker-thread goal packets;
- synthesis of competing worker outputs;
- integration decisions;
- acceptance gates;
- keeping OpenClaw-specific behavior out of the portable kernel.

Worker threads should not ask for routine next steps. Each worker gets a bounded
goal, acceptance criteria, and a target artifact. If blocked, it should leave a
clear blocked note with evidence and the smallest unblock request.

## Vision

Build a portable agent-workflow kernel that can run graph-defined workflows
across LLM agents, scripts, humans, reviewer/doer loops, surfaces, prompts,
receipts, and host runtimes.

OpenClaw is the reference host and first proving ground. The kernel must be
designed so it can later run outside OpenClaw.

## Product Boundary

The kernel owns:

- workflow definitions and versions;
- workflow instances and stage runs;
- prompt references and rendered context packet hashes;
- receipts, artifacts, transitions, and recovery state;
- runner claim/lease/retry mechanics;
- policy gates;
- surface and runtime adapter contracts.

Host adapters own:

- OpenClaw agent/session execution;
- oldmac runtime paths;
- Obsidian/Northstar and Telegram details;
- launchd/cron;
- lane-specific domain engines such as OR Research project ledgers;
- local machine verification.

## Architecture Slogan

Generic rail, domain-specific cargo.

## Current Decisions

| ID | Decision | Status |
| --- | --- | --- |
| D001 | Create a standalone repo at `/Users/suman/code/agent-workflow-kernel`. | accepted |
| D002 | Build for the full portable kernel vision, but validate first through narrow low-risk OpenClaw slices. | accepted |
| D003 | Incubate the kernel with OpenClaw as reference host, then extract once adapter boundaries are boring. | accepted |
| D004 | Use Bumblebee/quality-review as first validation, not as the product design center. | accepted |
| D005 | Use Ivy/Jonah as second validation for richer A2A reviewer/doer loops. | accepted |
| D006 | Treat the kernel as a durable workflow state machine, not as an agent chat harness. | accepted |
| D007 | Use YAML-authored workflow definitions that compile into canonical JSON for hashing, validation, and fixtures. | accepted |
| D008 | Use the Codex AWK project for Wave 2 threads; leave completed Wave 1 projectless threads as historical worker records. | accepted |
| D009 | Use stdlib dataclasses/enums for the first implementation, with YAML/pytest installed through `.venv` for integration verification. | accepted |
| D010 | Keep a small stdlib YAML fallback and normalize PyYAML's YAML 1.1 `on:` boolean behavior in the loader. | accepted |
| D011 | Launch Wave 3 as four independent AWK project threads: CLI/local execution, read-only OpenClaw adapter, parity reporting, and developer setup hardening. | accepted |
| D012 | Keep developer checks split between bare `python3` unittest resilience and `.venv` dependency verification through `make setup` and `make check`. | accepted |
| D013 | Treat Wave 4 as adoption pressure, not another abstract parity pass: Ivy/Jonah and Jarvis weekly update are the first OpenClaw lanes. | accepted |

## Workstreams

| Workstream | Purpose | First Artifact |
| --- | --- | --- |
| Domain model | Define workflow graph, stages, transitions, instances, receipts, artifacts. | `docs/synthesis/domain-model.md` |
| Workflow DSL | Define config shape without becoming a programming language. | `docs/synthesis/workflow-dsl.md` |
| Prompt registry | Define prompt versioning, rendering, hashes, context packets. | `docs/synthesis/prompt-registry.md` |
| Runner/recovery | Define execution loop, leases, retries, stale child sessions, validation. | `docs/synthesis/runner-recovery.md` |
| A2A contract | Define generic reviewer/doer stage, proof, verdicts, budgets. | `docs/synthesis/a2a-stage.md` |
| Adapter interfaces | Define runtime, surface, host, and lane adapter contracts. | `docs/synthesis/adapter-interfaces.md` |
| Policy gates | Define risk classes and approval boundaries. | `docs/synthesis/policy-gates.md` |
| OpenClaw integration | Define host adapter, compatibility wrappers, parity strategy. | `docs/synthesis/openclaw-adapter.md` |

## Thread Registry

| Thread | Workstream | Status | Notes |
| --- | --- | --- | --- |
| `019e7fd1-0910-7682-b738-f6ce902d8b93` | Domain model + workflow DSL | completed | Commit `ec4f599`; merged into supervisor branch. |
| `019e7fd1-0a3b-7f12-be15-1bd6355e52fa` | Prompt registry + context packets | completed | Commit `a0b98c0`; merged into supervisor branch. |
| `019e7fd1-0c21-7e01-9299-952fe11cd4c1` | Runner/recovery + storage | completed | Commit `ad9733c`; merged into supervisor branch. |
| `019e7fd1-0e37-73b0-a8a0-37d418c24c06` | Adapter contracts + OpenClaw host boundary | completed | Commit `b40eaf3`; merged into supervisor branch. |
| `019e7fd1-1008-7e62-9746-066d3258a6ac` | A2A stage + policy gates | completed | Commit `ba38ad6`; merged into supervisor branch. |
| `019e7fd1-12df-7ab3-806d-90ce576e0f68` | Example workflow validation matrix | completed | Commit `3b5b05d`; merged into supervisor branch. |

## Wave 1 Result

Wave 1 is merged into `codex/bootstrap-agent-workflow-kernel`. The combined
view is recorded in `docs/synthesis/wave-1-combined-view.md`.

Design-level Architecture Gate A1 passes. The design can express:

- Bumblebee quality review;
- Ivy/Jonah editorial A2A loop;
- trading research human gate with no live execution;
- Radhe-style generation/review pipeline;
- deterministic system action with human final gate.

The agreed architecture center is:

- durable workflow state machine;
- YAML-authored workflow graph compiled to canonical JSON;
- SQLite ledger for instances, stage runs, leases, receipts, artifacts, and
  recovery;
- prompt registry and context packet provenance;
- bounded `a2a_review_loop` as one stage type, not the whole system;
- layered policy gates with hard human approval boundaries;
- runtime, surface, host, and lane adapters keeping OpenClaw-specific details
  out of the portable kernel.

## Wave 2 Registry

Wave 2 uses Codex-managed worktrees from the AWK project, starting from
`codex/bootstrap-agent-workflow-kernel` after the Wave 1 synthesis and package
scaffold commits.

| Thread | Workstream | Status | Worktree |
| --- | --- | --- | --- |
| `019e7fdd-92e2-7592-bc1e-9bc1602c2b7b` | Core schema + DSL | completed | Commit `ce892c2`; merged into supervisor branch. |
| `019e7fde-8324-7202-bcb1-8319e843df00` | SQLite ledger + runner | completed | Commit `3aa06f1`; merged into supervisor branch. |
| `019e7fde-8232-7110-9b68-eafcb3a02111` | Prompt context + receipts | completed | Commit `1f1ab3d`; merged into supervisor branch. |
| `019e7fde-8322-7931-a960-039c5a012362` | Policy engine + human gates | completed | Commit `bbf68f3`; merged into supervisor branch. |
| `019e7fde-8334-7f42-86e0-d3db017436cd` | Adapter SPI + local fakes | completed | Commit `03158dd`; merged into supervisor branch. |
| `019e7fde-85f4-7ba2-bc31-0cde173f640c` | Example workflows + fixtures | completed | Commit `13bec50`; merged into supervisor branch. |

## Wave 2 Result

Wave 2 is merged into `codex/bootstrap-agent-workflow-kernel`.

The implementation now includes:

- stdlib dataclass contract models and package scaffold;
- workflow DSL loader, validator, and canonical JSON compiler;
- SQLite ledger repository and adapter-neutral runner skeleton;
- prompt registry, context packet renderer, and receipt provenance helpers;
- policy engine with hard human gates and exact action fingerprints;
- runtime, surface, host, and lane adapter SPI with local fake adapters;
- five example workflow fixtures covering Bumblebee, Ivy/Jonah, trading
  research gate, Radhe review, and deterministic system action with human gate.

Verification:

- `python3 -m unittest discover -s tests` passes 47 tests.
- `.venv/bin/python -m unittest discover -s tests` passes 47 tests.
- `.venv/bin/python -m pytest` passes 47 tests.
- `.venv` contains the declared runtime/dev dependencies, including `PyYAML`
  and `pytest`, and remains ignored by git.

Implementation Gate I1 passes at skeleton level. Parity Gate P1 remains
deferred until the OpenClaw adapter can dual-run or fixture-run current
OpenClaw behavior.

## Wave 3 Registry

Wave 3 starts from commit `be68730` on
`codex/bootstrap-agent-workflow-kernel`. Its purpose is to move from skeleton
to an operator-usable local harness while keeping OpenClaw integration
read-only and parity-first.

| Thread | Workstream | Status | Branch | Worktree |
| --- | --- | --- | --- | --- |
| `019e7fec-e01a-7343-bb6b-1ba80ba280f0` | CLI + local execution | completed | `codex/wave3-cli-local-execution` | Commit `99eb25c`; merged into supervisor branch. |
| `019e7fec-e01a-7343-bb6b-1b9cc1e16a3f` | OpenClaw read-only adapter | completed | `codex/wave3-openclaw-readonly-adapter` | Commit `953514e`; merged into supervisor branch. |
| `019e7fec-e0f8-7421-a600-bd6be59e274a` | Parity reporting fixtures | completed | `codex/wave3-parity-reporting` | Commit `e7390b6`; merged into supervisor branch. |
| `019e7fec-e0f4-77f3-896a-d21155214504` | Developer setup hardening | completed | `codex/wave3-developer-setup` | Commit `1abc0ac`; merged into supervisor branch. |

Completed so far:

- CLI/local execution adds `validate`, `compile`, and `run-local` commands.
  Local execution writes SQLite instances, stage runs, receipts, events, and
  adapter invocations, then stops safely at human gates without calling external
  OpenClaw or operator surfaces.
- Parity reporting compares expected host receipt fields against actual kernel
  receipt fields with equivalent, different, missing, extra, and ignored field
  classes. It includes deterministic fixture reports for Bumblebee quality
  review and human-gate surface readback shapes.
- OpenClaw read-only adapter boundary maps supplied fixture data into kernel
  adapter invocations, artifact refs, receipts, and adapter results while
  blocking mutation verbs. It does not import OpenClaw into the kernel and does
  not call live OpenClaw runtime surfaces.
- Developer setup hardening provides `make setup` for creating `.venv` and
  installing `.[dev]`, plus `make check` for running bare `python3` unittest and
  venv-backed `pytest` when available. This preserves fresh-machine resilience
  when system `python3` lacks `PyYAML` while still verifying the declared
  package environment.

Coordination notes:

- Each thread has an explicit goal packet in `docs/worker-goals/wave-3/`.
- Each thread is responsible for creating `.venv` if needed and keeping it
  untracked.
- No Wave 3 thread may mutate OpenClaw, oldmac, Telegram, Obsidian, launchd,
  broker, auth, or public-send surfaces.
- Read-only OpenClaw mapping and fixture-based parity must land before any
  runtime replacement plan.

## Wave 3 Result

Wave 3 is merged into `codex/bootstrap-agent-workflow-kernel`.

The implementation now adds:

- CLI commands for workflow validation, canonical compilation, and local fake
  adapter execution;
- local execution that writes SQLite instances, stage runs, receipts, events,
  and adapter invocations, then stops at human gates;
- read-only OpenClaw adapter boundary outside the kernel package;
- deterministic parity report models and fixtures;
- repeatable developer setup and check scripts.

Parity Gate P1 is partially satisfied at fixture level: the kernel can now
compare host-shaped receipts to kernel-shaped receipts deterministically. P1
does not pass for live OpenClaw replacement until a current OpenClaw path is
dual-run or fixture-read from live artifacts and documented as equivalent or
intentionally different.

## Wave 4 Launch Plan

Wave 4 should be bold enough to test actual OpenClaw adoption while keeping
irreversible external effects behind explicit gates.

Target lanes:

- Ivy/Jonah editorial: rich lane that stresses A2A review, Work Ledger
  handoffs, P-stage gates, publish packets, and public-publish approval.
- Suman/Jarvis weekly update: lower-risk operator lane that stresses
  Blackboard/weekly readback and human follow-up gates.

Worker packets:

| Workstream | Goal Packet | Target Repo |
| --- | --- | --- |
| Ivy lane adoption | `docs/worker-goals/wave-4/ivy-lane-adoption.md` | AWK |
| Jarvis weekly update adoption | `docs/worker-goals/wave-4/jarvis-weekly-update-adoption.md` | AWK |
| OpenClaw lane fixture exporter | `docs/worker-goals/wave-4/openclaw-lane-fixture-exporter.md` | OpenClaw |
| OpenClaw shadow runner | `docs/worker-goals/wave-4/openclaw-shadow-runner.md` | AWK |

Wave 4 registry:

| Thread | Workstream | Status | Branch | Worktree |
| --- | --- | --- | --- | --- |
| `019e8003-b6d8-7b52-ba41-95da4211811f` | Ivy lane adoption | completed | `codex/wave4-ivy-lane-adoption` | Commit `7369e8b`; merged into supervisor branch. |
| `019e8003-b6d8-7b52-ba41-95e86c4fd510` | Jarvis weekly update adoption | running | `codex/wave4-jarvis-weekly-adoption` | `/Users/suman/.codex/worktrees/2850/agent-workflow-kernel` |
| `019e8003-b794-7cc2-be05-696b30f84e0b` | OpenClaw lane fixture exporter | running | `codex/wave4-openclaw-lane-fixture-exporter` | `/Users/suman/.codex/worktrees/8f27/openclaw-core` |
| `019e8003-b70b-7b62-8164-1bef63dcdc11` | OpenClaw shadow runner | completed | `codex/wave4-openclaw-shadow-runner` | Commit `03e4da2`; merged into supervisor branch. |

Boldness boundary:

- Read from current OpenClaw source and runtime-shaped artifacts.
- Generate fixtures, reports, and shadow receipts aggressively.
- Allow local temp files and committed test fixtures.
- Do not publish publicly, send Telegram, mutate Obsidian/Northstar, change
  cron, touch credentials, trade, deploy, or write oldmac runtime state until a
  human gate explicitly authorizes that step.

## Acceptance Gates

### Architecture Gate A1

The design is acceptable only if it can express all of these without custom
kernel code:

- Bumblebee quality review;
- Ivy/Jonah editorial A2A loop;
- a trading research human gate with no live execution;
- a Radhe-style generation/review pipeline;
- a deterministic system action with a human gate before external effects.

### Implementation Gate I1

The first skeleton is acceptable only if:

- kernel code has no OpenClaw path assumptions;
- OpenClaw integration lives behind adapter contracts;
- receipts include prompt/context/version/provenance fields;
- runner state is recoverable after interruption;
- high-risk actions are blocked without explicit human approval.

### Parity Gate P1

Before replacing any OpenClaw path, the harness must dual-run or fixture-run
against current behavior and produce equivalent receipts or a documented delta.

## Open Questions

- How strict should output schemas be for creative work?
- How much transcript retention is required?
- What is the canonical human decision source when surfaces disagree?
- When should OpenClaw Work Ledger compatibility become a wrapper versus a
  migration target?
- What is the first live OpenClaw parity source: Bumblebee review, Work Ledger
  claim/receipt, or human-gate surface readback?

## Next Supervisor Actions

1. Launch Wave 4 adoption threads from the AWK and OpenClaw projects.
2. Merge the OpenClaw exporter and AWK shadow runner first if they complete
   cleanly, because they create the bridge for live-shaped fixture proof.
3. Then merge Ivy and weekly lane adoption slices and run the shadow runner
   against both fixture families.
4. Decide the first lane where AWK can move from shadow to owning a low-risk
   step.
