# Wave 2 Goal: Example Workflows And Fixtures

## Goal

Convert the five Wave 1 example workflows into concrete workflow fixtures and
tests that prove the kernel is vision-shaped, not Bumblebee-shaped.

## Target Files

Own these files:

- `workflows/*.yaml`
- `fixtures/*.json`
- `tests/test_example_workflows.py`

Avoid editing kernel implementation modules unless a test exposes a tiny import
export need in `packages/kernel/agent_workflow_kernel/__init__.py`.

## Inputs To Read

- `docs/synthesis/example-workflows.md`
- `docs/synthesis/validation-matrix.md`
- `docs/synthesis/workflow-dsl.md`
- `docs/synthesis/wave-1-combined-view.md`

## Acceptance Criteria

- Add YAML workflow fixtures for:
  - Bumblebee quality review;
  - Ivy/Jonah editorial workflow;
  - trading research gate with no live execution;
  - Radhe review pipeline;
  - deterministic system action with human final gate.
- Each workflow must use generic kernel stage types and adapter ids.
- No workflow may require custom kernel code or OpenClaw path assumptions.
- Tests should load and validate all fixtures through the DSL API if available.
  If the DSL worker has not merged yet, include a narrow fixture-shape smoke
  test and clearly mark the integration point.
- Include a validation fixture or report that maps each example to the kernel
  capability it proves.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
```

Commit with:

```bash
git commit -m "Add example workflow fixtures"
```
