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

Current OpenClaw Blackboard adoption snapshot, updated 2026-06-05:

| Lane | Readiness | Production entrypoint | Legacy status | Latest live proof | Known gap |
| --- | --- | --- | --- | --- | --- |
| Blackboard decision ingester | `owned_execution` | `scripts/lanes/run_awk_blackboard_decision_ingester.sh` via OpenClaw/AWK runner; graph `openclaw_blackboard_bus` | direct legacy script retained as compatibility cargo | oldmac dry-run returned `noop` with valid ingestion scan; graph has prompt/no-prompt provenance | needs first real post-hardening decision receipt after this lifecycle pass |
| Radhe publish review | `owned_execution` | Obsidian owner brief -> `radhe_publish_review/approved/*.json` -> deterministic Radhe control; graph `radhe_review_pipeline` | Telegram approval parser fails closed; Telegram is notify/conversation only | oldmac dry-run planned `radhe_control.py approve --source obsidian --publish --dry-run`; graph names publish/skip/feedback outcomes | needs live non-publish proof for `skip_radhe_run` and `record_radhe_feedback` after next reviewed video |
| Supercharge ideas | `owned_execution` | Obsidian Supercharge note -> Jarvis route -> Codex implementation -> Obsidian Runner Closeout; graph `openclaw_supercharge_idea_lifecycle` | no direct terminal chat-only requirement; closeout can be ingested from Obsidian | local tests cover `awaiting_close` closeout block and ingestion; graph makes closeout the terminal human gate | needs first live `awaiting_close` closeout readback after a real Codex implementation |
| Safe Token Optimizer | `owned_execution` | option card -> final prompt card -> `handoff_to_jarvis` / `handled_manually` / `park`; graph `safe_token_optimizer_review` | old `implemented` / `parked` states suppressed as historical terminals | local/oldmac focused suites pass prompt-card and handoff tests; graph has two human gates and terminal choices | needs cleanup of old resurfaced cards only if new cards still repeat after terminal decision |

Checklist audit verdict:

- Completed: single durable Obsidian decision source for Radhe, Supercharge, and token optimizer gates; adopted-lane workflow graphs with prompt refs or documented no-prompt reasons; prompt registry-backed Blackboard/Radhe/Supercharge/Safe Token prompt cards; deterministic Radhe publish/skip/feedback commands; `awaiting_close` visibility; AWK/OpenClaw runner tests on local and oldmac.
- Still to observe: first real live lifecycle receipts after these changes for Radhe non-publish decisions and Supercharge closeout terminalization.
- Not done by design: Telegram inline approval buttons. If added later, they should update or link to Obsidian rather than becoming a second approval ledger.

## Readiness Labels

- `concept`: design only.
- `fixture_shadow`: deterministic local proof.
- `live_readonly`: current runtime/surface readback, no mutation.
- `owned_execution`: AWK may drive the lane but stops at policy gates.
- `cutover`: production entrypoints use AWK and legacy behavior is handled.
