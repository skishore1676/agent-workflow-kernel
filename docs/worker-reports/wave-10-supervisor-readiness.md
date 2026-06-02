# Wave 10 Supervisor Readiness

Date: 2026-05-31

## Verdict

Wave 10 moved AWK from a strong fixture-shadow harness into a live-readonly,
dual-run-ready kernel for Ivy and Jarvis Weekly.

- Independent kernel/harness readiness: 84%.
- Real OpenClaw workflow test readiness: 79%.
- Current gate level: live-readonly plus local dual-run packet.
- Not yet allowed: owned live execution, live surface writes, Telegram sends,
  public publish, deploy, auth, trading, or destructive actions.

## What Landed

- Workflow graph and ledger: owned runner can discover queued or waiting work,
  drive `WorkflowKernel`, publish local human gates, ingest checked decisions,
  and resume idempotently.
- Prompt registry and Work Ledger: stage runs now record prompt bundle hash,
  context packet ref/hash, rendered context hash, and exportable stage-run audit
  state.
- Surface adapters: generic surface capability contracts now describe readback,
  dry-run/live mode, decision ingest, clear semantics, and external effects.
  LocalMarkdown remains executable; Obsidian, Telegram, and Sheets have
  deterministic dry-run adapters.
- Policy layer: approval fingerprints now bind risk classes and hard gates, and
  human-gate surface lifecycle calls policy-check adapter side effects before
  invoking publish/readback/ingest.
- E2E packet: two-lane onboarding now writes an evidence manifest, per-lane
  reports, review-note readbacks, readiness deltas, and explicit next
  owned-execution gates.
- OpenClaw bridge: source-side read-only dual-run proof helper compares two
  live exports and writes operator-readable JSON/Markdown proof packets.

## Evidence

Integrated AWK branch:

- `python3 -m unittest discover -s tests`: 127 tests passed.
- `./scripts/check.sh`: stdlib unittest and pytest both passed, 127 tests.
- Local fixture packet: `/tmp/awk-wave10-dual-run-packet`.

OpenClaw integration branch:

- `python3 -m unittest workspace-main.tests.test_export_awk_lane_fixture workspace-main.tests.test_build_awk_dual_run_proof`: 11 tests passed.
- Fresh oldmac read-only exports were run by piping the merged local exporter to
  `python3 -` over SSH; no oldmac writes were performed.
- Live dual-run proof packet: `/tmp/openclaw-awk-wave10-live-dual-run/proof/awk-dual-run-proof.json`.
- Operator Markdown proof: `/tmp/openclaw-awk-wave10-live-dual-run/proof/awk-dual-run-proof.md`.
- AWK live packet from fresh oldmac fixtures: `/tmp/awk-wave10-live-dual-run-packet/summary.json`.

Fresh live-readonly proof status:

- Ivy: stable across two exports, `reviewable_fixture`, `live_readonly`.
- Weekly: stable across two exports, `reviewable_fixture`, `live_readonly`.
- OpenClaw proof helper failure modes: none.
- AWK packet overall: `human_review_required`.
- Local review notes created: 3.
- Mutation permission granted: false.

## Remaining Gates

- Human-decision readback must be exercised from the generated local review
  notes for Ivy and Weekly.
- A fresh dual-run equivalence receipt must be produced after human-decision
  readback.
- Mutation-capable adapters must be fail-closed by manifest, not by convention.
- Approved mutation stages need a scoped approval capability carried from human
  gate to the exact adapter invocation.
- Real Obsidian/Telegram/Sheets adapters still need approved live-readback
  testing before they can replace dry-run/local surfaces.

## Readiness By Principle

- Workflow graph: 88%. Generic runner and lane definitions are executable; live
  owned execution is still gated.
- Prompt registry: 84%. Versioned hashes and ledger audit are in place; lane
  prompt coverage can broaden.
- Work Ledger: 86%. Prompt/context/run/receipt provenance is queryable; recovery
  receipts should now be tested on more lanes.
- Runner: 82%. Owned local loop is real; live lane execution remains behind
  explicit gates.
- Surface adapters: 76%. Contract and dry-run adapters exist; live adapters are
  intentionally blocked.
- Policy layer: 83%. Key replay and surface-preflight bugs are fixed; scoped
  live-apply capability remains the main hardening task.

