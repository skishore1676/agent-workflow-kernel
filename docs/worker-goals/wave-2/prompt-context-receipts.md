# Wave 2 Goal: Prompt Registry, Context Packets, And Receipts

## Goal

Implement prompt registry loading, context packet rendering, content hashing,
and receipt provenance helpers.

## Target Files

Own these files:

- `packages/kernel/agent_workflow_kernel/prompts.py`
- `packages/kernel/agent_workflow_kernel/receipts.py`
- `tests/test_prompt_context_receipts.py`
- sample prompt files under `prompts/`

Avoid editing storage, runner, policy, adapter, and DSL modules except for
minimal import exports in `packages/kernel/agent_workflow_kernel/__init__.py`.

## Inputs To Read

- `docs/synthesis/prompt-registry.md`
- `docs/synthesis/wave-1-combined-view.md`
- `packages/kernel/agent_workflow_kernel/contracts.py`

## Acceptance Criteria

- Define a prompt registry layout and loader for local prompt files.
- Resolve exact `PromptRef` entries with content hashes.
- Render a deterministic context packet from prompt refs, workflow state,
  artifacts, receipts, approvals, and variables.
- Compute context packet digest and rendered input digest.
- Build receipt provenance fields for prompt ids, versions, content hashes,
  model/runtime metadata, and tool permissions.
- Include tests for prompt resolution, missing required prompt, deterministic
  digests, and receipt provenance.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
```

Commit with:

```bash
git commit -m "Implement prompt context and receipt provenance"
```
