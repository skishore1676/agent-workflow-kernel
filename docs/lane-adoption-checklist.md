# AWK Lane Adoption Checklist

Design principle: **generic rail, domain-specific cargo**.

Use this checklist when adopting any new lane into AWK. Keep domain behavior in
lane adapters, prompts, scripts, and artifacts; keep AWK responsible for durable
workflow control.

## Checklist

| Rail | Adoption question | Evidence |
| --- | --- | --- |
| Workflow graph | Are stages, transitions, actors, prompt refs, policies, terminal states, and revision paths explicit? | Workflow YAML/definition validates and names every stop/retry/revision path. |
| Prompt registry | Are actor/profile/stage prompts versioned and resolved by hash? | Receipts include prompt refs/hash or a documented no-prompt reason. |
| Work Ledger | Does each work item have durable instance, current stage, prompt hash, receipts, and terminal/waiting state? | Ledger readback proves current state and prior transitions. |
| Runner | Can the runner pick up, resume, retry, and stop at gates without manual babysitting? | Local/host command shows `done`, `waiting_on_human`, or named `blocked` state. |
| Surface adapters | Do human gates appear on the intended surface and ingest exact decisions safely? | Obsidian/Telegram/Sheets/local readback shows link, choices, and decision receipt. |
| Policy layer | Are publish, deploy, trade, auth, external-send, and destructive actions blocked unless explicitly approved? | Policy receipt or test proves risky actions fail closed. |

## Surface Posture

- Obsidian is the durable executive review room: show the artifact, decision
  choices, links, owner, current state, and concise receipts.
- Telegram is the sparse interrupt channel: use it for failures, action-needed
  nudges, publish/deploy handoffs, or short summaries that link back to the
  durable surface.
- Do not dump machine logs, repeated receipts, or every stage update into either
  surface; store that detail in the ledger/artifacts and link it when useful.
- Every human gate should have one canonical decision surface. If Telegram and
  Obsidian disagree, stop and write a blocked receipt instead of guessing.

## Cutover Gate

A lane is not cut over until:

- fixture or local proof passes;
- live-readonly or live dry-run readback passes on the real host/surface;
- legacy path is disabled, wrapped, or intentionally retained as compatibility;
- launchd/cron/runner/ingester entrypoints point to AWK;
- source branches are merged/pushed and local/live machines are clean or explained;
- first real run produces reviewable artifacts and durable AWK receipts.

## Readiness Labels

- `concept`: design only.
- `fixture_shadow`: deterministic local proof.
- `live_readonly`: current runtime/surface readback, no mutation.
- `owned_execution`: AWK may drive the lane but stops at policy gates.
- `cutover`: production entrypoints use AWK and legacy behavior is handled.
