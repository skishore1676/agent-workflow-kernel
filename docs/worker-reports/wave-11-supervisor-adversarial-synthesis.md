# Wave 11 Supervisor Adversarial Audit Synthesis

Date: 2026-06-01

Supervisor goal: run two independent adversarial audits before Suman's manual
end-to-end lane review:

- OpenClaw-blind AWK generic kernel audit.
- OpenClaw-aware integration audit against Ivy and Jarvis Weekly live-readonly
  evidence.

## Source Reports

- Blind AWK audit:
  `docs/worker-reports/wave-11-blind-awk-adversarial-audit.md`
- OpenClaw-aware audit:
  `/Users/suman/code/openclaw-core/workspace-main/docs/worker-reports/wave-11-openclaw-aware-adversarial-audit.md`

## Executive Verdict

Manual review can proceed only as a narrow local shadow review of the Ivy/P5
packet. It must not be described as live onboarding, owned execution, public
publishing approval, Obsidian/Blackboard approval, Telegram approval, deploy
approval, auth approval, trading approval, or runtime mutation approval.

The independent kernel is not ready for generic owned execution. The blocking
issues are fail-closed policy/guard enforcement and safe recovery after a stage
may have started adapter work. The OpenClaw bridge is safer than the generic
kernel audit sounds because the current proof path is read-only/local-only, but
it is not yet deployed or durable enough to be an adoption path.

Recommended readiness classification:

- Independent AWK kernel: fixture shadow, not owned execution.
- OpenClaw lane review: live-readonly plus local shadow packet.
- OpenClaw adoption: blocked until deployment/provenance/handoff and kernel
  safety fixes land.

## Highest-Risk Findings To Fix

1. Policy and transition guards are parsed but not enforced by execution.
   Declared workflow/stage policy can be weaker in practice than intended, and
   guarded transitions can advance without evaluating the guard.

2. Stale-lease recovery can replay a claimed stage after adapter work may have
   begun. The ledger needs a started/preflight state before invocation and must
   block or recover by idempotency proof instead of blindly requeueing.

3. Resume semantics are not yet durable enough. Inputs and workflow definitions
   are not persisted as canonical snapshots, so restart can change prompt
   context or silently use a changed workflow graph.

4. Retry policy exists in the DSL but is not implemented as append-only
   attempts with failure classification, budget checks, and idempotency proof.

5. Required outputs, artifact roles, and outcome schemas are not enforced before
   transition, so a successful adapter can advance with missing contract data.

6. Human decision ingest is not strongly bound to the configured canonical
   surface/human source, and readback is not a hard prerequisite in every
   ingest path.

7. OpenClaw exporter/proof helper are not deployed on oldmac yet. The successful
   live-readonly proof used the allowed stdin-piped local script fallback, not
   the documented deployed oldmac command path.

8. OpenClaw handoff is local and developer-shaped. The packet is reviewable
   under `/tmp`, but there is not yet a durable operator-surface pointer and
   readback receipt.

## What Is Proven

- The OpenClaw exporter and proof helper do not show evidence of live mutation,
  Telegram sends, Obsidian/Northstar writes, auth refresh, deploy, trading, or
  public publishing.
- Ivy and Weekly live-readonly exports were stable across two runs when the
  local exporter was piped to oldmac.
- The OpenClaw proof helper returned `reviewable_proof` with no failure modes.
- The AWK local packet generated three local review notes and preserved
  `mutation_permission_granted=false`.
- Required worker verification passed:
  - AWK worker branch: `python3 -m unittest discover -s tests`, 127 tests.
  - AWK worker branch: `./scripts/check.sh`, unittest passed; optional venv
    pytest skipped because `.venv/bin/python` is missing.
  - OpenClaw worker branch: `python3 -m unittest
    workspace-main.tests.test_export_awk_lane_fixture
    workspace-main.tests.test_build_awk_dual_run_proof`, 11 tests.

## What Is Not Proven

- AWK cannot yet be called a generic independent owned-execution harness for
  arbitrary lanes.
- OpenClaw has not been synced so the documented deployed exporter/proof helper
  commands do not yet work on oldmac.
- Weekly has not completed a decision-readback path; it is currently a
  read/clear case.
- No live Obsidian, Blackboard, Telegram, deploy, auth, trading, or public
  publishing handoff has been tested or approved.
- No mutation-capable adapter has been exercised through AWK.

## Manual Review Recommendation

Proceed with Suman's manual review only for this exact scope:

- Lane: Ivy/P5.
- Packet README:
  `/tmp/awk-wave11-live-readonly-packet/README.md`
- Review card:
  `/tmp/awk-wave11-live-readonly-packet/review_notes/ivy/review_cards/ivy_jonah_editorial-openclaw-ivy-agent-to-agent-communication-live-6-p5_final_approval-p5_final_approval-review-ivy-p5_final_approval.md`
- Boundary: local AWK shadow review only.

Do not proceed with live onboarding or any mutation-capable adoption from this
review. If Suman approves/rejects during this manual review, capture that as
supervisor-thread evidence only unless Suman separately approves a write to an
operator surface.

## Next Fix Wave

The next implementation wave should raise the kernel and OpenClaw bridge to an
honest 80-85 percent by fixing:

1. Effective policy compiler and guarded transition enforcement.
2. Stage `started`/preflight receipt plus safer stale-lease recovery.
3. Durable instance input snapshot and workflow definition hash.
4. Append-only retry attempts with idempotency-aware retry policy.
5. Output/artifact validation before transition.
6. Canonical human-source and readback-required decision ingest.
7. OpenClaw fixture schema plus exporter provenance/freshness checks.
8. OpenClaw deployed oldmac exporter/proof-helper readback.
9. Durable no-mutation handoff receipt for the packet pointer.

## Supervisor Decision

Do not block Suman from looking at the Ivy local packet. Do block any claim that
the independent kernel or OpenClaw integration is ready for live owned
execution. The right next move is: Suman manually reviews the Ivy local shadow
packet while the implementation wave fixes the fail-closed kernel and deployed
OpenClaw handoff gaps.
