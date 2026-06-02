# Wave 3 Goal: Parity Reporting

## Goal

Implement fixture-based parity reporting so the kernel can compare current or
simulated host receipts with kernel receipts before any OpenClaw replacement.

## Target Files

Own these files:

- `packages/kernel/agent_workflow_kernel/parity.py`
- `fixtures/parity/*.json`
- `tests/test_parity_reporting.py`
- optional minimal export updates in `packages/kernel/agent_workflow_kernel/__init__.py`

Avoid editing OpenClaw adapter implementation, storage, runner, policy, prompt,
or workflow fixture files except for a tiny fixture reference if necessary.

## Inputs To Read

- `docs/control.md`
- `docs/synthesis/openclaw-adapter.md`
- `docs/synthesis/validation-matrix.md`
- `fixtures/example_workflow_validation_report.json`
- `packages/kernel/agent_workflow_kernel/contracts.py`
- `packages/kernel/agent_workflow_kernel/receipts.py`

## Acceptance Criteria

- Define a parity report model comparing expected host receipt fields to actual
  kernel receipt fields.
- Report equivalent, different, missing, extra, and ignored fields.
- Include fixture JSON for at least Bumblebee quality review and a human-gate
  surface readback shape.
- Produce deterministic JSON report output.
- Include tests for equivalent receipts, documented deltas, missing fields, and
  deterministic report ordering.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
.venv/bin/python -m pytest
```

Commit with:

```bash
git commit -m "Implement parity reporting fixtures"
```

