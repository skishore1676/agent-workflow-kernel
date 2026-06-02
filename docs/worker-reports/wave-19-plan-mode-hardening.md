# Wave 19 Plan Mode Hardening

Date: 2026-06-01

## Goal

Exercise AWK owned-completion plan mode from oldmac against the real OpenClaw
tree, fix any final read-only hardening gaps, and stop before live `--run`
execution.

## Finding

The scheduler-safe plan command was read-only and correctly avoided ledger
writes, but it required an explicit artifact list or a cutover receipt. That was
too manual for a real scheduler plan loop. A plan-mode runner should be able to
inspect the OpenClaw artifact outbox and discover AWK migrated-lane review
records by itself.

## Change

`discover_openclaw_artifacts` now falls back to scanning:

```text
workspace-main/state/artifact_outbox/records/*.json
```

when no cutover receipt or explicit artifact IDs are supplied. It only treats a
record as owned-completion candidate when it is clearly AWK/OpenClaw migrated
lane cargo:

- `artifact_type == "awk_human_gate_review"`;
- or the record has an `awk` metadata object;
- or `owner == "awk_openclaw"` with review action `continue_awk_workflow`.

Non-AWK review records are ignored.

## Verification

Local:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_openclaw_owned_completion_bridge
```

Result: 12 tests passed.

```bash
PYTHONDONTWRITEBYTECODE=1 ./scripts/check.sh
```

Result: 199 unittest tests and 199 pytest tests passed.

Oldmac:

```bash
cd /Users/sunny/code/agent-workflow-kernel
./scripts/dev_setup.sh
PYTHONDONTWRITEBYTECODE=1 ./scripts/check.sh
```

Result: 199 unittest tests and 199 pytest tests passed under the repo venv.

Live OpenClaw plan mode without artifact IDs:

```bash
.venv/bin/python scripts/openclaw_owned_completion_bridge.py \
  --openclaw-root /Users/sunny/.openclaw \
  --ledger /tmp/awk-wave19-auto-plan.sqlite3 \
  --summary-json /tmp/awk-wave19-auto-plan.json
```

Result:

- mode: `plan`
- `live_mutation_enabled`: false
- `ledger_write_enabled`: false
- artifact count: 3
- runnable count: 3
- no SQLite ledger was created
- discovered artifacts:
  - `awk-cutover-ivy-779016d92628`: `create_or_resume`, `would_reach_terminal`
  - `awk-cutover-weekly-381730bb7382`: `create_or_resume`, `would_reach_terminal`
  - `awk-suman-loop-weekly-20260601-1`: `create_or_resume`, `would_reach_terminal`

Existing-ledger resumability plan:

```bash
.venv/bin/python scripts/openclaw_owned_completion_bridge.py \
  --openclaw-root /Users/sunny/.openclaw \
  --ledger /tmp/awk-openclaw-owned-completion-20260601-v2.sqlite3 \
  --summary-json /tmp/awk-wave19-existing-ledger-plan-hash.json
```

Result:

- mode: `plan`
- artifact count: 3
- runnable count: 0
- planned actions: all `already_terminal`
- SHA-256 of the existing AWK ledger was unchanged before versus after plan

## Live Safety

The change only reads OpenClaw JSON records during plan mode. It does not write
OpenClaw, Obsidian, Telegram, launchd/cron, credentials, trading, deployment, or
public-send surfaces. The AWK ledger is still only written when the CLI is
called with `--run`.

## Readiness

Plan mode is now live-readonly complete for the owned-completion bridge. The
next step is a human-accompanied `--run` against the live oldmac OpenClaw tree
when Suman is ready. That will write only the AWK ledger and should still avoid
Telegram, Obsidian, launchd/cron, deployment, auth, trading, and public sends.
