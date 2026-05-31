"""Deterministic local adapter implementations for tests and fixtures."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from .adapters import (
    ADAPTER_STATUS_CANCELLED,
    ADAPTER_STATUS_SUCCEEDED,
    CapabilitySet,
    HostDescriptor,
    LaneDescriptor,
    RuntimeRef,
    SurfaceRef,
    ensure_invocation_family,
    make_adapter_receipt,
    result_from_receipt,
    unsupported_operation_result,
)
from .contracts import (
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    ArtifactRef,
    Receipt,
    StageRun,
    to_plain_data,
)


DETERMINISTIC_CREATED_AT = "2000-01-01T00:00:00Z"


def _plain_mapping(value: Mapping[str, Any] | RuntimeRef | SurfaceRef) -> dict[str, Any]:
    if isinstance(value, (RuntimeRef, SurfaceRef)):
        return to_plain_data(asdict(value))
    return dict(value)


def _synthetic_invocation(
    *,
    adapter_family: AdapterFamily,
    adapter_id: str,
    operation: str,
    instance_id: str = "local-instance",
    stage_run_id: str = "local-stage-run",
    idempotency_key: str | None = None,
) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"{adapter_id}:{operation}:{idempotency_key or instance_id}",
        workflow_id="local-workflow",
        instance_id=instance_id,
        stage_run_id=stage_run_id,
        adapter_family=adapter_family,
        adapter_id=adapter_id,
        operation=operation,
        idempotency_key=idempotency_key,
    )


class LocalFakeRuntimeAdapter:
    adapter_id = "runtime.local_fake"
    family = AdapterFamily.RUNTIME
    operations = ("invoke", "execute", "poll", "cancel", "collect_proof", "recover")

    def __init__(self, *, created_at: str = DETERMINISTIC_CREATED_AT) -> None:
        self.created_at = created_at
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("deterministic", "local"),
        )

    def invoke(
        self,
        invocation: AdapterInvocation,
        runtime_input: Mapping[str, Any],
    ) -> AdapterResult:
        ensure_invocation_family(invocation, self.family)
        if not self.capabilities().supports(invocation.operation):
            return unsupported_operation_result(
                invocation,
                created_at=self.created_at,
                supported_operations=self.operations,
            )

        runtime_ref = RuntimeRef(
            runtime_id=f"runtime:{invocation.invocation_id}",
            kind="local_fake_runtime",
            external_id=invocation.idempotency_key,
            status="completed",
        )
        outputs = {
            "adapter_id": self.adapter_id,
            "operation": invocation.operation,
            "runtime_input": to_plain_data(dict(runtime_input)),
            "runtime_ref": to_plain_data(asdict(runtime_ref)),
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Local fake runtime completed {invocation.operation}.",
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("operation_supported", "deterministic_runtime"),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def poll(self, runtime_ref: RuntimeRef | Mapping[str, Any]) -> AdapterResult:
        ref = _plain_mapping(runtime_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="poll",
            idempotency_key=ref.get("runtime_id"),
        )
        outputs = {"runtime_ref": ref, "state": ref.get("status", "completed")}
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Local fake runtime poll completed.",
            created_at=self.created_at,
            outputs=outputs,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def cancel(
        self,
        runtime_ref: RuntimeRef | Mapping[str, Any],
        reason: str,
    ) -> Receipt:
        ref = _plain_mapping(runtime_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="cancel",
            idempotency_key=ref.get("runtime_id"),
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_CANCELLED,
            summary=f"Local fake runtime cancelled: {reason}",
            created_at=self.created_at,
            outputs={"runtime_ref": ref, "reason": reason},
        )
        self.receipts.append(receipt)
        return receipt

    def collect_proof(
        self,
        runtime_ref: RuntimeRef | Mapping[str, Any],
        proof_request: Mapping[str, Any],
    ) -> Receipt:
        ref = _plain_mapping(runtime_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="collect_proof",
            idempotency_key=ref.get("runtime_id"),
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Local fake runtime proof collected.",
            created_at=self.created_at,
            outputs={"runtime_ref": ref, "proof_request": dict(proof_request)},
            checks_run=("proof_request_recorded",),
        )
        self.receipts.append(receipt)
        return receipt

    def recover(self, idempotency_key: str) -> AdapterResult:
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="recover",
            idempotency_key=idempotency_key,
        )
        outputs = {"idempotency_key": idempotency_key, "recovered": True}
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Local fake runtime recovery completed.",
            created_at=self.created_at,
            outputs=outputs,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)


class LocalFakeSurfaceAdapter:
    adapter_id = "surface.local_fake"
    family = AdapterFamily.SURFACE
    operations = ("publish", "readback", "ingest_decisions", "clear", "validate")

    def __init__(self, *, created_at: str = DETERMINISTIC_CREATED_AT) -> None:
        self.created_at = created_at
        self.receipts: list[Receipt] = []
        self.published_packets: dict[str, dict[str, Any]] = {}
        self.decisions: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("deterministic", "local", "readback"),
        )

    def publish(
        self,
        invocation: AdapterInvocation,
        surface_packet: Mapping[str, Any],
    ) -> AdapterResult:
        ensure_invocation_family(invocation, self.family)
        if not self.capabilities().supports(invocation.operation):
            return unsupported_operation_result(
                invocation,
                created_at=self.created_at,
                supported_operations=self.operations,
            )

        surface_ref = SurfaceRef(
            surface_id=f"surface:{invocation.invocation_id}",
            kind="local_fake_surface",
            external_id=invocation.idempotency_key,
            title=str(surface_packet.get("title", invocation.operation)),
            readback_required=bool(surface_packet.get("readback_required", False)),
            status="published",
        )
        self.published_packets[surface_ref.surface_id] = dict(surface_packet)
        outputs = {
            "surface_packet": dict(surface_packet),
            "surface_ref": to_plain_data(asdict(surface_ref)),
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Local fake surface published packet.",
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("operation_supported", "packet_recorded"),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def readback(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="readback",
            idempotency_key=ref.get("surface_id"),
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Local fake surface readback completed.",
            created_at=self.created_at,
            outputs={
                "surface_ref": ref,
                "packet": self.published_packets.get(str(ref.get("surface_id")), {}),
            },
            checks_run=("surface_ref_read",),
        )
        self.receipts.append(receipt)
        return receipt

    def ingest_decisions(
        self,
        surface_query: Mapping[str, Any],
    ) -> list[Receipt]:
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="ingest_decisions",
            idempotency_key=str(surface_query.get("query_id", "local-query")),
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Local fake surface ingested decisions.",
            created_at=self.created_at,
            outputs={"surface_query": dict(surface_query), "decision_count": 0},
            checks_run=("query_recorded",),
        )
        self.receipts.append(receipt)
        return [*self.decisions, receipt]

    def clear(
        self,
        surface_ref: SurfaceRef | Mapping[str, Any],
        reason: str,
    ) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="clear",
            idempotency_key=ref.get("surface_id"),
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Local fake surface cleared: {reason}",
            created_at=self.created_at,
            outputs={"surface_ref": ref, "reason": reason},
        )
        self.receipts.append(receipt)
        return receipt

    def validate(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="validate",
            idempotency_key=ref.get("surface_id"),
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Local fake surface validation completed.",
            created_at=self.created_at,
            outputs={"surface_ref": ref, "valid": True},
            checks_run=("surface_ref_present",),
        )
        self.receipts.append(receipt)
        return receipt


class LocalFakeHostAdapter:
    adapter_id = "host.local_fake"
    family = AdapterFamily.HOST
    operations = (
        "describe",
        "resolve",
        "prepare_state",
        "acquire_lease",
        "release_lease",
        "schedule",
        "unschedule",
        "healthcheck",
    )

    def __init__(self, *, created_at: str = DETERMINISTIC_CREATED_AT) -> None:
        self.created_at = created_at
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("deterministic", "local", "in_memory"),
        )

    def describe(self) -> HostDescriptor:
        return HostDescriptor(
            host_id="local-fake-host",
            host_kind="local",
            capability_set=self.capabilities(),
            state_root_ref="state:local-fake",
            scheduler_ref="scheduler:local-fake",
        )

    def resolve(self, ref: str) -> Mapping[str, Any]:
        return {"ref": ref, "resolved_ref": f"resolved:{ref}", "host_id": "local-fake-host"}

    def prepare_state(self, instance_id: str) -> Mapping[str, Any]:
        return {"instance_id": instance_id, "state_ref": f"state:{instance_id}"}

    def acquire_lease(self, idempotency_key: str, ttl_seconds: int) -> Receipt:
        return self._receipt(
            "acquire_lease",
            idempotency_key,
            {"lease_id": f"lease:{idempotency_key}", "ttl_seconds": ttl_seconds},
        )

    def release_lease(self, lease_id: str) -> Receipt:
        return self._receipt("release_lease", lease_id, {"lease_id": lease_id})

    def schedule(self, schedule_request: Mapping[str, Any]) -> Receipt:
        key = str(schedule_request.get("schedule_ref", "local-schedule"))
        return self._receipt("schedule", key, {"schedule_request": dict(schedule_request)})

    def unschedule(self, schedule_ref: str) -> Receipt:
        return self._receipt("unschedule", schedule_ref, {"schedule_ref": schedule_ref})

    def healthcheck(self, scope: str) -> Receipt:
        return self._receipt("healthcheck", scope, {"scope": scope, "healthy": True})

    def _receipt(
        self,
        operation: str,
        key: str,
        outputs: Mapping[str, Any],
    ) -> Receipt:
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation=operation,
            idempotency_key=key,
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Local fake host completed {operation}.",
            created_at=self.created_at,
            outputs=outputs,
        )
        self.receipts.append(receipt)
        return receipt


class LocalFakeLaneAdapter:
    adapter_id = "lane.local_fake"
    family = AdapterFamily.LANE
    operations = (
        "describe",
        "open_work",
        "build_stage_input",
        "validate_artifacts",
        "interpret_result",
        "prepare_human_gate",
        "apply_decision",
    )

    def __init__(self, *, created_at: str = DETERMINISTIC_CREATED_AT) -> None:
        self.created_at = created_at
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("deterministic", "local", "domain_neutral"),
        )

    def describe(self) -> LaneDescriptor:
        return LaneDescriptor(
            lane_id="local-fake-lane",
            name="Local Fake Lane",
            capability_set=self.capabilities(),
        )

    def open_work(self, domain_input: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "workflow_id": "local-fake-workflow",
            "workflow_version": "0.1.0",
            "input": dict(domain_input),
            "idempotency_key": str(domain_input.get("idempotency_key", "local-work")),
        }

    def build_stage_input(
        self,
        stage_run: StageRun,
        domain_state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {
            "stage_run_id": stage_run.stage_run_id,
            "stage_id": stage_run.stage_id,
            "domain_state": dict(domain_state),
        }

    def validate_artifacts(
        self,
        stage_run: StageRun,
        artifact_refs: tuple[ArtifactRef, ...],
    ) -> Receipt:
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="validate_artifacts",
            instance_id=stage_run.instance_id,
            stage_run_id=stage_run.stage_run_id,
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Local fake lane validated artifacts.",
            created_at=self.created_at,
            stage_id=stage_run.stage_id,
            artifact_refs=artifact_refs,
            outputs={"artifact_count": len(artifact_refs)},
            checks_run=("artifact_refs_recorded",),
        )
        self.receipts.append(receipt)
        return receipt

    def interpret_result(
        self,
        stage_run: StageRun,
        adapter_result: AdapterResult,
    ) -> Mapping[str, Any]:
        return {
            "stage_run_id": stage_run.stage_run_id,
            "status": adapter_result.status,
            "outcome": "done" if adapter_result.status == ADAPTER_STATUS_SUCCEEDED else "failed",
            "next_hint": adapter_result.next_hint,
        }

    def prepare_human_gate(
        self,
        stage_run: StageRun,
        gate_request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {
            "stage_run_id": stage_run.stage_run_id,
            "title": gate_request.get("title", "Local fake gate"),
            "allowed_decisions": tuple(gate_request.get("allowed_decisions", ("approve", "reject"))),
            "readback_required": True,
        }

    def apply_decision(self, decision_receipt: Receipt) -> AdapterResult:
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="apply_decision",
            instance_id=decision_receipt.instance_id,
            stage_run_id=decision_receipt.stage_run_id,
        )
        outputs = {
            "decision_receipt_ref": decision_receipt.receipt_id,
            "decision_status": decision_receipt.status,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Local fake lane applied decision.",
            created_at=self.created_at,
            stage_id=decision_receipt.stage_id,
            outputs=outputs,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)
