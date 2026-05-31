# Wave 4 Goal: OpenClaw Shadow Runner

## Goal

Build the AWK-side shadow runner that consumes OpenClaw-exported lane fixtures
and produces a concrete adoption report. This is the end-to-end proof harness:
OpenClaw exports a fixture, AWK reads it, maps it through adapters, runs parity,
and writes a report without mutating OpenClaw.

## Target Files

Own these files:

- `scripts/openclaw_shadow_run.py`
- `tests/test_openclaw_shadow_runner.py`
- `docs/synthesis/wave-4-shadow-run.md`
- optional fixture files under `fixtures/openclaw/shadow_runner/`

Avoid editing generic kernel modules, lane adapter modules, and OpenClaw source.
If a tiny public import/export is required, keep it isolated and explain it.

## Inputs To Read

- `docs/control.md`
- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/readonly.py`
- `packages/kernel/agent_workflow_kernel/parity.py`
- `docs/worker-goals/wave-4/ivy-lane-adoption.md`
- `docs/worker-goals/wave-4/jarvis-weekly-update-adoption.md`
- `docs/worker-goals/wave-4/openclaw-lane-fixture-exporter.md`

## Acceptance Criteria

- Provide a CLI:
  - `python3 scripts/openclaw_shadow_run.py --fixture <fixture.json> --report <report.json>`
  - `python3 scripts/openclaw_shadow_run.py --fixture <fixture.json> --report -`
- Accept at least generic OpenClaw read-only fixtures immediately.
- Detect lane-specific fixture payloads for `ivy` and `weekly` even if the
  lane-specific adapter branch has not merged yet; in that case produce a
  useful `adapter_missing` report instead of crashing.
- Produce deterministic JSON with:
  - fixture identity and lane;
  - mapping summary;
  - receipts generated;
  - parity/adoption status;
  - blocked external actions;
  - next recommended adoption step.
- Include at least one fixture and tests proving stdout report output, file
  report output, unsupported lane behavior, and deterministic ordering.
- No live OpenClaw calls, no oldmac mutation, no operator-surface writes.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
.venv/bin/python -m pytest
```

Commit with:

```bash
git commit -m "Add OpenClaw shadow runner"
```
