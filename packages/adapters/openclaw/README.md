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
