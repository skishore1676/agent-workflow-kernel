# Wave 13 Supervisor Oldmac Adoption Report

## Verdict

AWK is now an oldmac shadow-execution candidate, not yet an owner of live
OpenClaw mutation.

What is proven:

- OpenClaw oldmac source was cleaned recoverably and switched to a pushed Wave
  13 branch.
- oldmac `verify_host_setup.sh` passes after `post_pull_sync.sh`.
- AWK is installed on oldmac under `/Users/sunny/code/agent-workflow-kernel`
  with a repo-local venv.
- AWK tests pass on oldmac through `./scripts/check.sh`.
- Deployed OpenClaw exported live-readonly Ivy and Weekly fixtures from oldmac.
- AWK built a two-lane local review packet from those live-readonly fixtures.
- `AutomatedSumanReviewer` reviewed and ingested all three local/test-only
  gates without granting mutation permission.

Still blocked from live ownership:

- No real Obsidian/Northstar write adapter has been enabled.
- No real Telegram send adapter has been enabled.
- No public publish, auth/deploy, trading, destructive action, or OpenClaw
  runtime mutation is authorized by this wave.
- The two-lane proof is shadow/local evidence. Owned execution requires a
  separate gate after live write adapters and runner integration are explicitly
  approved.

## Branches And Commits

AWK integration branch:

- `317260b Merge wave 13 sandbox surface connectors`
- `d5ff388 Merge wave 13 automated reviewer loop`
- `9ed9aef Prefer venv Python in AWK checks`
- `93d7924 Add OpenClaw automated review packet harness`

OpenClaw integration branch:

- `5558a21 Merge wave 13 oldmac AWK adoption bridge`
- `c11e673 Promote oldmac goal builder skill`
- `226f151 Promote OR supply chain agent memory note`

Oldmac OpenClaw runtime checkout:

- Branch: `codex/wave4-openclaw-fixture-exporter-integration`
- Commit: `226f151`
- Pre-branch dirty state backup: `/tmp/openclaw-wave13-prebranch-backup-20260601T065519`
- Pre-branch dirty state stash: `wave13-pre-adoption-cleanup`

## Verification

Local AWK:

```bash
./scripts/check.sh
# 155 unittest tests OK; 155 pytest tests passed
```

Oldmac AWK:

```bash
cd /Users/sunny/code/agent-workflow-kernel
./scripts/check.sh
# 155 unittest tests OK; 155 pytest tests passed
```

Local OpenClaw:

```bash
python3 -m unittest workspace-main.tests.test_export_awk_lane_fixture \
  workspace-main.tests.test_build_awk_dual_run_proof \
  workspace-main.tests.test_oldmac_awk_adoption_bridge
# 18 tests OK

python3 scripts/validate_openclaw_skills.py
# PASS (59 skills)
```

Oldmac OpenClaw:

```bash
cd /Users/sunny/.openclaw
./scripts/post_pull_sync.sh
./scripts/verify_host_setup.sh
# PASS
```

## Artifacts

OpenClaw oldmac adoption bridge:

- `/tmp/openclaw-awk-adoption-bridge-wave13-live/bridge-receipt.md`
- `/tmp/openclaw-awk-adoption-bridge-wave13-live/proof/awk-dual-run-proof.md`
- Status: `proof_complete`
- Proof mode: `adoption_evidence`
- Blocks: none
- Warnings: none
- Safety: local output only; no oldmac writes; no operator-surface writes

AWK oldmac two-lane onboarding packet:

- `/tmp/awk-wave13-oldmac-two-lane-onboarding/README.md`
- `/tmp/awk-wave13-oldmac-two-lane-onboarding/summary.json`
- Status: `human_review_required`
- Local review notes created: 3
- Mutation permission granted: false

AWK oldmac automated review packet:

- `/tmp/awk-wave13-oldmac-auto-review/README.md`
- `/tmp/awk-wave13-oldmac-auto-review/summary.json`
- Status: `reviewed`
- Decisions:
  - Ivy `accept_source_approval`: `selected`
  - Ivy `p5_final_approval`: `approve_packet`
  - Weekly `suman_review_gate`: `read_clear`
- Reviewer identity: `Suman(test automated reviewer)`
- Mutation permission granted: false
- Operator-surface writes performed: false

## Readiness

Current readiness: `live-readonly plus local shadow autoreview`.

Next readiness target: `dual-run write-sandbox`.

The next wave should connect the sandbox Obsidian and Telegram adapters into
the runner path and produce oldmac-local write artifacts under explicit sandbox
roots. Real Obsidian vault writes and real Telegram sends should remain blocked
until a separate Suman approval gate.
