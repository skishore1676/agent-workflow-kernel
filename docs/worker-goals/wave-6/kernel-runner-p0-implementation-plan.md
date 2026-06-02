# Wave 6 Goal: Kernel Runner P0 Implementation Plan

## Goal

Convert the Wave 5 adversarial audit into an implementation-ready plan for the
generic AWK runner/kernel layer. The next wave should graduate AWK from a local
harness skeleton to a kernel beta by making the portable package own workflow
graph lifecycle, policy enforcement, context/provenance, adapter resolution,
validation, and resumable human gates.

This document is a planning artifact only. It does not implement the runner.

## Target Architecture

AWK beta should expose one generic execution path:

```text
WorkflowDef + workflow input + KernelRuntimeConfig
  -> WorkflowKernel.start()
  -> SQLite WorkflowLedger instance + queued first StageRun
  -> WorkflowKernel.run_once()/run_until_idle()
  -> StageExecutionContext
  -> policy preflight
  -> prompt/context rendering
  -> AdapterRegistry.resolve(stage.adapter)
  -> adapter invocation
  -> output/receipt/artifact validation
  -> transition evaluation
  -> next queued stage, waiting_on_human, retry, blocked, or terminal status
```

The kernel owns:

- workflow instance creation and first-stage queueing;
- graph transitions and terminal workflow status updates;
- stage input selector resolution from workflow input, prior artifacts, receipts,
  constants, policy, and context;
- adapter lookup by logical ref and family;
- policy preflight before adapter calls, post-result policy checks before
  transition, and replay/resume checks;
- prompt/context packet rendering and receipt provenance for every executable
  stage;
- validation of required artifacts, output schema refs, receipt shape, guard
  allowlists, and human decision receipts;
- human-gate lifecycle: publish gate packet, record `waiting_on_human`, ingest
  canonical decisions, validate approval fingerprint, and resume;
- recovery classification for expired leases and uncertain side effects.

Adapters own:

- runtime, surface, host, and lane-specific execution;
- host path resolution and capability drift checks;
- surface readback and human decision ingestion;
- domain artifact parsing, domain validation, and domain result interpretation.

## Non-Goals

- Do not mutate OpenClaw, oldmac, Obsidian/Northstar, Telegram, cron,
  credentials, trading, deploy, public publish, or live runtime state.
- Do not replace OpenClaw paths or claim live parity in Wave 6.
- Do not build a no-code workflow builder or scripting language.
- Do not broaden YAML into inline expressions, shell commands, or Python snippets.
- Do not rewrite existing local runner tests until the new kernel path has its
  own tests and compatibility story.
- Do not implement A2A native sessions beyond the generic hooks needed for
  bounded verdict validation and policy checks.

## Proposed Module And File Changes

Primary kernel files:

- `packages/kernel/agent_workflow_kernel/kernel.py`
  - New high-level `WorkflowKernel` orchestration facade.
- `packages/kernel/agent_workflow_kernel/execution.py`
  - Stage execution context, selector resolution, transition decisions, and
    normalized stage outputs.
- `packages/kernel/agent_workflow_kernel/adapter_registry.py`
  - Adapter registration, capability metadata, side-effect declarations, and
    adapter lookup.
- `packages/kernel/agent_workflow_kernel/validation.py`
  - Strengthen DSL validation for selectors, guards, hard policies, retry budgets,
    required artifact roles, and adapter registry references.
- `packages/kernel/agent_workflow_kernel/policy.py`
  - Add stage-policy to `ActionRequest` helpers and policy snapshot builders.
- `packages/kernel/agent_workflow_kernel/storage.py`
  - Add ledger methods for workflow status transition, waiting-on-human stage
    state, decision receipt recording, prior receipt/artifact lookup, and
    resumable stage queueing.
- `packages/kernel/agent_workflow_kernel/runner.py`
  - Keep the current low-level lease runner, but narrow it to claim/lease/result
    mechanics used by `WorkflowKernel`.
- `packages/kernel/agent_workflow_kernel/local_runner.py`
  - Rebase local fake execution onto `WorkflowKernel` after the beta path exists;
    remove fabricated outcome routing from the main claim path.
- `packages/kernel/agent_workflow_kernel/__init__.py`
  - Export new public APIs after implementation.

Tests and fixtures:

- `tests/test_kernel_workflow_execution.py`
- `tests/test_kernel_policy_enforcement.py`
- `tests/test_kernel_human_gate_resume.py`
- `tests/test_kernel_adapter_registry.py`
- `tests/test_kernel_context_provenance.py`
- `tests/test_kernel_validation_guards.py`
- Existing `tests/test_sqlite_ledger_runner.py` remains the low-level lease
  regression suite.

Docs:

- Optional tiny updates to `README.md` and `docs/control.md` after the beta path
  lands, not during worker slices unless needed for acceptance notes.

## Public API Sketches

These sketches define the target shape for workers. Names may move during
implementation, but the contracts should remain stable.

```python
@dataclass(frozen=True, slots=True)
class KernelRuntimeConfig:
    owner_id: str
    adapter_registry: AdapterRegistry
    prompt_registry: PromptRegistry | None = None
    policy_engine: PolicyEngine = field(default_factory=PolicyEngine)
    default_lease_seconds: int = 300
    guard_allowlist: tuple[str, ...] = (
        "within_retry_budget",
        "within_revision_budget",
        "lease_not_expired",
        "adapter_declares_safe_recovery",
        "requires_final_approval",
    )
```

```python
class WorkflowKernel:
    def __init__(
        self,
        ledger: WorkflowLedger,
        workflow: WorkflowDef,
        config: KernelRuntimeConfig,
    ) -> None: ...

    def start(
        self,
        *,
        instance_id: str,
        inputs: Mapping[str, Any],
        idempotency_key: str | None = None,
        now: datetime | str | None = None,
    ) -> WorkflowInstance: ...

    def run_once(self, *, now: datetime | str | None = None) -> KernelStep: ...

    def run_until_idle(
        self,
        *,
        max_steps: int = 50,
        now: datetime | str | None = None,
    ) -> KernelRunSummary: ...

    def ingest_human_decision(
        self,
        decision: HumanApprovalReceipt,
        *,
        now: datetime | str | None = None,
    ) -> KernelDecisionResult: ...
```

```python
@dataclass(frozen=True, slots=True)
class AdapterRegistration:
    adapter_id: str
    family: AdapterFamily
    adapter: RuntimeAdapter | SurfaceAdapter | HostAdapter | LaneAdapter
    operations: tuple[str, ...]
    side_effects: tuple[RiskClass, ...] = (RiskClass.READ_ONLY,)
    replay_safe: bool = False
    requires_idempotency_key: bool = True
    default_timeout_seconds: int | None = None
    proof_capabilities: tuple[str, ...] = ()


class AdapterRegistry:
    def register(self, registration: AdapterRegistration) -> None: ...
    def resolve(self, adapter_ref: str, *, stage_type: StageType) -> AdapterRegistration: ...
    def validate_ref(self, adapter_ref: str) -> None: ...
```

```python
@dataclass(frozen=True, slots=True)
class StageExecutionContext:
    workflow: WorkflowDef
    instance: WorkflowInstance
    stage: StageDef
    run: StageRun
    inputs: Mapping[str, Any]
    prior_receipts: tuple[Receipt, ...]
    artifact_refs: tuple[ArtifactRef, ...]
    rendered_context: RenderedContext | None
    policy_gate: PolicyGate
    adapter_registration: AdapterRegistration
```

```python
@dataclass(frozen=True, slots=True)
class StageExecutionResult:
    outcome: str
    adapter_result: AdapterResult
    receipts: tuple[Receipt, ...]
    artifact_refs: tuple[ArtifactRef, ...]
    output_hash: str
    validation_status: Literal["valid", "invalid", "needs_human", "blocked"]
    failure_class: FailureClass | None = None
    failure_summary: str | None = None
```

## Exact P0 Sequence

### P0.1 Ledger State And Kernel Facade

Implement `WorkflowKernel.start()`, workflow status mutation helpers, and
stage queueing without adapter execution.

Acceptance criteria:

- Starting an instance records canonical input hash, first stage run, and
  `workflow_started`.
- Terminal transition helper updates workflow status and appends events.
- Existing `WorkflowRunner` lease tests remain passing.

Tests:

- `test_kernel_start_queues_first_stage`
- `test_kernel_terminal_transition_updates_instance`
- `test_kernel_start_rejects_duplicate_instance_id`

### P0.2 Adapter Registry And Stage Resolution

Add `AdapterRegistry` and route a claimed stage to one registered adapter family.

Acceptance criteria:

- Registry validates declared adapter refs for all workflow stages.
- Missing adapter refs fail before invocation with a blocked receipt.
- Family mismatch fails closed.
- Local fake adapters can be registered without OpenClaw imports.

Tests:

- `test_registry_resolves_runtime_surface_host_lane_refs`
- `test_missing_adapter_blocks_without_invocation`
- `test_family_mismatch_is_validation_error`

### P0.3 Policy Preflight Before Adapter Invocation

Convert stage policy plus adapter side-effect metadata into an `ActionRequest`
and call `PolicyEngine` before any adapter invocation.

Acceptance criteria:

- Read-only/local draft stages proceed with receipt.
- Hard-risk or ambiguous-side-effect stages become `waiting_on_human` before
  adapter invocation unless a matching approval is present.
- Forbidden actions become `policy_denied` or blocked per workflow transition.
- Policy gate id/fingerprint is stored on invocation/receipt metadata.

Tests:

- `test_public_publish_stage_does_not_invoke_adapter_without_approval`
- `test_unknown_adapter_side_effect_requires_human`
- `test_forbidden_action_denied_before_invocation`
- `test_matching_approval_allows_exact_action`

### P0.4 Context Packet And Receipt Provenance On Main Path

Use `PromptRegistry.resolve`, `render_context_packet`, and receipt provenance
helpers in the generic stage execution path.

Acceptance criteria:

- Runtime and system-action stages produce receipts with `context_packet_ref`,
  prompt provenance when prompt refs exist, rendered input digest, policy
  snapshot, and runtime adapter identity.
- Stages without prompt refs still produce a deterministic structured context
  packet or explicit no-prompt provenance marker.
- Context packet digest participates in action fingerprints for hard gates.

Tests:

- `test_runtime_stage_receipt_includes_prompt_context_digest`
- `test_system_action_receipt_includes_policy_context_without_prompt`
- `test_changed_context_invalidates_prior_approval`

### P0.5 Stage Output Interpretation And Transition Ownership

Move outcome selection out of `_choose_local_outcome` and into adapter result
plus lane interpretation.

Acceptance criteria:

- `WorkflowKernel` picks the next transition using a validated outcome returned
  by `StageExecutionResult`.
- Unknown outcomes block with an invalid-output receipt.
- Guard names are checked against allowlist and implemented guard predicates.
- Retry/revision loops require explicit budgets.

Tests:

- `test_adapter_output_drives_transition`
- `test_unknown_outcome_blocks`
- `test_unallowed_guard_rejected_at_validation`
- `test_retry_loop_without_budget_rejected`

### P0.6 Human Gate Lifecycle And Resume

Represent human gates as first-class waiting states, not blocked stage rows.

Acceptance criteria:

- Human gate stage publishes a surface packet via a registered surface adapter
  when allowed by policy.
- Stage run status becomes `waiting_on_human` or `approval_required`.
- `WorkflowKernel.ingest_human_decision()` validates canonical surface,
  decision, exact action, fingerprint, expiration, and revocation.
- Approved decisions queue the next stage; rejected/revise/park decisions follow
  declared transitions or block with a receipt.

Tests:

- `test_human_gate_waits_without_blocking_stage`
- `test_approved_decision_resumes_to_next_stage`
- `test_vague_approval_does_not_resume_hard_gate`
- `test_surface_disagreement_blocks_resume`
- `test_expired_approval_remains_waiting_or_policy_denied`

### P0.7 Validation Hooks Before Transition

Add a validation layer that runs after adapter output and before state movement.

Acceptance criteria:

- Required artifact roles are present and have hashes.
- Receipt kind/status/stage ids match the claimed stage run.
- Output schema refs are checked by a minimal local validator registry.
- A2A-like verdicts cannot approve hard gates.
- Validation failures preserve produced receipts/artifacts and classify failure.

Tests:

- `test_missing_required_artifact_invalid_output`
- `test_receipt_stage_mismatch_rejected`
- `test_a2a_verdict_cannot_approve_public_publish`
- `test_validation_failure_preserves_receipt_and_blocks`

### P0.8 Kernel Beta CLI Wiring

Add CLI commands that exercise the generic kernel path with local fake adapters.

Acceptance criteria:

- Add `python -m agent_workflow_kernel.cli run-kernel-local <workflow.yaml>`.
- Keep existing `run-local` stable or make it a compatibility wrapper.
- Output summary distinguishes `done`, `waiting_on_human`, `blocked`, and
  `policy_denied`.
- No external surfaces or OpenClaw imports are used.

Tests:

- Subprocess tests for `run-kernel-local` on:
  - Bumblebee to terminal or human gate depending fixture;
  - deterministic system action stopping before `apply_action`;
  - a synthetic unsafe workflow proving adapter was not invoked.

## P1 Sequence After P0

- Strengthen `validate_workflow_mapping()` for selectors, hard policy paths,
  adapter refs, guards, retry budgets, required actor refs, and required artifact
  outputs.
- Add JSON export/import for context packets, receipts, and recovery snapshots.
- Add recovery mode for expired running/validating/waiting-on-child leases with
  adapter readback.
- Add child-session audit models and tests.
- Fix OpenClaw shadow status semantics so missing `expected_host_receipt` cannot
  report takeover-ready `shadow_ready`.
- Split example workflows into portable examples and reference-host OpenClaw
  adoption fixtures.

## Proposed Worker Goal Packets

### Worker 1: Kernel Facade And Ledger Lifecycle

Target files:

- `packages/kernel/agent_workflow_kernel/kernel.py`
- `packages/kernel/agent_workflow_kernel/storage.py`
- `packages/kernel/agent_workflow_kernel/__init__.py`
- `tests/test_kernel_workflow_execution.py`

Goal:

Implement `WorkflowKernel.start()`, instance status updates, first-stage queueing,
terminal transition helpers, and run summaries. Do not invoke adapters yet.

Acceptance:

- Tests prove instance creation, duplicate protection, event writing, terminal
  transitions, and compatibility with existing lease runner tests.

### Worker 2: Adapter Registry And Generic Invocation

Target files:

- `packages/kernel/agent_workflow_kernel/adapter_registry.py`
- `packages/kernel/agent_workflow_kernel/execution.py`
- `packages/kernel/agent_workflow_kernel/local_runner.py`
- `tests/test_kernel_adapter_registry.py`

Goal:

Register local fake adapters and route stages through the correct adapter family
without fabricated outcomes.

Acceptance:

- Tests prove registry resolution, missing adapter failure, family mismatch
  failure, and one successful local fake stage invocation.

### Worker 3: Policy Preflight And Human Waiting State

Target files:

- `packages/kernel/agent_workflow_kernel/policy.py`
- `packages/kernel/agent_workflow_kernel/execution.py`
- `packages/kernel/agent_workflow_kernel/storage.py`
- `tests/test_kernel_policy_enforcement.py`

Goal:

Integrate `PolicyEngine` before adapter invocation and add the first waiting
state for hard gates.

Acceptance:

- Tests prove no adapter call occurs for hard-risk actions without exact
  approval, ambiguous side effects require humans, and forbidden actions deny.

### Worker 4: Prompt Context And Provenance Integration

Target files:

- `packages/kernel/agent_workflow_kernel/execution.py`
- `packages/kernel/agent_workflow_kernel/receipts.py`
- `packages/kernel/agent_workflow_kernel/prompts.py`
- `tests/test_kernel_context_provenance.py`
- optional fixtures under `prompts/`

Goal:

Render context packets on the generic execution path and build receipts carrying
prompt/context/runtime/policy provenance.

Acceptance:

- Tests prove stable digests, prompt hash mismatch blocking, context digest in
  approval fingerprint, and receipts with required provenance fields.

### Worker 5: Human Gate Resume

Target files:

- `packages/kernel/agent_workflow_kernel/kernel.py`
- `packages/kernel/agent_workflow_kernel/storage.py`
- `packages/kernel/agent_workflow_kernel/execution.py`
- `tests/test_kernel_human_gate_resume.py`

Goal:

Implement `ingest_human_decision()` and transition from waiting human gates.

Acceptance:

- Tests prove approval, rejection, revise, expired approval, revoked approval,
  stale fingerprint, and surface disagreement behavior.

### Worker 6: Validation And Guard Enforcement

Target files:

- `packages/kernel/agent_workflow_kernel/validation.py`
- `packages/kernel/agent_workflow_kernel/execution.py`
- `tests/test_kernel_validation_guards.py`

Goal:

Add selector, guard, retry-budget, artifact, receipt, and verdict validation
before transitions.

Acceptance:

- Tests cover invalid selectors, unknown guards, retry loop without budget,
  missing required artifacts, mismatched receipt stage, and A2A hard-gate
  bypass attempts.

### Worker 7: CLI Beta Path And Compatibility Cleanup

Target files:

- `packages/kernel/agent_workflow_kernel/cli.py`
- `packages/kernel/agent_workflow_kernel/local_runner.py`
- `README.md`
- `tests/test_cli_local_execution.py`

Goal:

Expose `run-kernel-local`, keep existing CLI behavior stable, and document the
kernel beta status.

Acceptance:

- Subprocess tests prove the new path runs local fake workflows, stops at human
  gates, and avoids external adapters.

## Migration Risk

- **State semantics risk:** Existing tests treat human gates as blocked stage rows.
  Wave 6 should add new tests first and then update local-runner behavior only
  when the beta path is ready.
- **Policy mapping risk:** DSL policy strings such as `public_publish` and
  `deploy_or_prod_mutation` do not map one-to-one with current `RiskClass`
  values. Add a small mapping table and fail closed on unknown classes.
- **Adapter capability risk:** Current `CapabilitySet` lacks explicit side-effect
  and replay metadata. Prefer additive `AdapterRegistration` metadata over
  changing all protocols at once.
- **Prompt provenance risk:** Some stages have no prompt refs. Use an explicit
  no-prompt context packet rather than skipping provenance.
- **Receipt compatibility risk:** Current receipts are minimal. Add fields
  additively and preserve JSON serialization through `to_plain_data`.
- **Workflow validation risk:** Strengthening validation can break examples. Add
  warnings first where needed, then hard failures for P0 safety gates.
- **OpenClaw status risk:** Shadow reports currently use fixture self-comparison.
  Do not let those statuses define kernel beta readiness.

## Test Plan

Required command for every worker:

```bash
python3 -m unittest discover -s tests
```

If `.venv` exists:

```bash
.venv/bin/python -m pytest
```

Minimum new coverage before kernel beta:

- Start and queue:
  - instance creation, canonical input hash, first stage queued, duplicate
    instance rejection.
- Adapter registry:
  - resolve all adapter families, missing adapter blocked, wrong family rejected,
    side-effect metadata available to policy.
- Policy:
  - hard gates block before invocation, exact approvals allow, stale approvals
    reject, unknown side effects require human, forbidden actions deny.
- Context/provenance:
  - prompt refs resolve to exact hashes, context digests stable, receipts include
    prompt/context/runtime/policy provenance.
- Transition:
  - adapter/lane outcome drives next stage, unknown outcome blocks, terminal
    status updates workflow instance.
- Human gates:
  - gate waits without blocked row, decision ingestion resumes, rejected/revise
    decisions follow transitions, surface disagreement blocks.
- Validation:
  - required artifact missing, receipt mismatch, invalid output schema, A2A hard
    gate bypass, retry loop without budget.
- Recovery:
  - expired pre-start claims requeue; expired post-start uncertain state blocks
    with approval required.
- CLI:
  - local beta run produces deterministic summary and never imports OpenClaw.

## Readiness Gate For Kernel Beta

AWK can graduate from harness skeleton to kernel beta when all of these are true:

- `WorkflowKernel` can start and run a workflow to terminal, blocked,
  waiting-on-human, or policy-denied status without `LocalWorkflowExecutor`
  owning graph transitions.
- Every adapter invocation passes through `AdapterRegistry` and a policy preflight.
- Hard-gated or ambiguous side-effect stages cannot invoke adapters without an
  exact valid approval receipt.
- Receipts from the generic execution path include context packet refs, prompt
  provenance when applicable, runtime adapter identity, policy snapshot, artifact
  refs, and output hash.
- Human gates are resumable from a recorded canonical decision.
- Validation hooks can block invalid outputs before transition without erasing
  produced receipts or artifacts.
- Existing unittest suite and venv pytest pass.
- The kernel package still has no OpenClaw imports or host path assumptions.
- At least three workflows run on the beta local path:
  - Bumblebee or quality-review read-only flow;
  - deterministic system action stopping before a hard gate;
  - Jarvis weekly or another human-gate flow in local fake mode only.

## Verification For This Planning Slice

Run before committing this document:

```bash
python3 -m unittest discover -s tests
```

If `.venv` exists:

```bash
.venv/bin/python -m pytest
```

Commit message:

```bash
git commit -m "Plan AWK kernel runner P0 implementation"
```
