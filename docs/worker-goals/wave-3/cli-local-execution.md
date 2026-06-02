# Wave 3 Goal: CLI And Local Execution

## Goal

Implement a small operator CLI and local execution path that can validate,
compile, and run example workflows through local fake adapters.

## Target Files

Own these files:

- `packages/kernel/agent_workflow_kernel/cli.py`
- `packages/kernel/agent_workflow_kernel/local_runner.py`
- `tests/test_cli_local_execution.py`
- optional minimal export updates in `packages/kernel/agent_workflow_kernel/__init__.py`

Avoid editing OpenClaw adapter files, policy internals, storage schema, prompt
registry internals, and workflow fixture YAML except for bug fixes discovered
by your tests.

## Inputs To Read

- `docs/control.md`
- `docs/synthesis/wave-1-combined-view.md`
- `docs/synthesis/validation-matrix.md`
- `packages/kernel/agent_workflow_kernel/dsl.py`
- `packages/kernel/agent_workflow_kernel/storage.py`
- `packages/kernel/agent_workflow_kernel/runner.py`
- `packages/kernel/agent_workflow_kernel/local_adapters.py`
- `workflows/bumblebee_quality_review.yaml`

## Acceptance Criteria

- Provide `python -m agent_workflow_kernel.cli validate <workflow.yaml>`.
- Provide `python -m agent_workflow_kernel.cli compile <workflow.yaml>` that
  prints canonical JSON.
- Provide `python -m agent_workflow_kernel.cli run-local <workflow.yaml>` that
  creates a temporary or specified SQLite ledger, walks the workflow with local
  fake adapters, and prints a concise JSON run summary.
- Running local execution must write receipts/events to SQLite and stop safely
  at human gates or terminal states.
- No OpenClaw, Telegram, Obsidian, broker, auth, or external-send behavior is
  invoked.
- Include subprocess-style tests using the current Python executable.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
.venv/bin/python -m pytest
```

Commit with:

```bash
git commit -m "Implement CLI and local execution"
```

