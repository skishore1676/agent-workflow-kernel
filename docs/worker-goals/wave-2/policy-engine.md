# Wave 2 Goal: Policy Engine And Human Gates

## Goal

Implement the generic policy engine for risk classes, hard human gates, action
fingerprints, approval receipts, and enforcement decisions.

## Target Files

Own these files:

- `packages/kernel/agent_workflow_kernel/policy.py`
- `tests/test_policy_engine.py`

Avoid editing storage, runner, prompt, adapter, and DSL modules except for
minimal import exports in `packages/kernel/agent_workflow_kernel/__init__.py`.

## Inputs To Read

- `docs/synthesis/policy-gates.md`
- `docs/synthesis/wave-1-combined-view.md`
- `packages/kernel/agent_workflow_kernel/contracts.py`

## Acceptance Criteria

- Represent global hard gates for public publish, deploy, live trade, auth,
  money, external send, and destructive changes.
- Decide `allow`, `allow_with_receipt`, `require_human`, or `deny`.
- Generate stable action fingerprints from action, target, arguments, artifact
  hashes, and context packet digest.
- Validate that an approval receipt matches the exact action fingerprint and
  has not expired or been revoked.
- Treat unknown or ambiguous side effects as `require_human`.
- Include tests for read-only allow, local draft allow, hard gate requiring
  human, fingerprint mismatch, expired approval, and forbidden action denial.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
```

Commit with:

```bash
git commit -m "Implement policy engine and human gates"
```
