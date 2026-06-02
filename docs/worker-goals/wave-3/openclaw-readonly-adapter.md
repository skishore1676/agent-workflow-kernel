# Wave 3 Goal: OpenClaw Read-Only Adapter Boundary

## Goal

Implement the first OpenClaw adapter boundary as read-only compatibility code.
It should model how the kernel would inspect OpenClaw state and emit parity
fixtures without changing OpenClaw runtime behavior.

## Target Files

Own these files:

- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/__init__.py`
- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/readonly.py`
- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/mapping.py`
- `tests/test_openclaw_readonly_adapter.py`

Avoid editing `/Users/suman/code/openclaw-core`. You may inspect it read-only.
Do not call oldmac, Telegram, Obsidian, launchd, or live OpenClaw runtime.

## Inputs To Read

- `docs/synthesis/openclaw-adapter.md`
- `docs/synthesis/adapter-interfaces.md`
- `docs/control.md`
- `packages/kernel/agent_workflow_kernel/adapters.py`
- `/Users/suman/code/openclaw-core/workspace-main/docs/agent_architecture.md`
- `/Users/suman/code/openclaw-core/workspace-main/PROJECT_MAP.md`

## Acceptance Criteria

- Define read-only dataclasses or helpers for OpenClaw reference-host mapping:
  lane id, agent id, Work Ledger-compatible ids, surface refs, and runtime refs.
- Implement a read-only adapter facade that converts supplied fixture data into
  kernel `AdapterInvocation`, `ArtifactRef`, `Receipt`, and `AdapterResult`
  shapes.
- Include explicit guards that prevent mutation operations.
- Tests must use local fixture data only.
- Kernel package must not import this adapter package; dependency direction is
  adapter to kernel.
- No OpenClaw path assumptions may be hardcoded in kernel code.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
.venv/bin/python -m pytest
```

Commit with:

```bash
git commit -m "Implement OpenClaw read-only adapter boundary"
```

