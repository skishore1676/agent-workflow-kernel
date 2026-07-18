# Kernel Public API Surface

The public API of the kernel is exactly `agent_workflow_kernel.__all__`
(146 names, frozen by `tests/test_kernel_public_api.py`). Anything not listed
here is internal and may change without notice. Submodules prefixed `_`
(`_internal_types`, `_helpers`) and any `local_adapters/_shared` are private.

Import everything from the top-level package, never from submodules:
```python
from agent_workflow_kernel import WorkflowKernel, WorkflowLedger, load_workflow_file
```

## Lifecycle (the core consumer API)
- `WorkflowKernel` + `KernelRuntimeConfig` — construct and drive one instance.
  - `start(...)` create an instance from a `WorkflowDef`.
  - `run_once(...)` advance one tick (idempotent, lease-guarded).
  - `ingest_human_decision(...)` apply a direct human decision.
  - `publish_waiting_human_gate(...)` / `readback_human_gate_surface(...)` /
    `ingest_human_gate_surface_decision(...)` the surface-routed gate cycle.
  - Result types: `KernelStep`, `KernelDecisionResult`, `HumanGateSurfaceResult`.
- `WorkflowRunner` (+ `RunnerResult`, `RunnerStep`, `OwnedRunSummary`) — the
  adapter-neutral owned-execution loop.
- `run_local_workflow` / `LocalWorkflowExecutor` / `LocalRunSummary` — local E2E.

## Durable state
- `WorkflowLedger`, `LedgerConflict`, `RecoveryAction`.

## Workflow definition / DSL
- `WorkflowDef`, `StageDef`, `Transition`, `WorkflowInstance`, `StageRun`,
  `StageType`, `StageRunStatus`, `WorkflowStatus`, `FailureClass`, `AdapterFamily`.
- `load_workflow_file`, `load_workflow_yaml`, `workflow_from_mapping`,
  `workflow_to_canonical_json[_bytes]`, `canonical_json`, `to_plain_data`.
- `validate_workflow_def`, `validate_workflow_mapping`, `WorkflowValidationError`.

## Policy & gates
- `PolicyEngine`, `PolicyGate`, `GateDecision`, `HardGate`, `RiskClass`,
  `ActionRequest`, `ApprovalDecision`, `ApprovalValidation`, `HumanApprovalReceipt`.
- Guards/constants: `ALLOWED_TRANSITION_GUARDS`, `FAIL_CLOSED_TRANSITION_GUARDS`,
  `IMPLEMENTED_TRANSITION_GUARDS`.
- `validate_approval`, `action_fingerprint`, `fingerprint_request`,
  `build_test_only_suman_approval`.

## Adapters & registry (the extension points)
- Protocols: `RuntimeAdapter`, `SurfaceAdapter`, `HostAdapter`, `LaneAdapter`.
- `AdapterRegistry`, `AdapterRegistration`, `AdapterRegistryError`,
  `AdapterResult`, `AdapterInvocation`, `AdapterError`, `CapabilitySet`,
  `RuntimeRef`, `SurfaceRef`, `HostDescriptor`, `LaneDescriptor`,
  `SurfaceCapabilityContract`, `SurfaceBinding`, `ResolvedSurfaceBinding`.
- Local/dev adapters (`local_adapters/` package): `LocalFake*Adapter`,
  `DryRun*SurfaceAdapter`, `Sandbox*SurfaceAdapter`,
  `LiveObsidianMarkdownSurfaceAdapter`, `LocalMarkdownHumanReviewSurfaceAdapter`.
  *Note (lane-host):* these dev/sandbox/live surfaces are candidates to move to a
  host/dev layer rather than travel with the vendored kernel core.

## Prompts, receipts, sessions, surfaces, parity
- Prompts: `PromptRegistry`, `PromptRecord`, `PromptRef`, `PromptBundle`,
  `ResolvedPrompt`, `RenderedContext`, `ContextPacket`, `render_context_packet`,
  related errors.
- Receipts/provenance: `Receipt`, `build_receipt`, `make_adapter_receipt`,
  `build_prompt_provenance`, `build_runtime_provenance`, `build_policy_snapshot`,
  `receipt_digest`, `digest_data`, `hash_text`.
- Sessions: `canonical_actor_session_key`, `canonical_actor_session_binding`,
  `ActorSessionBinding`, `ActorSessionScope`, `SessionBudget`, related constants.
- Surface profiles: `SurfaceProfile`, `load_surface_profile`,
  `surface_profile_from_mapping`, `SurfaceProfileError`.
- Parity: `ParityReport`, `ParityField`, `report_from_fixture`,
  `load_parity_fixture`, `compare_receipts`.
- Leases: `resolve_stage_lease_policy`, `resolved_lease_policy_from_stage_run`,
  `ResolvedLeasePolicy`.
- Automated reviewer: `AutomatedSumanReviewer`, `AutomatedSumanReviewResult`.

## Purity invariant (enforced)
`tools/import_lint.py` (wired into `make check`) guarantees the kernel package
imports nothing from any `agent_workflow_kernel_*` provider/adapter
distribution. This is the agnosticism contract the lane-host program depends on.
