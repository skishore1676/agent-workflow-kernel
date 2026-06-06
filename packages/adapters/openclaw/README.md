# OpenClaw Adapter Package

Reference OpenClaw host adapter package.

This package may know about OpenClaw, oldmac, Work Ledger compatibility paths,
Blackboard/Northstar, and OpenClaw agent/session execution.

## Installation Boundary

The repository root editable install includes both packages:

```bash
python3 -m pip install -e .
```

After that, portable kernel imports should come from `agent_workflow_kernel`,
while OpenClaw-specific imports should come from this adapter package:

```python
from agent_workflow_kernel import WorkflowKernel
from agent_workflow_kernel_openclaw import OpenClawBlackboardReviewAdapter
from agent_workflow_kernel_openclaw import OpenClawTelegramSurfaceAdapter
```

Scripts in this repository first try those normal installed imports and only
fall back to source-checkout paths when they are run without an editable install.

## Blackboard Review Pointer

`OpenClawBlackboardReviewAdapter` is the write-capable boundary for live
OpenClaw human gates that need to appear on Northstar's generated
`01 Blackboard.md`.

The adapter:

- validates the review note stays inside the configured vault
- writes a `workspace-main/state/artifact_outbox/records/*.json` pointer
- runs `workspace-main/scripts/update_review_inbox.py --check-sync --validate`
- reads `01 Blackboard.md` back and blocks if the expected item is missing

Portable AWK workflow definitions should emit generic human-gate review notes.
This adapter is where OpenClaw-specific dashboard plumbing belongs.

## Blackboard Decision Loop

`OpenClawBlackboardDecisionLoopAdapter` wraps the existing OpenClaw scripts that
consume checked review-note decisions and route follow-up work:

- `workspace-main/scripts/surfaces/ingest_agent_reviews.py`
- `workspace-main/scripts/programs/agent_review_runner.py`
- `scripts/lanes/run_blackboard_decision_loop_direct.sh`

The adapter deliberately reuses those scripts instead of duplicating lane rules
inside AWK. Dry inspection can refresh Blackboard, run a non-applying decision
ingest, and read the runner plan. Mutating ingest requires `allow_apply=True`.
The full direct Jarvis/runner loop requires `allow_agent_dispatch=True`.

## OpenClaw Telegram Surface

`OpenClawTelegramSurfaceAdapter` lives in this adapter package, not in the
portable kernel. It wraps `openclaw message send --channel telegram ... --json`
behind explicit live-send configuration, packet-level operator-surface
authorization, idempotency receipts, and local receipt readback.
