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
| TBD | Domain model + workflow DSL | planned | Wave 1 |
| TBD | Prompt registry + context packets | planned | Wave 1 |
| TBD | Runner/recovery + storage | planned | Wave 1 |
| TBD | Adapter contracts + OpenClaw host boundary | planned | Wave 1 |
| TBD | A2A stage + policy gates | planned | Wave 1 |
| TBD | Example workflow validation matrix | planned | Wave 1 |

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

- Should workflow definitions be YAML, Python declarations, or both?
- What is the minimum useful schema for `WorkflowDef` and `StageRun`?
- How strict should output schemas be for creative work?
- How much transcript retention is required?
- Should policy be global-first, workflow-first, or layered?
- What is the canonical human decision source when surfaces disagree?

## Next Supervisor Actions

1. Commit the bootstrap skeleton.
2. Create first-wave worktrees/branches.
3. Launch worker threads with goal packets.
4. Read back worker artifacts.
5. Update this control document with the synthesis and next-wave plan.

