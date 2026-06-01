# Wave 10 Policy / Adversarial Audit

Worker: Wave 10 policy/adversarial audit  
Branch: `codex/wave10-policy-adversarial-audit`  
Scope: local repo only; no live OpenClaw, oldmac, Telegram, Obsidian/Northstar,
auth, deploy, trading, public publish, or external sends.

## Summary

AWK is closer to a safe live-readonly/shadow posture after this pass, but it
should not be treated as ready for mutation-capable live workflow testing until
adapter capability declarations are made fail-closed by construction.

Two bounded safety bugs were fixed in this branch:

- human-gate surface lifecycle methods now policy-check surface adapter side
  effects before invoking `publish`, `readback`, or `ingest_decisions`;
- action fingerprints now bind risk classes and hard-gate classification, so a
  historical read-only approval cannot be replayed as mutation permission after
  the action is reclassified.

## Findings

### High - fixed: human-gate surface lifecycle could invoke an external surface before policy preflight

The policy doc requires policy evaluation before every side-effecting adapter
invocation and before writing to a human-visible surface when the write is an
external send (`docs/synthesis/policy-gates.md:138-150`). Before this fix, the
human-gate surface lifecycle resolved the surface adapter and invoked lifecycle
operations directly. A Telegram/Obsidian/public-send surface registered with
`RiskClass.EXTERNAL_EFFECT` could therefore be called by
`publish_waiting_human_gate()` before policy had a chance to stop it.

Fix: `WorkflowKernel` now calls `_require_surface_policy_allows()` before
surface `publish`, `readback`, and `ingest_decisions`
(`packages/kernel/agent_workflow_kernel/kernel.py:423`,
`packages/kernel/agent_workflow_kernel/kernel.py:520`,
`packages/kernel/agent_workflow_kernel/kernel.py:613`,
`packages/kernel/agent_workflow_kernel/kernel.py:1054`). The regression proves
an external-effect surface adapter is blocked before any receipt/event from the
surface adapter is produced
(`tests/test_workflow_kernel_run_once.py:675-704`).

Impact before fix: a future live Telegram/send or operator-surface mutation
adapter could accidentally be treated as just another gate-publish surface.

### High - fixed: approval fingerprints did not stale when risk class changed

The policy doc says approval becomes stale when the risk class changes
(`docs/synthesis/policy-gates.md:176-177`). The original fingerprint was based
on action, target, arguments, artifact hashes, and context digest only. That
left an adversarial replay shape: a historical read-only approval for the same
action/target/arguments could match after the action was reclassified as
`external_effect`, `production_effect`, `financial_effect`, `auth_effect`, or
`destructive_effect`.

Fix: `action_fingerprint()` now includes sorted `risk_classes` and `hard_gates`,
and `fingerprint_request()` passes them through
(`packages/kernel/agent_workflow_kernel/policy.py:106-140`). The regression
shows a read-only packet approval cannot authorize the same packet once it
becomes a public-publish hard gate (`tests/test_policy_engine.py:180-208`).

Impact before fix: historical publish artifacts or review approvals could be
over-trusted if the workflow/adapters changed the side-effect classification.

### Medium - open: adapter side-effect defaults are still fail-open for runtime adapters

`AdapterRegistration.from_runtime_adapter()` defaults `side_effects` to
`RiskClass.READ_ONLY` (`packages/kernel/agent_workflow_kernel/adapter_registry.py:32-45`).
The policy model intentionally relies on adapters declaring their side effects
(`docs/synthesis/policy-gates.md:148-150`), but the default means a new runtime
adapter can be unsafe by omission: if a mutation-capable adapter is registered
without an explicit risk class, the kernel will treat it as read-only and allow
the invocation with receipt.

Recommendation: before live mutation testing, require every non-test adapter
registration to declare side effects explicitly, or add a capability-manifest
validator that blocks default read-only registration outside local fakes and
known read-only adapters. This should cover broker, auth, deploy, Telegram/send,
Obsidian/Northstar write, public publish, and destructive filesystem adapters.

### Medium - open: approval is not yet a scoped capability carried into the approved mutation stage

Human gate ingestion validates the waiting gate receipt and queues the next
stage, while the later adapter invocation separately evaluates policy. This is
safe in the current implementation because hard-risk adapter invocations still
block without a fresh approval, but it is not yet an end-to-end live-apply
authorization model. In other words, a human gate can say "approved" and queue
`apply`, but the approved action/fingerprint is not carried as a scoped
capability to the exact next adapter invocation.

Recommendation: before allowing any deploy/trade/auth/destructive/external-send
apply stage, require the queued mutation stage to consume a specific approval
receipt whose fingerprint includes the mutation target, arguments, artifacts,
context digest, risk classes, and hard gates. The stage should block if it
cannot prove that binding.

### Low - no issue found: OpenClaw lane adapters keep readback and publish packet evidence separate from permission

The Ivy/Jonah adapter blocks shadow readiness when a publish packet says
`external_publish_performed=true`, requires `public_publish_blocked`, and keeps
public publish out of the shadow path
(`packages/adapters/openclaw/agent_workflow_kernel_openclaw/ivy_lane.py:325-339`,
`packages/adapters/openclaw/agent_workflow_kernel_openclaw/ivy_lane.py:480-515`).

The weekly update adapter maps Blackboard checked/read state as readback
evidence and only creates a human gate when unchecked
(`packages/adapters/openclaw/agent_workflow_kernel_openclaw/weekly_update.py:235-313`).
The two-lane onboarding packet also records that `read_clear` is not mutation
permission (`scripts/openclaw_two_lane_onboarding.py:401-417`).

I did not find evidence that these lane adapters treat live-readonly evidence,
checked boxes, or prompt wording as permission to mutate OpenClaw, Telegram,
Obsidian/Northstar, or public-publish surfaces.

## Verification

- `python3 -m unittest tests.test_policy_engine tests.test_workflow_kernel_run_once` passed.
- `python3 -m unittest discover -s tests` passed: 114 tests.
- `./scripts/check.sh` passed: 114 unittest tests; venv pytest skipped because
  `.venv/bin/python` is missing.
