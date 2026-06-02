# Wave 2 Goal: SQLite Ledger And Runner Skeleton

## Goal

Implement the durable SQLite ledger and a minimal runner skeleton for claiming,
leasing, completing, retrying, and recovering stage runs.

## Target Files

Own these files:

- `packages/kernel/agent_workflow_kernel/storage.py`
- `packages/kernel/agent_workflow_kernel/runner.py`
- `tests/test_sqlite_ledger_runner.py`

Avoid editing DSL, prompt, policy, adapter, or example fixtures except for
minimal import exports in `packages/kernel/agent_workflow_kernel/__init__.py`.

## Inputs To Read

- `docs/synthesis/runner-recovery.md`
- `docs/synthesis/domain-model.md`
- `docs/synthesis/wave-1-combined-view.md`
- `packages/kernel/agent_workflow_kernel/contracts.py`

## Acceptance Criteria

- Create SQLite tables for workflow instances, stage runs, receipts, artifacts,
  adapter invocations, events, and child session audit records.
- Provide a repository API that initializes a database, inserts workflow
  instances and stage runs, atomically claims one queued run, renews or expires
  leases, records receipts, and completes or blocks a run.
- Include recovery sweep behavior for stale leases.
- Store append-only events for claim, completion, failure, block, and recovery.
- Keep runner execution adapter-neutral. Do not call OpenClaw, shell commands,
  Telegram, Obsidian, or browser APIs.
- Include tests using a temporary SQLite database.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
```

Commit with:

```bash
git commit -m "Implement SQLite ledger and runner skeleton"
```
