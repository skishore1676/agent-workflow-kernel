# Wave 4 Goal: Jarvis Weekly Update Adoption

## Goal

Pressure-test the harness against the Suman/Jarvis weekly update lane. This is
the safer operator-facing lane: the harness should understand the generated
weekly personal check-in surface, map it to workflow gates, and prove that AWK
can carry the lane without silently changing the Obsidian/Blackboard behavior.

## Target Files

Own these files:

- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/weekly_update.py`
- `workflows/jarvis_weekly_update_shadow.yaml`
- `fixtures/openclaw/weekly_update/*.json`
- `tests/test_openclaw_weekly_update_adoption.py`
- optional export updates in
  `packages/adapters/openclaw/agent_workflow_kernel_openclaw/__init__.py`

Avoid editing generic kernel internals, the existing example workflows, or
OpenClaw source.

## Inputs To Read

- `docs/control.md`
- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/readonly.py`
- `packages/kernel/agent_workflow_kernel/parity.py`
- `/Users/suman/code/openclaw-core/workspace-main/scripts/update_review_inbox.py`
- `/Users/suman/code/openclaw-core/workspace-main/docs/blackboard_decision_loop.md`
- `/Users/suman/code/openclaw-core/workspace-main/PROJECT_MAP.md`

## Acceptance Criteria

- Add `jarvis_weekly_update_shadow.yaml` as a portable workflow with stages for:
  weekly artifact discovery, Blackboard/reference-card readback, Suman review
  gate, and follow-up routing.
- Define weekly-update fixture helpers for mode, note path, item id, source
  artifact, Blackboard bucket, owner, evidence link, and checked/read state.
- Convert a supplied fixture into AWK stage observations, receipts, and a
  deterministic adoption report.
- Include fixtures for:
  - weekly check-in ready for Suman review;
  - weekly check-in cleared/read with no follow-up.
- No code may write Obsidian, Telegram, oldmac, or OpenClaw state. This slice is
  a shadow reader plus workflow mapper.
- Tests must prove the workflow validates and the human gate remains explicit.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
.venv/bin/python -m pytest
```

Commit with:

```bash
git commit -m "Adopt Jarvis weekly update shadow lane"
```
