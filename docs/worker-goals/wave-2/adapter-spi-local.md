# Wave 2 Goal: Adapter SPI And Local Fakes

## Goal

Implement the adapter service-provider interface and local fake adapters for
runtime, surface, host, and lane behavior.

## Target Files

Own these files:

- `packages/kernel/agent_workflow_kernel/adapters.py`
- `packages/kernel/agent_workflow_kernel/local_adapters.py`
- `tests/test_adapter_spi_local.py`

Avoid editing storage, runner, prompt, policy, and DSL modules except for
minimal import exports in `packages/kernel/agent_workflow_kernel/__init__.py`.

## Inputs To Read

- `docs/synthesis/adapter-interfaces.md`
- `docs/synthesis/openclaw-adapter.md`
- `docs/synthesis/wave-1-combined-view.md`
- `packages/kernel/agent_workflow_kernel/contracts.py`

## Acceptance Criteria

- Define abstract or protocol-style contracts for runtime, surface, host, and
  lane adapters.
- Define a shared invocation envelope and result conversion using existing
  contract objects.
- Implement local fake adapters that return deterministic receipts/results for
  tests and fixtures.
- Ensure no OpenClaw path, Telegram, Obsidian, Northstar, oldmac, or host
  assumption leaks into the kernel.
- Include tests for each adapter family and for unsupported operation failure.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
```

Commit with:

```bash
git commit -m "Implement adapter SPI and local fakes"
```
