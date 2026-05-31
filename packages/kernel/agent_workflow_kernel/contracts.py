"""Shared domain contracts for Wave 2 implementation work.

These are intentionally small. Worker branches can build loaders, storage,
policy, prompt rendering, and adapters around these stable names without
fighting over package shape.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class StageType(StrEnum):
    AGENT_WORK = "agent_work"
    AGENT_GATE = "agent_gate"
    A2A_REVIEW_LOOP = "a2a_review_loop"
    HUMAN_GATE = "human_gate"
    SYSTEM_ACTION = "system_action"
    WAIT_SCHEDULE = "wait_schedule"
    RECOVERY = "recovery"
    BLOCKED = "blocked"


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_ON_AGENT = "waiting_on_agent"
    WAITING_ON_HUMAN = "waiting_on_human"
    WAITING_ON_SCHEDULE = "waiting_on_schedule"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    POLICY_DENIED = "policy_denied"
    FINAL_APPROVAL_REQUIRED = "final_approval_required"
    DONE = "done"
    CANCELLED = "cancelled"


class StageRunStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    STARTED = "started"
    WAITING = "waiting"
    WAITING_ON_CHILD = "waiting_on_child"
    WAITING_ON_HUMAN = "waiting_on_human"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INVALID_OUTPUT = "invalid_output"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_DENIED = "approval_denied"
    SUPERSEDED = "superseded"


class FailureClass(StrEnum):
    RUNTIME_FAILURE = "runtime_failure"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    INVALID_OUTPUT = "invalid_output"
    DETERMINISTIC_TEST_FAILURE = "deterministic_test_failure"
    HUMAN_REJECTION = "human_rejection"
    POLICY_DENIAL = "policy_denial"
    STALE_LEASE = "stale_lease"
    MISSING_DEPENDENCY = "missing_dependency"
    DOMAIN_BLOCKED = "domain_blocked"
    UNKNOWN_SIDE_EFFECT_STATE = "unknown_side_effect_state"


class RiskClass(StrEnum):
    READ_ONLY = "read_only"
    LOCAL_DRAFT = "local_draft"
    REVIEW_ONLY = "review_only"
    INTERNAL_STATE = "internal_state"
    EXTERNAL_EFFECT = "external_effect"
    PRODUCTION_EFFECT = "production_effect"
    FINANCIAL_EFFECT = "financial_effect"
    AUTH_EFFECT = "auth_effect"
    DESTRUCTIVE_EFFECT = "destructive_effect"
    FORBIDDEN = "forbidden"


class AdapterFamily(StrEnum):
    RUNTIME = "runtime"
    SURFACE = "surface"
    HOST = "host"
    LANE = "lane"


@dataclass(slots=True, frozen=True)
class PromptRef:
    id: str
    kind: str
    version: str
    registry: str = "local"
    render_mode: str = "markdown"
    required: bool = True
    content_hash: str | None = None


@dataclass(slots=True, frozen=True)
class ArtifactRef:
    artifact_id: str
    role: str
    uri: str
    content_hash: str
    mime_type: str = "text/plain"
    size_bytes: int | None = None
    created_by: str | None = None
    visibility: str = "internal"


@dataclass(slots=True, frozen=True)
class ContextPacket:
    schema_version: str
    context_id: str
    workflow_id: str
    instance_id: str
    stage_id: str
    stage_run_id: str
    input_digest: str
    rendered_digest: str
    prompt_refs: tuple[PromptRef, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class Receipt:
    receipt_id: str
    kind: str
    workflow_id: str
    instance_id: str
    stage_id: str
    stage_run_id: str
    status: str
    summary: str
    created_at: str
    artifact_refs: tuple[ArtifactRef, ...] = ()
    context_packet_ref: str | None = None
    prompt_provenance: dict[str, Any] = field(default_factory=dict)
    runtime_provenance: dict[str, Any] = field(default_factory=dict)
    policy_snapshot: dict[str, Any] = field(default_factory=dict)
    residual_risk: str | None = None
    next_action: str | None = None


@dataclass(slots=True, frozen=True)
class Transition:
    from_stage: str
    on: str
    to_stage: str | None = None
    terminal: str | None = None
    guard: str | None = None
    label: str | None = None


@dataclass(slots=True, frozen=True)
class StageDef:
    id: str
    type: StageType
    adapter: str
    outcomes: tuple[str, ...]
    actors: dict[str, str] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    prompt_refs: tuple[PromptRef, ...] = ()
    policy: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    retry: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    surface: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WorkflowDef:
    id: str
    version: str
    name: str
    stages: tuple[StageDef, ...]
    transitions: tuple[Transition, ...]
    schema: str = "workflow.kernel.v1"
    owner: str | None = None
    description: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    actors: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    policies: dict[str, Any] = field(default_factory=dict)
    compatibility: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowInstance:
    instance_id: str
    workflow_def_id: str
    workflow_version: str
    status: WorkflowStatus
    input_hash: str
    current_stage_id: str | None = None
    idempotency_key: str | None = None
    recovery_epoch: int = 0


@dataclass(slots=True)
class StageRun:
    stage_run_id: str
    instance_id: str
    stage_id: str
    status: StageRunStatus
    attempt: int = 1
    adapter_id: str | None = None
    actor_ref: str | None = None
    lease_token: str | None = None
    receipt_id: str | None = None
    failure_class: FailureClass | None = None
    retry_after_at: str | None = None


@dataclass(slots=True, frozen=True)
class PolicyGate:
    gate_id: str
    workflow_id: str
    instance_id: str
    stage_id: str
    requested_action: str
    action_fingerprint: str
    risk_classes: tuple[RiskClass, ...]
    decision: str
    evidence_refs: tuple[str, ...] = ()
    approval_receipt_ref: str | None = None
    decision_reason: str | None = None


@dataclass(slots=True, frozen=True)
class AdapterInvocation:
    invocation_id: str
    workflow_id: str
    instance_id: str
    stage_run_id: str
    adapter_family: AdapterFamily
    adapter_id: str
    operation: str
    input_ref: str | None = None
    context_packet_ref: str | None = None
    idempotency_key: str | None = None


@dataclass(slots=True, frozen=True)
class AdapterResult:
    invocation_id: str
    status: str
    outputs: dict[str, Any] = field(default_factory=dict)
    artifact_refs: tuple[ArtifactRef, ...] = ()
    receipt_ref: str | None = None
    residual_risk: str | None = None
    next_hint: str | None = None


def to_plain_data(value: Any) -> Any:
    """Convert dataclasses and enums into JSON-serializable Python values."""

    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return to_plain_data(asdict(value))
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain_data(item) for item in value]
    return value
