# Wave 18 Owned Completion Scheduler

Date: 2026-06-01

## Verdict

AWK owned completion now has a scheduler-friendly runner entry point. The
existing OpenClaw owned-completion bridge remains the execution core, but the
default CLI/API behavior is now no-op planning: it discovers candidate migrated
OpenClaw Blackboard artifact IDs, reads any existing AWK ledger state, reports
the graph-derived next stage/owner, and exits without creating or mutating the
ledger.

Explicit execution requires `--run` or `run=True`. Run mode still has no
OpenClaw, Obsidian, Telegram, launchd, cron, auth, trading, deploy, or
destructive mutation authority. It writes only the selected AWK SQLite ledger
and optional JSON summary.

## Implemented Behavior

Code:

- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/owned_completion.py`
- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/__init__.py`
- `scripts/openclaw_owned_completion_bridge.py`
- `tests/test_openclaw_owned_completion_bridge.py`

New API:

- `plan_owned_completion_run(...)`: read-only/no-op scheduler plan. It does not
  create a missing ledger.
- `run_owned_completion_scheduler(..., run=False)`: scheduler entry point. Plan
  mode is default.
- `run_owned_completion_scheduler(..., run=True)`: explicit AWK ledger execution
  using the existing owned-completion bridge.

CLI behavior:

```bash
python3 scripts/openclaw_owned_completion_bridge.py \
  --openclaw-root /Users/sunny/.openclaw \
  --ledger /tmp/awk-openclaw-owned-completion.sqlite3 \
  --cutover-receipt /path/to/cutover_receipt.json \
  --summary-json /tmp/awk-openclaw-owned-completion-plan.json
```

The command above is a no-op plan. It exits `0`, writes only the requested
summary JSON, and does not create the ledger.

```bash
python3 scripts/openclaw_owned_completion_bridge.py \
  --openclaw-root /Users/sunny/.openclaw \
  --ledger /tmp/awk-openclaw-owned-completion.sqlite3 \
  --cutover-receipt /path/to/cutover_receipt.json \
  --summary-json /tmp/awk-openclaw-owned-completion-run.json \
  --run
```

The command above explicitly runs the AWK ledger completion pass. It returns a
non-zero exit code when execution is still waiting or blocked, preserving the
previous bridge semantics for explicit runs.

## Resumability

Plan and run summaries include:

- `mode`, `dry_run`, `read_only`, `live_mutation_enabled`,
  `ledger_write_enabled`, and `openclaw_write_count`.
- per-artifact `planned_action`, `predicted_stop_reason`, `workflow_status`,
  `current_stage_id`, `terminal_event_count`, and `stage_runs`.
- `next` with graph-derived `stage_id`, `stage_type`, `owner`, and actor refs.
- `predicted_next` for the state expected after an execution pickup when the
  current plan can infer it from OpenClaw handoff/runner evidence.

Terminal reruns are idempotent: a terminal instance reports
`planned_action: already_terminal` in plan mode and `stop_reason:
already_terminal` in run mode, with `terminal_event_count` remaining `1`.

Waiting states remain resumable:

- missing Blackboard handoff: reports `openclaw_acknowledgement_missing` and
  the human gate owner/stage.
- acknowledged handoff but missing runner receipt: reports
  `openclaw_runner_done_receipt_missing` and the OpenClaw runner verification
  stage.

## Future launchd/cron Wiring

No live cron or launchd files were edited in this slice.

Later wiring should call the CLI in plan mode first and publish/store only the
summary artifact. A scheduler can treat the JSON as the durable pickup plan:

```bash
cd /Users/sunny/code/agent-workflow-kernel
python3 scripts/openclaw_owned_completion_bridge.py \
  --openclaw-root /Users/sunny/.openclaw \
  --ledger /Users/sunny/.openclaw/workspace-main/state/awk/owned_completion.sqlite3 \
  --cutover-receipt /private/tmp/openclaw-awk-blackboard-native-20260601-1/awk-cutover/cutover_receipt.json \
  --summary-json /Users/sunny/.openclaw/workspace-main/state/awk/owned_completion_plan.json
```

After a separate approval/adoption gate, the scheduled command can add `--run`.
That change should remain explicit in the runtime job payload so a routine
read-only monitor cannot silently become a ledger-mutating runner.

## Acceptance Evidence

Focused tests:

```bash
python3 -m unittest tests.test_openclaw_owned_completion_bridge
```

Result: 9 tests passed.

Full unittest suite:

```bash
python3 -m unittest discover -s tests
```

Result: 190 tests passed.

Check script:

```bash
./scripts/check.sh
```

Result: 190 tests passed under unittest. The script then reported:
`Skipping venv pytest: .venv/bin/python is missing. Run ./scripts/dev_setup.sh first.`

The added tests cover:

- default scheduler plan mode;
- explicit scheduler run mode against OpenClaw-shaped fixtures;
- already-terminal planning/idempotency;
- CLI default no-op behavior;
- no OpenClaw fixture tree writes in plan or run mode.

## Safety Notes

This slice did not mutate oldmac, OpenClaw runtime state, Obsidian, Telegram,
launchd, cron, auth, credentials, trading systems, deploy config, or public
surfaces.
