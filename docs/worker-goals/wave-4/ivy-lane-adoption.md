# Wave 4 Goal: Ivy Lane Adoption

## Goal

Pressure-test the independent harness against the real Ivy/Jonah editorial lane.
Build an OpenClaw adapter slice that can consume OpenClaw-shaped Ivy/Jonah
fixtures, map them to AWK workflow/stage/receipt concepts, and produce a
shadow-adoption report that is strong enough to decide the first takeover
slice.

## Target Files

Own these files:

- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/ivy_lane.py`
- `fixtures/openclaw/ivy_jonah/*.json`
- `tests/test_openclaw_ivy_lane_adoption.py`
- optional export updates in
  `packages/adapters/openclaw/agent_workflow_kernel_openclaw/__init__.py`

Avoid editing kernel internals, generic CLI, OpenClaw source, or unrelated
fixtures unless a small import/export change is necessary.

## Inputs To Read

- `docs/control.md`
- `workflows/ivy_jonah_editorial.yaml`
- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/readonly.py`
- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/mapping.py`
- `packages/kernel/agent_workflow_kernel/parity.py`
- `/Users/suman/code/openclaw-core/config/work_ledger.json`
- `/Users/suman/code/openclaw-core/workspace-main/scripts/work_ledger/adapters/or_research.py`
- `/Users/suman/code/openclaw-core/workspace-main/scripts/work_ledger/adapters/ivy_jonah.py`
- `/Users/suman/code/openclaw-core/workspace-main/scripts/work_ledger/handlers/or_research.py`

## Acceptance Criteria

- Define Ivy/Jonah fixture dataclasses or typed helpers for project id, handoff
  type, P-stage, Ivy actor, Jonah actor, review surfaces, transcript refs, and
  publish-packet refs.
- Convert a supplied fixture into:
  - OpenClaw reference mapping;
  - AWK stage observations aligned to `ivy_jonah_editorial`;
  - read-only adapter receipts;
  - a deterministic adoption report with `ready_for_shadow`,
    `requires_human_gate`, `public_publish_blocked`, and `open_questions`.
- Include fixtures for at least:
  - P3 approval to P4/P5 shadow path;
  - P5 publish-decision path that stops before public publish.
- Explicitly preserve the public-publish human gate. Do not build code that
  posts, publishes, sends externally, or mutates OpenClaw.
- Tests must use local fixture data only and must prove dependency direction:
  OpenClaw adapter code may import the kernel; the kernel must not import this
  adapter package.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
.venv/bin/python -m pytest
```

Commit with:

```bash
git commit -m "Adopt Ivy lane shadow mapping"
```
