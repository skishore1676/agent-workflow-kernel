"""Adapter service-provider interface for the portable workflow kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from .contracts import (
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    ArtifactRef,
    Receipt,
    StageRun,
    to_plain_data,
)


ADAPTER_STATUS_SUCCEEDED = "succeeded"
ADAPTER_STATUS_FAILED = "failed"
ADAPTER_STATUS_BLOCKED = "blocked"
ADAPTER_STATUS_NEEDS_HUMAN = "needs_human"
ADAPTER_STATUS_TIMED_OUT = "timed_out"
ADAPTER_STATUS_CANCELLED = "cancelled"


@dataclass(slots=True, frozen=True)
class CapabilitySet:
    """Portable declaration of an adapter's supported operations."""

    adapter_id: str
    family: AdapterFamily
    operations: tuple[str, ...]
    features: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def supports(self, operation: str) -> bool:
        return operation in self.operations


@dataclass(slots=True, frozen=True)
class RuntimeRef:
    runtime_id: str
    kind: str
    external_id: str | None = None
    host_ref: str | None = None
    redacted_locator: str | None = None
    status: str = "running"


@dataclass(slots=True, frozen=True)
class SurfaceRef:
    surface_id: str
    kind: str
    external_id: str | None = None
    title: str | None = None
    readback_required: bool = False
    status: str = "published"


@dataclass(slots=True, frozen=True)
class HostDescriptor:
    host_id: str
    host_kind: str
    capability_set: CapabilitySet
    state_root_ref: str | None = None
    scheduler_ref: str | None = None


@dataclass(slots=True, frozen=True)
class LaneDescriptor:
    lane_id: str
    name: str
    capability_set: CapabilitySet
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class AdapterError:
    error_class: str
    message: str
    retryable: bool = False
    suggested_next_action: str | None = None
    evidence_refs: tuple[str, ...] = ()
    partial_outputs: Mapping[str, Any] = field(default_factory=dict)


def ensure_invocation_family(
    invocation: AdapterInvocation,
    expected_family: AdapterFamily,
) -> None:
    """Reject an invocation routed to the wrong adapter family."""

    if invocation.adapter_family != expected_family:
        raise ValueError(
            "adapter invocation family mismatch: "
            f"expected {expected_family.value}, got {invocation.adapter_family.value}"
        )


def make_adapter_receipt(
    invocation: AdapterInvocation,
    *,
    status: str,
    summary: str,
    created_at: str,
    stage_id: str | None = None,
    artifact_refs: tuple[ArtifactRef, ...] = (),
    outputs: Mapping[str, Any] | None = None,
    checks_run: tuple[str, ...] = (),
    policy_snapshot: Mapping[str, Any] | None = None,
    residual_risk: str | None = None,
    next_action: str | None = None,
) -> Receipt:
    """Convert an adapter invocation outcome into the shared receipt contract."""

    plain_outputs = to_plain_data(outputs or {})
    runtime_provenance = {
        "adapter_family": invocation.adapter_family.value,
        "adapter_id": invocation.adapter_id,
        "operation": invocation.operation,
        "invocation_id": invocation.invocation_id,
        "input_ref": invocation.input_ref,
        "idempotency_key": invocation.idempotency_key,
        "checks_run": list(checks_run),
        "outputs": plain_outputs,
    }
    return Receipt(
        receipt_id=f"receipt:{invocation.invocation_id}:{status}",
        kind=f"adapter.{invocation.adapter_family.value}.{invocation.operation}",
        workflow_id=invocation.workflow_id,
        instance_id=invocation.instance_id,
        stage_id=stage_id or invocation.stage_run_id,
        stage_run_id=invocation.stage_run_id,
        status=status,
        summary=summary,
        created_at=created_at,
        artifact_refs=artifact_refs,
        context_packet_ref=invocation.context_packet_ref,
        runtime_provenance=runtime_provenance,
        policy_snapshot=dict(policy_snapshot or {}),
        residual_risk=residual_risk,
        next_action=next_action,
    )


def result_from_receipt(
    invocation: AdapterInvocation,
    receipt: Receipt,
    *,
    outputs: Mapping[str, Any] | None = None,
    artifact_refs: tuple[ArtifactRef, ...] | None = None,
    next_hint: str | None = None,
) -> AdapterResult:
    """Convert a receipt back into the shared adapter result contract."""

    return AdapterResult(
        invocation_id=invocation.invocation_id,
        status=receipt.status,
        outputs=dict(outputs or {}),
        artifact_refs=artifact_refs if artifact_refs is not None else receipt.artifact_refs,
        receipt_ref=receipt.receipt_id,
        residual_risk=receipt.residual_risk,
        next_hint=next_hint or receipt.next_action,
    )


def unsupported_operation_result(
    invocation: AdapterInvocation,
    *,
    created_at: str,
    supported_operations: tuple[str, ...],
) -> AdapterResult:
    error = AdapterError(
        error_class="missing_capability",
        message=(
            f"{invocation.adapter_id} does not support operation "
            f"{invocation.operation!r}"
        ),
        retryable=False,
        suggested_next_action="choose a supported adapter operation",
    )
    outputs = {
        "error": to_plain_data(error),
        "supported_operations": list(supported_operations),
    }
    receipt = make_adapter_receipt(
        invocation,
        status=ADAPTER_STATUS_FAILED,
        summary=error.message,
        created_at=created_at,
        outputs=outputs,
        checks_run=("operation_supported",),
        next_action=error.suggested_next_action,
    )
    return result_from_receipt(invocation, receipt, outputs=outputs)


@runtime_checkable
class RuntimeAdapter(Protocol):
    adapter_id: str
    family: AdapterFamily

    def capabilities(self) -> CapabilitySet: ...

    def invoke(
        self,
        invocation: AdapterInvocation,
        runtime_input: Mapping[str, Any],
    ) -> AdapterResult: ...

    def poll(self, runtime_ref: RuntimeRef | Mapping[str, Any]) -> AdapterResult: ...

    def cancel(
        self,
        runtime_ref: RuntimeRef | Mapping[str, Any],
        reason: str,
    ) -> Receipt: ...

    def collect_proof(
        self,
        runtime_ref: RuntimeRef | Mapping[str, Any],
        proof_request: Mapping[str, Any],
    ) -> Receipt: ...

    def recover(self, idempotency_key: str) -> AdapterResult: ...


@runtime_checkable
class SurfaceAdapter(Protocol):
    adapter_id: str
    family: AdapterFamily

    def capabilities(self) -> CapabilitySet: ...

    def publish(
        self,
        invocation: AdapterInvocation,
        surface_packet: Mapping[str, Any],
    ) -> AdapterResult: ...

    def readback(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt: ...

    def ingest_decisions(
        self,
        surface_query: Mapping[str, Any],
    ) -> list[Receipt]: ...

    def clear(
        self,
        surface_ref: SurfaceRef | Mapping[str, Any],
        reason: str,
    ) -> Receipt: ...

    def validate(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt: ...


@runtime_checkable
class HostAdapter(Protocol):
    adapter_id: str
    family: AdapterFamily

    def capabilities(self) -> CapabilitySet: ...

    def describe(self) -> HostDescriptor: ...

    def resolve(self, ref: str) -> Mapping[str, Any]: ...

    def prepare_state(self, instance_id: str) -> Mapping[str, Any]: ...

    def acquire_lease(self, idempotency_key: str, ttl_seconds: int) -> Receipt: ...

    def release_lease(self, lease_id: str) -> Receipt: ...

    def schedule(self, schedule_request: Mapping[str, Any]) -> Receipt: ...

    def unschedule(self, schedule_ref: str) -> Receipt: ...

    def healthcheck(self, scope: str) -> Receipt: ...


@runtime_checkable
class LaneAdapter(Protocol):
    adapter_id: str
    family: AdapterFamily

    def capabilities(self) -> CapabilitySet: ...

    def describe(self) -> LaneDescriptor: ...

    def open_work(self, domain_input: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def build_stage_input(
        self,
        stage_run: StageRun,
        domain_state: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def validate_artifacts(
        self,
        stage_run: StageRun,
        artifact_refs: tuple[ArtifactRef, ...],
    ) -> Receipt: ...

    def interpret_result(
        self,
        stage_run: StageRun,
        adapter_result: AdapterResult,
    ) -> Mapping[str, Any]: ...

    def prepare_human_gate(
        self,
        stage_run: StageRun,
        gate_request: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def apply_decision(self, decision_receipt: Receipt) -> AdapterResult: ...
