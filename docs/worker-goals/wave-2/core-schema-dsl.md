# Wave 2 Goal: Core Schema And DSL

## Goal

Implement the core schema and workflow DSL loader/compiler for the Agent
Workflow Kernel.

## Target Files

Own these files:

- `packages/kernel/agent_workflow_kernel/contracts.py`
- `packages/kernel/agent_workflow_kernel/dsl.py`
- `packages/kernel/agent_workflow_kernel/validation.py`
- `tests/test_core_schema_dsl.py`

Avoid editing storage, runner, policy, prompt, or adapter modules except for
minimal import exports in `packages/kernel/agent_workflow_kernel/__init__.py`.

## Inputs To Read

- `docs/synthesis/domain-model.md`
- `docs/synthesis/workflow-dsl.md`
- `docs/synthesis/wave-1-combined-view.md`
- `tests/test_contracts.py`

## Acceptance Criteria

- Load YAML workflow definitions into a normalized `WorkflowDef`.
- Compile definitions into deterministic canonical JSON bytes or string.
- Validate required top-level sections, unique stage ids, known stage types,
  declared outcomes, transition targets, and terminal states.
- Preserve adapter ids as logical references without resolving OpenClaw paths.
- Include tests for valid workflows, unknown stage type, duplicate stage id,
  missing transition target, and deterministic canonicalization.
- Keep the schema generic enough to express the five Wave 1 example workflows.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
```

Commit with:

```bash
git commit -m "Implement core schema and workflow DSL"
```
