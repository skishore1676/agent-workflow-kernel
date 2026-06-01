# Automated Suman Reviewer

`AutomatedSumanReviewer` is a deterministic, test-only reviewer for local
human-gate loops. It is not a real Suman approval source.

Allowed scope:

- local repository tests, fixtures, and shadow review packets;
- local Markdown human-review cards with `test_only=true` and `non_live=true`;
- review decisions that move the workflow through already declared human-gate
  outcomes.

Safety policy:

- it never approves public publish, deploy, live trade, auth, money, external
  send, Telegram send, Obsidian/Northstar write, oldmac mutation, or destructive
  actions;
- it parks or blocks packets that are missing explicit test/non-live scope;
- it requests revision when required artifacts are missing or adoption blockers
  remain;
- it can apply configured override decisions only after the same safety checks
  pass.

The owned runner can call the reviewer after publishing and reading back a local
human-gate surface. The reviewer checks exactly one local Markdown checkbox,
writes a structured `automated_suman_reviewer_decision.v1` receipt, and the
existing surface-ingest path converts the checked card into a normal human-gate
decision receipt. Those receipts identify the actor as
`Suman(test automated reviewer)` so downstream tests cannot confuse it with a
real human approval.
