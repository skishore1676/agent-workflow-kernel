# Wave 17 OpenClaw Owned Completion Bridge

Date: 2026-06-01

## Verdict

AWK now has an owned-completion bridge for migrated OpenClaw Blackboard work IDs.
The previous gap was real: OpenClaw could mark an AWK handoff `done`, but AWK did
not own a durable workflow instance for that same work ID. The bridge now creates
or resumes one AWK workflow instance per OpenClaw artifact ID and marks it
terminal only after both conditions are true:

- OpenClaw has ingested Suman's Blackboard acknowledgement into a handoff file.
- OpenClaw's agent-review runner has a `done` receipt for that artifact ID.

The bridge is read-only with respect to OpenClaw. It writes only the AWK SQLite
ledger selected by the operator.

## Implemented Behavior

Code:

- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/owned_completion.py`
- `scripts/openclaw_owned_completion_bridge.py`
- `tests/test_openclaw_owned_completion_bridge.py`

Bridge workflow:

1. `capture_openclaw_surface_artifact`
2. `blackboard_acknowledgement`
3. `verify_openclaw_review_runner`
4. terminal `done`

State handling:

- If the OpenClaw handoff is missing, the AWK instance remains
  `waiting_on_human`.
- If the handoff is acknowledged but the OpenClaw runner receipt is missing, the
  AWK instance keeps `verify_openclaw_review_runner` queued for the next pickup.
- If both acknowledgement and runner receipt exist, AWK records the human
  decision, runs the verification stage, and writes `workflow_terminal`.
- If rerun after terminal completion, the bridge reports `already_terminal` and
  does not duplicate terminal events.

## Local Verification

Commands:

```bash
python3 -m unittest tests.test_openclaw_owned_completion_bridge
python3 -m unittest discover -s tests
./scripts/check.sh
```

Results:

- Focused bridge tests: 3 passed.
- Full unittest suite: 184 passed.
- Full check script: 184 passed under unittest and 184 passed under pytest.

The bridge tests cover:

- acknowledged Blackboard artifacts reaching AWK terminal workflow state;
- missing runner receipt leaving the verify stage queued until the next pickup;
- unacknowledged artifact waiting at the human gate;
- rerun idempotence after terminal completion.

## oldmac Verification

Commit deployed on oldmac:

```text
ff73a73 Add OpenClaw owned completion bridge
```

oldmac check:

```bash
cd /Users/sunny/code/agent-workflow-kernel && ./scripts/check.sh
```

Result:

- 184 passed under unittest.
- 184 passed under pytest.

Bridge command:

```bash
cd /Users/sunny/code/agent-workflow-kernel
python3 scripts/openclaw_owned_completion_bridge.py \
  --openclaw-root /Users/sunny/.openclaw \
  --ledger /tmp/awk-openclaw-owned-completion-20260601.sqlite3 \
  --cutover-receipt /private/tmp/openclaw-awk-blackboard-native-20260601-1/awk-cutover/cutover_receipt.json \
  --artifact-id awk-suman-loop-weekly-20260601-1 \
  --summary-json /tmp/awk-openclaw-owned-completion-20260601-summary.json
```

Result:

- `ok: true`
- `artifact_count: 3`
- workflow: `openclaw_migrated_lane_completion`
- version: `0.1.0`

Artifacts proven terminal:

| Artifact ID | AWK instance | Workflow status | Human decision | Runner stage owner | Terminal events |
| --- | --- | --- | --- | --- | --- |
| `awk-cutover-ivy-779016d92628` | `openclaw-owned:awk-cutover-ivy-779016d92628` | `done` | `acknowledged` | `main` | 1 |
| `awk-cutover-weekly-381730bb7382` | `openclaw-owned:awk-cutover-weekly-381730bb7382` | `done` | `acknowledged` | `main` | 1 |
| `awk-suman-loop-weekly-20260601-1` | `openclaw-owned:awk-suman-loop-weekly-20260601-1` | `done` | `acknowledged` | `main` | 1 |

Rerun proof:

```text
stop_reasons:
- awk-cutover-ivy-779016d92628: already_terminal
- awk-cutover-weekly-381730bb7382: already_terminal
- awk-suman-loop-weekly-20260601-1: already_terminal
terminal_counts: all 1
```

SQLite readback:

- `workflow_instances`: 3 rows, all `status = done`, `current_stage_id = NULL`.
- `stage_runs`: all capture, acknowledgement, and verify stages `succeeded`.
- `human_decisions`: 3 rows, all `decision = acknowledged`,
  `human_ref = Suman`, `canonical_surface = openclaw_blackboard`.
- `events`: each instance has exactly one `workflow_terminal`.

## Readiness Classification

This moves the OpenClaw migrated-lane proof from live handoff completion to
AWK-owned terminal completion for the tested artifacts.

Current classification:

- Runner mechanics: owned execution proof.
- OpenClaw migrated artifact bridge: owned terminal proof for the tested AWK
  cutover artifacts.
- Full production cutover: still should remain gated until this bridge is wired
  into the normal OpenClaw/AWK runner schedule instead of being run as an
  operator-invoked proof command.
