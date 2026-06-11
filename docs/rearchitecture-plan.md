# Lane Host Rearchitecture Plan

Last updated: 2026-06-11
Status: direction approved by Suman; executing in autonomous goal mode
(see "Goal mode" section). Old repos are recovery archives only.

## Why

The kernel is right; the system around it is expensive. Today one lane costs
six to eight touch points across two repos (launchd plist + shell wrapper +
python runner + AWK adapter + manifest entry + prompts in two trees), three
schedulers coexist (launchd, OpenClaw cron, AWK pollers), state is scattered
across per-lane SQLite/JSON files, and four audit lanes exist solely to
reconcile the drift this creates. Prod hotfixes strand on the live tree
because the repo split forces hand-syncing.

This plan rebuilds the system around the kernel: one repo, one supervisor,
one ledger, two lane tiers, shared surface services, and a runtime-provider
boundary that makes the whole thing agent-agnostic.

## Hard requirements (from Suman)

1. **Agent-agnostic.** Not tied to OpenClaw, Codex, or Claude. OpenClaw is
   today's surface; later it could be something else. Proof bar: the same
   lane YAML runs on two different runtime providers with parity receipts.
2. **Per-stage surface binding.** Obsidian for deep "kneading" review
   (drafts, visual assets); Telegram for fast binary gates ("M4 dry run
   passed with score 0.85, deploy to shadow? [Approve]/[Reject]"). The stage
   config chooses the surface; the ingestion engine handles both.
3. **Config-only DAG evolution.** Workflows are DAGs of nodes, not rigid
   lists. Inserting an AI auditor (Scout (Mala) → **Audit (Bumblebee)** →
   Review (Suman) → Execute) must be a YAML edit only — no runner code, no
   new adapter registration code.
4. **North star.** "Suman should no longer manually prompt... AWK should
   select the right governed loop, gather bounded context, run or delegate
   the work, validate proof, and stop."

## Target architecture

```
┌─ lane-host (single repo, single resident service) ─────────────┐
│                                                                 │
│  lanehostd supervisor (the ONE scheduler)                       │
│   ├─ tick loop: advance all workflow instances (leases)         │
│   ├─ cron triggers: declared in lane YAML, not plists           │
│   └─ gate ingestion: Obsidian + Telegram decisions              │
│                                                                 │
│  Tier 1: Jobs       = schedule + prompt/script + receipt line   │
│  Tier 2: Workflows  = kernel (ledger, gates, budgets, receipts) │
│                                                                 │
│  Shared surface services (lanes declare, never implement)       │
│   ├─ Obsidian gate renderer/parser (one note schema)            │
│   ├─ Telegram gate (inline approve/reject, idempotent)          │
│   └─ Publish executor (receipt-bound approval hash check)       │
│                                                                 │
│  Runtime provider boundary (agent-agnostic)                     │
│   └─ invoke(persona_ref, prompt_refs, context_packet)           │
│        → structured receipt + token/budget accounting           │
│      providers: openclaw_codex (today), <second provider>,      │
│      local/deterministic. Swappable per actor per lane.         │
│                                                                 │
│  One host ledger (single SQLite: instances, receipts, jobs,     │
│  gate decisions, artifacts) — `lanehost status` sees all.       │
└─────────────────────────────────────────────────────────────────┘
```

A lane becomes one directory:

```
lanes/<name>/
  lane.yaml      # tier, schedule, DAG (tier 2), actors→provider bindings,
                 # per-stage surface + policy class, budgets, prompt refs
  plugin.py      # optional: deterministic lane logic (intake, validation)
  prompts/       # versioned, hashed, registry-bound
```

## Lane disposition census (43 active lanes)

- **Tier 2 workflows (~12, keep + port):** ivy/jonah editorial P1–P5,
  or_research weekly post producer, x_digest_post_review, radhe publish
  review (wisdom + parenting), safe_token_optimizer, supercharge ideas,
  blackboard decision routing, mala→bhiksha shadow promotion, jarvis weekly,
  (future) trade_lab diagnostics.
- **Tier 1 jobs (~20, simplify):** vault intelligence weekly/monthly,
  birdclaw digest, morning standup, market scout, mwf scouts, mala daily
  review, gym trainer, career transition nudges, family weekend options,
  where-is-home scorecards, steel-calm check-in, reflect, memory promotion.
- **Deterministic jobs (~5, keep as Tier 1 script jobs):** radhe daily
  generation ×2, trading_systems_watch, polygon cache topup, auth health.
- **Delete (compensating controls, ~4–6):** editorial_path_audit,
  work_ledger_reconciliation, cron_health_audit, blackboard_health (replaced
  by `lanehost doctor` against the single ledger), plus legacy runners already
  fenced behind env flags.

## Migration invariants (apply to every phase)

- oldmac keeps running throughout; never two owners for the same lane.
- Shadow-first: new path runs in parity alongside old before any cutover
  (existing fixtures→shadow→owned discipline applies).
- Every cutover has a one-command rollback (re-enable plist / revert branch).
- Rip-out is part of done: a phase isn't complete until the replaced thing
  is deleted or fenced, not just bypassed.

---

## Phase 0 — Charter, freeze, and census lock (size S)

**Work:** Freeze new lane adoption on the old pattern (no new
plists/wrappers). Census locked (kill list approved by Suman 2026-06-11).
Repo identity resolved: a **fresh `lane-host` repo**; the two existing
repos are frozen recovery archives — no migration obligation to old glue,
only to lane *outcomes*.

**Acceptance gate:** census signed off (done); freeze noted.
**Rollback:** none needed (docs only).

## Phase 1 — New repo bootstrap (size M)

**Work:** Create the `lane-host` repo from scratch. Vendor the kernel in
as `packages/kernel/agent_workflow_kernel/` (copy with its test suite —
it's the one asset worth carrying; everything else is rewritten, with the
old repos consulted read-only for lane semantics and prompts). Scaffold:
`packages/host/` (supervisor, surfaces, providers), `lanes/`, `jobs/`,
`prompts/` (registry seeded from the AWK registry), CI (pytest + the
import-lint enforcing kernel purity: `packages/kernel/` imports nothing
from host or any provider). Bootstrap script for a host machine.

**Rip out:** nothing yet — old repos keep running prod untouched.
**Acceptance gate:** kernel test suite green inside lane-host; import-lint
wired into CI and pre-commit; repo bootstraps on a clean checkout (dev and
oldmac) with `make setup && make check`.
**Rollback:** delete the repo; nothing depends on it yet.

## Phase 2 — lanehostd supervisor + single ledger (size L)

**Work:** Build `lanehostd`, one resident service (single launchd entry) that:
ticks all workflow instances via leases, fires cron triggers declared in
lane YAML, and ingests gate decisions. Consolidate per-lane SQLite/JSON
state under `state/awk/` into one host ledger (instances, stage runs,
receipts, jobs, gate decisions, artifacts). Add `lanehost status` / `lanehost doctor`
CLI over the ledger. Run lanehostd in shadow first: it observes and writes
parity receipts while launchd lanes stay authoritative; then cut lanes over
one at a time, deleting each plist as it moves.

**Rip out:** ~30 `ai.openclaw.*` lane plists, hourly poller wrappers,
per-lane state files (migrated).
**Acceptance gate:** for lanes still live on oldmac, N consecutive clean
shadow runs with receipt parity vs. the old path; for everything else,
fixture + synthetic-operator acceptance (see Goal mode). One scheduler owns
every lane; `lanehost status` lists all in-flight instances.
**Rollback:** re-enable the lane's plist; the supervisor ignores lanes
marked legacy.

## Phase 3 — Surface services (size M, can start during Phase 2)

**Work:** Extract three shared services with per-stage binding from YAML:

1. **Obsidian gate** — one renderer/parser for the standardized gate note
   schema (x_digest option gate is the template). Deep-review gates render
   full drafts/assets for kneading.
2. **Telegram gate** — inline approve/reject buttons for fast binary
   decisions, idempotent send, decision receipts; stage config selects it
   for low-bandwidth choices.
3. **Publish executor** — the only code path that performs external effects
   (X, Substack, Medium, YouTube); refuses any packet whose content hash
   lacks a matching approval receipt (generalizes validate_editorial_state).

**Rip out:** per-lane Blackboard render/parse scripts, per-lane Telegram
senders, per-lane publish code.
**Acceptance gate:** the same human gate runs on Obsidian and Telegram by
flipping one YAML field; publish executor blocks a deliberately tampered
packet in test and in a live drill.
**Rollback:** per-lane scripts remain until each lane flips.

## Phase 4 — Runtime provider boundary: the agnosticism milestone (size L, parallel after Phase 2)

**Work:** Define RuntimeProvider v2: `invoke(persona_ref, prompt_refs,
context_packet) → structured receipt` with capability discovery, token
accounting feeding kernel budget enforcement (max_revision_turns /
max_ping_pong_turns stay kernel-owned), and failure classes. Personas move
fully into the AWK prompt registry (identities exist already); OpenClaw
AGENTS.md files become thin shims pointing at registry refs. Implement two
providers: `openclaw_codex` (today's runtime) and one genuinely different
second provider (direct API or another agent SDK — choice deferred to the
phase, vendor-neutral interface is the point). A2A loops (ivy/jonah) must
work cross-provider: writer on one, reviewer on the other.

**Rip out:** any direct OpenClaw/Codex import outside the provider package
(enforced by the Phase 1 import lint, now extended to providers).
**Acceptance gate:** one real lane (suggest ivy_jonah_editorial in shadow)
runs end-to-end on the second provider with parity receipts; the
cross-provider A2A loop completes with budgets enforced.
**Rollback:** provider binding is per-actor per-lane YAML; flip back.

## Phase 5 — Tier 1 jobs migration (size M)

**Work:** Collapse the ~20 stateless lanes (synthesis, scouts, nudges, gym
trainer, career coach, personal cadences) into `jobs/` YAML entries run by
lanehostd: schedule + provider call or script + one receipt row + surface
delivery. No workflow instances, no gates, no ceremony. Delete their
OpenClaw cron entries and remaining plists.

**Rip out:** OpenClaw cron as a lane scheduler (cron/jobs.template.json
shrinks to OpenClaw-internal concerns or empties), remaining lane plists.
**Acceptance gate:** a week of job receipts in the ledger with delivery
parity; gym-trainer and career-coach cadences confirmed unchanged from
Suman's seat.
**Rollback:** cron template restore is one commit.

## Phase 6 — Tier 2 consolidation + DAG ergonomics (size M)

**Work:** Port remaining gated lanes to lane dirs. Build the reusable stage
library so common nodes are declarative: `a2a_review_loop`, `human_gate`,
`validate_state` (hash check), `publish_preflight`, `publish`, `intake`.
Adapter resolution by role/capability so a new actor needs zero registry
code. Fold Ivy's Work Ledger state machine into kernel instances (the
P1–P5 lifecycle becomes the workflow definition it already mirrors), with
a migration script for in-flight items.

**Rip out:** work_ledger lib as a parallel state machine; per-lane bespoke
adapters that the stage library obsoletes.
**Acceptance gate (the Bumblebee test):** insert an auditor node into the
Mala review lane by editing YAML only — no Python, no registration code —
and the A2A handoff routes to the auditor before Suman's board.
**Rollback:** Work Ledger fold-in is the riskiest step; keep dual-write
during migration with a reconciliation check until N clean days.

## Phase 7 — Decommission compensating controls (size S)

**Work:** Delete editorial_path_audit, work_ledger_reconciliation,
cron_health_audit, blackboard_health as scheduled lanes; `lanehost doctor`
(single-ledger invariant checks + failure-only Telegram alert) replaces
them. Remove legacy fenced runners (`ALLOW_LEGACY_*` paths). Archive dead
docs per the existing worker-records policy.

**Acceptance gate:** `lanehost doctor` catches a seeded fault (stale lease,
orphan receipt, gate timeout) end-to-end; zero scheduled audit lanes
remain.

## Phase 8 — trade_lab as the first born-native lane (size L)

**Work:** Build the trade_lab diagnostic lane on the finished architecture:
deterministic collectors (trading_systems_watch outputs, app logs, ledger
exports) → diagnose (agent, read-only) → propose (agent: strategy memo or
draft code change as artifact) → human gate (Telegram fast-gate for
shadow-tier, Obsidian for deep review) → apply (publish-executor-equivalent
guarded by `financial_effect`/`production_effect` policy classes; shadow
first, never autonomous live trading).

**Acceptance gate:** one real discovered issue travels
observe→diagnose→propose→gate→apply-in-shadow with full receipts; nothing
reaches live effect without an approval-bound hash.
**Why last:** it's the payoff lane and the cleanest proof the architecture
serves a brand-new domain with zero new infrastructure.

---

## Sequencing summary

```
P0 charter → P1 one repo → P2 lanehostd + one ledger ─┬─ P3 surfaces ─┐
                                                  └─ P4 providers ┴→ P5 jobs → P6 tier-2 + ledger fold → P7 delete audits → P8 trade_lab
```

P3 and P4 parallelize after P2. P5 needs P2+P4 (jobs call providers).
P6 needs P3+P4. Biggest-risk steps: P2 cutover (mitigated by shadow parity
and per-lane rollback) and P6 Work Ledger fold (mitigated by dual-write).

## Decisions (resolved 2026-06-11)

1. **Kill list:** approved by Suman. Old system carries no weight ("nothing
   is working anyway"); old repos are recovery archives only.
2. **Repo identity:** fresh `lane-host` repo (private, GitHub), kernel
   vendored in with its tests. Decided by Suman.
3. **Runtime providers (updated 2026-06-11):** three from day one.
   `claude` is the **primary workhorse** — no token ceiling (Suman), CLI
   installed on both machines, used for the wide e2e matrix. `codex` has
   soft token limits — used for cross-provider proofs throughout, and the
   codex-only full run is the **last hardening step**. `deterministic`
   (scripted mock) for fixtures and breadth. OpenClaw may additionally get
   a `claude`-backed agent for surface-side integration.
   Agnosticism proof = the same lane YAML green on claude AND codex with
   parity receipts. **Deliverable — provider comparison report:** receipts
   already carry token accounting; every cross-provider run feeds a
   per-lane comparison (output quality verdicts + token spend per stage),
   so the program also answers "which LLM does the better job for fewer
   tokens" per lane.
4. **Supervisor shape (delegated to Claude, decided):** a resident daemon
   managed by launchd `KeepAlive`, whose main loop is a strictly
   *idempotent tick* also runnable as `lanehostd --once`. Rationale:
   launchd owns crash/reboot recovery (no supervision code to maintain);
   the idempotent tick keeps every behavior reproducible in tests and
   usable as a manual recovery tool; scaling is "lower the tick interval",
   never an architecture change. The CLI is `lanehost` (not `awk` — that
   would shadow unix awk on every machine it touches).

## Goal mode: autonomous end-to-end development and testing

Suman's directive (2026-06-11): Claude has deploy permission (ssh oldmac),
acts as the human operator during testing, and does not bring Suman in
until Claude itself is satisfied. Anywhere there is an external publish,
build a **safe internal publish** instead; apart from that, end to end is
the goal.

**Sandbox-first effects.** The publish executor ships with an `internal`
backend as the default and only enabled backend: it writes the final
packet (content + approval-bound hash + receipt chain) to an internal
outbox (`state/outbox/` + an Obsidian "Published (Internal)" folder on
oldmac). Real X/Substack/Medium/YouTube backends are out of scope for this
goal and stay behind `production_effect` policy + a Suman-only gate.
Telegram likewise gets a sandbox surface backend (file-based message log
with the same schema as real sends) so gates and nudges are fully testable
without messaging anyone.

**Synthetic operator.** A first-class test harness (`tests/operator/`)
that plays Suman: it reads rendered gate notes (Obsidian schema) and
sandbox Telegram gate messages, then writes decisions — approve, reject,
revise-with-feedback, park, and timeout (no response) — both as scripted
sequences (deterministic fixtures) and as an LLM-judged reviewer for
realistic e2e runs. Every Tier 2 lane must pass: happy path, each
non-happy gate outcome, gate timeout, and tampered-packet refusal.

**Test matrix.**
- Dev machine: deterministic + claude providers; full lane suite against
  sandbox surfaces; fast iteration.
- oldmac: claude (primary) + codex providers; lanehostd under launchd;
  real Obsidian vault surfaces (gate notes written to a `Lane Host
  (Shadow)` folder, never the live Blackboard); shadow-parity for lanes
  still running on the old path.
- Cross-provider A2A: writer on claude, reviewer on codex (and reversed).
- Last hardening step: full lane suite with codex as the only provider.

**Definition of satisfied (when Suman gets brought in).** All phase
acceptance gates P1–P7 pass self-verified, including: the Bumblebee test,
the surface-flip test, the tampered-packet drill, cross-provider parity
receipts, a week-equivalent of supervisor soak on oldmac (compressed via
tick-interval acceleration where wall-clock isn't the point), and `lanehost
doctor` catching seeded faults. Suman is then brought in for: a live
walkthrough, the decision to enable any real external publish backend, and
prod cutover of live lanes (cutover of currently-live oldmac lanes is OUT
of this goal's scope — the new system runs in shadow beside them).

**Resolved with Suman (2026-06-11):** no token ceiling for claude (primary
provider, both machines); codex limits are soft, codex-only full run is
the last hardening step; provider quality/token comparison is an explicit
deliverable; prod-lane cutover confirmed out of goal scope.

**One setup item (Suman, on oldmac):** Claude CLI v2.1.173 is installed
and interactively logged in, but headless calls (ssh — and launchd, which
is how lanehostd runs) cannot reach the macOS Keychain credentials
(verified 2026-06-11: `claude -p` over ssh returns "Not logged in").
Fix: run `claude setup-token` once in an interactive terminal on oldmac;
the long-lived token is then exported as `CLAUDE_CODE_OAUTH_TOKEN` in
non-interactive contexts (stored mode-600 under `~/.lane-host/secrets/`,
loaded by lanehostd's launchd environment and test scripts; never
committed). Codex auth on oldmac is file-based (`~/.codex/auth.json`) and
already works headless.
