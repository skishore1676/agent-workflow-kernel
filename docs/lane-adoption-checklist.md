# AWK Lane Adoption Checklist

Design principle: **generic rail, domain-specific cargo**.

Use this checklist when adopting any new lane into AWK. Keep domain behavior in
lane adapters, prompts, scripts, and artifacts; keep AWK responsible for durable
workflow control.

AWK is the active adoption rail for selected OpenClaw lanes. This checklist is
the production cutover discipline: use it to add, harden, or retire lanes
without splitting the operator surface.

## Checklist

| Rail | Adoption question | Evidence |
| --- | --- | --- |
| Workflow graph | Are stages, transitions, actors, prompt refs, policies, terminal states, and revision paths explicit? | Workflow YAML/definition validates and names every stop/retry/revision path. |
| Prompt registry | Are actor/profile/stage prompts versioned and resolved by hash? | Receipts include prompt refs/hash or a documented no-prompt reason. |
| Work Ledger | Does each work item have durable instance, current stage, prompt hash, receipts, and terminal/waiting state? | Ledger readback proves current state and prior transitions. |
| Runner | Can the runner pick up, resume, retry, and stop at gates without manual babysitting? | Local/host command shows `done`, `waiting_on_human`, or named `blocked` state. |
| Surface adapters | Do human gates appear on the intended surface and ingest exact decisions safely? | Obsidian/Telegram/Sheets/local readback shows link, choices, and decision receipt. |
| Policy layer | Are publish, deploy, trade, auth, external-send, and destructive actions blocked unless explicitly approved? | Policy receipt or test proves risky actions fail closed. |
| Scheduler entrypoint | If the lane is recurring or event-triggered, does the installed launchd/cron/runner/ingester path invoke AWK? | Live host readback shows the installed label, arguments, schedule, and working directory. |
| Legacy retirement | Are old routes disabled, guarded, archived, or intentionally retained as named compatibility adapters? | Old live labels are absent or old scripts fail closed unless an explicit emergency override is set. |
| Operator packet quality | Is the review card readable as an executive decision packet, not a machine log? | Card has a quick read, artifact links, evidence summary, exact choices, and explains what each click will do. |

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
- recurring/event-driven lanes have live scheduler readback captured;
- active operator surfaces are cleaned of obsolete proof cards from earlier runs;
- the live card is good enough for Suman to decide without opening raw logs.

Do not count a lane as `cutover` just because AWK has a shadow adapter,
fixture, prompt profile, or Obsidian card. `cutover` means the production
entrypoint for that lane now rides AWK, or the lane is explicitly documented as
non-AWK production with AWK used only for observation.

After cutover, keep improving the lane in AWK. Do not keep a parallel legacy
board or scheduler unless it is a named compatibility/emergency fallback.

## Status Snapshot

When reporting adoption status, use this shape so shadow work is not confused
with production cutover:

| Lane | Readiness | Production entrypoint | Legacy status | Latest live proof | Known gap |
| --- | --- | --- | --- | --- | --- |
| Example lane | `cutover` / `owned_execution` / `live_readonly` | launchd/cron/runner label and script | disabled / guarded / compatibility / retained | receipt, artifact, or readback path | one sentence |

## Readiness Labels

- `concept`: design only.
- `fixture_shadow`: deterministic local proof.
- `live_readonly`: current runtime/surface readback, no mutation.
- `owned_execution`: AWK may drive the lane but stops at policy gates.
- `cutover`: production entrypoints use AWK and legacy behavior is handled.
