# OpenClaw Adapter Package

Reserved for the reference OpenClaw host adapter.

This package may know about OpenClaw, oldmac, Work Ledger compatibility paths,
Blackboard/Northstar, and OpenClaw agent/session execution.

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

- `workspace-main/scripts/ingest_agent_reviews.py`
- `workspace-main/scripts/agent_review_runner.py`
- `scripts/run_blackboard_decision_ingester.sh`

The adapter deliberately reuses those scripts instead of duplicating lane rules
inside AWK. Dry inspection can refresh Blackboard, run a non-applying decision
ingest, and read the runner plan. Mutating ingest requires `allow_apply=True`.
The full direct Jarvis/runner loop requires `allow_agent_dispatch=True`.
