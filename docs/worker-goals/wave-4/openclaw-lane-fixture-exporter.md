# Wave 4 Goal: OpenClaw Lane Fixture Exporter

## Goal

In `openclaw-core`, build the OpenClaw-side read-only exporter that emits
AWK-consumable lane fixtures for Ivy/Jonah and the Suman/Jarvis weekly update
surface. This is the bridge from current OpenClaw artifacts to the independent
harness.

## Target Repository

`/Users/suman/code/openclaw-core`

## Target Files

Own these files in `openclaw-core`:

- `workspace-main/scripts/export_awk_lane_fixture.py`
- `workspace-main/tests/test_export_awk_lane_fixture.py` or the nearest
  existing test location/pattern if this repo uses a different convention
- `workspace-main/docs/awk_lane_fixture_exporter.md`

Avoid editing live runtime state, `cron/jobs.json`, credentials, oldmac files,
Northstar notes, Telegram queues, or AWK source.

## Inputs To Read

- `/Users/suman/code/openclaw-core/AGENTS.md`
- `/Users/suman/code/openclaw-core/SOURCE_BOUNDARIES.md`
- `/Users/suman/code/openclaw-core/config/work_ledger.json`
- `/Users/suman/code/openclaw-core/workspace-main/scripts/update_review_inbox.py`
- `/Users/suman/code/openclaw-core/workspace-main/scripts/work_ledger/handlers/or_research.py`
- `/Users/suman/code/openclaw-core/workspace-main/scripts/work_ledger/adapters/ivy_jonah.py`
- `/Users/suman/code/agent-workflow-kernel/docs/worker-goals/wave-4/ivy-lane-adoption.md`
- `/Users/suman/code/agent-workflow-kernel/docs/worker-goals/wave-4/jarvis-weekly-update-adoption.md`

## Acceptance Criteria

- Provide a read-only CLI:
  - `python3 workspace-main/scripts/export_awk_lane_fixture.py --lane ivy --output <path-or->`
  - `python3 workspace-main/scripts/export_awk_lane_fixture.py --lane weekly --output <path-or->`
- Default to local repo/runtime paths, with explicit `--openclaw-root`,
  `--workspace-main`, and `--vault-root` overrides for tests.
- Emit JSON shaped for AWK:
  - `fixture_id`, `lane`, `generated_at`, `source_root`;
  - `mapping` compatible with the AWK OpenClaw read-only adapter;
  - lane-specific payload under `ivy` or `weekly_update`;
  - `artifacts`, `surface_refs`, and `runtime_refs` where observable;
  - `redactions` list for anything intentionally withheld.
- Never print secrets. Redact credential-like paths, token values, and full
  auth paths.
- Tests must use temporary fixture directories and not require oldmac, Obsidian,
  Telegram, network, Google APIs, or live runtime state.
- Do not mutate OpenClaw state. The only write is the explicit `--output` file.

## Verification

Run the repo-appropriate tests. Prefer:

```bash
python3 -m unittest discover -s workspace-main/tests
```

If that test root does not exist, use the nearest existing OpenClaw unit-test
pattern and document it in the final response.

Commit with:

```bash
git commit -m "Export AWK lane fixtures from OpenClaw"
```
