# Wave 18 Policy Budget Guards

## Summary

Implemented runner-owned transition guard evaluation for:

- `within_retry_budget`
- `within_revision_budget`
- `within_resume_budget`
- `within_research_iteration_budget`

These guards no longer use the reserved fail-closed stub path. Unknown guards
and guards with missing or malformed budget config still fail closed.

## Semantics

Budget lookup is intentionally conservative. The guard searches the current
stage, target stage, workflow defaults, and workflow policies for the relevant
budget key. If the first matching budget value is missing, non-integer,
negative, or otherwise malformed, the transition blocks.

`within_retry_budget` uses total target-stage attempts. For a retry self-loop,
`retry.max_attempts: 2` allows attempt 1 to queue attempt 2 and blocks a third
attempt.

`within_revision_budget`, `within_resume_budget`, and
`within_research_iteration_budget` count budget-consuming transitions. The
kernel records budget metadata on successful guarded transitions and uses that
ledger history on later checks. If older ledger history lacks that metadata, it
falls back to stage-run counts.

Revision and resume return edges do not double-spend the budget. For example,
`needs_revision -> revise` consumes one revision turn, while
`revised -> review` is allowed to return to review as long as the already-used
budget is not over the configured ceiling.

## Verification

Focused verification:

```text
python3 -m unittest tests.test_workflow_kernel_run_once
Ran 38 tests in 0.348s
OK
```

Required verification:

```text
python3 -m unittest discover -s tests
Ran 190 tests in 3.286s
OK
```

```text
./scripts/check.sh
Ran 190 tests in 3.691s
OK
Skipping venv pytest: .venv/bin/python is missing. Run ./scripts/dev_setup.sh first.
```
