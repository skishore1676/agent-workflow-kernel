# Wave 12 Supervisor Readiness

Date: 2026-06-01

## Verdict

Wave 12 moves AWK from a useful local/live-readonly shadow harness to an adoption
candidate for supervised OpenClaw lane trials. It is not yet owned execution.

Current readiness:

- Independent kernel/harness: `adoption_candidate_shadow`
- OpenClaw integration: `live_readonly_local_audit`
- Owned execution: blocked until deployed oldmac proof and human review-decision
  readback are verified.

No live runtime mutation, operator-surface write, Telegram send, deploy, auth
change, trade, or public publish was performed.

## Merged Work

AWK supervisor branch: `codex/bootstrap-agent-workflow-kernel`

- `2745a8f` merged `9af6cf0 Enforce fail-closed policy guards`
- `b460dcb` merged `74896c1 Harden runner recovery provenance`

OpenClaw supervisor branch: `codex/wave4-openclaw-fixture-exporter-integration`

- `d31bb13` merged `2a966c0 Harden OpenClaw AWK proof provenance`

## Kernel Changes Proven

- Workflow/stage/adapter policy now participates in runtime preflight before
  adapter invocation.
- Unknown or high-risk policy metadata fails closed with receipt evidence.
- Transition guards are allowlisted. Unknown guards are rejected or blocked.
- Duplicate transition keys are rejected during validation.
- Stale lease recovery replays only true pre-start claims.
- Started/preflight adapter invocation evidence is durable before invocation.
- Retry attempts are append-only with parent lineage.
- Workflow input snapshot and workflow definition hash are persisted for resume.
- Definition-hash mismatch blocks before adapter invocation.
- Required output fields and artifact roles block before unsafe transition.

## OpenClaw Bridge Changes Proven

- Lane fixtures carry `openclaw.awk_lane_fixture.v1`.
- Fixture exporter records provenance including execution mode, source host/root,
  source commit when available, generated time, and read-only mutation policy.
- Proof helper separates adoption evidence from local audit evidence.
- Proof helper warns or blocks on stale fixtures, missing provenance, mixed
  exporter hashes, and non-deployed exporter execution.
- Proof Markdown includes lane operator facts and a no-mutation handoff receipt.

## Verification

AWK integration checkout:

```bash
python3 -m unittest discover -s tests
./scripts/check.sh
```

Result: 145 unit tests passed. `./scripts/check.sh` also ran pytest from the
available local environment and reported `145 passed`.

OpenClaw integration checkout:

```bash
python3 -m unittest workspace-main.tests.test_export_awk_lane_fixture workspace-main.tests.test_build_awk_dual_run_proof
```

Result: 14 tests passed.

Live-readonly local audit packet:

- Packet: `/tmp/awk-wave12-live-readonly-audit/awk-packet/README.md`
- Ivy P5 review card:
  `/tmp/awk-wave12-live-readonly-audit/awk-packet/review_notes/ivy/review_cards/ivy_jonah_editorial-openclaw-ivy-agent-to-agent-communication-live-6-p5_final_approval-p5_final_approval-review-ivy-p5_final_approval.md`
- Proof Markdown:
  `/tmp/awk-wave12-live-readonly-audit/proof/awk-dual-run-proof.md`
- Proof JSON:
  `/tmp/awk-wave12-live-readonly-audit/proof/awk-dual-run-proof.json`

Proof result:

- Status: `local_audit_with_warnings`
- Failure modes: none
- Warnings: expected, because fixtures were produced by stdin-piping the merged
  local exporter to oldmac instead of running a deployed oldmac exporter script.
- Source: oldmac live-readonly tree at `/Users/sunny/.openclaw`
- Source commit observed: `cb1674b`

AWK packet result:

- Overall readiness: `human_review_required`
- Ivy lane: `shadow_ready_human_gate_required`
- Weekly lane: `waiting_on_human_read_clear`
- Review notes created locally: 3
- Mutation permission granted: `false`
- Ready for live onboarding: `false`

## Remaining Gates

1. Suman reviews the local packet/review card.
2. Deploy or sync the OpenClaw exporter/proof helper to oldmac, then regenerate
   fixtures with `execution_mode=deployed_oldmac_script`.
3. Re-run the dual-run proof in `adoption_evidence` mode.
4. Add/read back a human review decision through the selected non-live or
   explicitly approved surface adapter path.
5. Only after the above, consider dual-run or owned-execution migration for one
   lane.
