# Decisions

## D001 - Standalone Repository

Accepted 2026-05-31.

Create `/Users/suman/code/agent-workflow-kernel` as an independent repository.
OpenClaw remains the first reference host, not the kernel home.

## D002 - Vision First, Narrow Validation

Accepted 2026-05-31.

Build the architecture for the full portable workflow kernel. Use narrow,
low-risk OpenClaw workflows only as validation slices.

## D003 - Hybrid Incubation

Accepted 2026-05-31.

Incubate with OpenClaw because it has real Work Ledger, Blackboard, and A2A
lessons. Extract only after adapter boundaries and parity are boring.

## D004 - Bumblebee Is A Test Lane, Not The Product

Accepted 2026-05-31.

Bumblebee/quality-review is the first validation lane because it is generic and
low-risk. It must not shrink the product vision.

## D005 - AWK Is An Active OpenClaw Rail

Accepted 2026-06-02.

AWK is no longer only a local harness or proof track. Selected OpenClaw lanes
now use AWK-owned production entrypoints, and future decision/workflow lane
adoption should start from AWK unless the lane explicitly belongs elsewhere.
Legacy paths should become guarded compatibility shims or be archived after the
AWK entrypoint is live and verified.
