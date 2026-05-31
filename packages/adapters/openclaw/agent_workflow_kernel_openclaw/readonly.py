"""Fixture-only OpenClaw read-only adapter facade."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from agent_workflow_kernel.adapters import (
    ADAPTER_STATUS_BLOCKED,
    ADAPTER_STATUS_SUCCEEDED,
    CapabilitySet,
    make_adapter_receipt,
    result_from_receipt,
    unsupported_operation_result,
)
from agent_workflow_kernel.contracts import (
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    ArtifactRef,
    Receipt,
    to_plain_data,
)
from agent_workflow_kernel.dsl import canonical_json

from .mapping import OpenClawReferenceMapping, mapping_from_fixture


READ_ONLY_OPERATIONS = (
    "inspect_fixture",
    "emit_parity_fixture",
    "map_reference_host",
)
MUTATING_OPERATION_TERMS = (
    "apply",
    "cancel",
    "clear",
    "create",
    "delete",
    "execute",
    "ingest",
    "invoke",
    "mutate",
    "publish",
    "refresh",
    "schedule",
    "send",
    "trade",
    "update",
    "write",
)
DEFAULT_CREATED_AT = "2000-01-01T00:00:00Z"


class OpenClawMutationBlocked(ValueError):
    """Raised when the read-only adapter is asked to perform a mutation."""


@dataclass(slots=True, frozen=True)
class OpenClawReadOnlyInspection:
    """Complete conversion result for a supplied OpenClaw fixture."""

    invocation: AdapterInvocation
    mapping: OpenClawReferenceMapping
    artifact_refs: tuple[ArtifactRef, ...]
    receipt: Receipt
    result: AdapterResult


def _normalized_operation(operation: str) -> str:
    return operation.strip().lower().replace("-", "_").replace(" ", "_")


def guard_read_only_operation(operation: str) -> None:
    """Reject operations that could mutate OpenClaw or operator surfaces."""

    normalized = _normalized_operation(operation)
    if normalized not in READ_ONLY_OPERATIONS:
        for term in MUTATING_OPERATION_TERMS:
            if normalized == term or normalized.startswith(f"{term}_"):
                raise OpenClawMutationBlocked(
                    f"OpenClaw read-only adapter refuses mutating operation {operation!r}"
                )


def _adapter_family(value: Any) -> AdapterFamily:
    if isinstance(value, AdapterFamily):
        return value
    if isinstance(value, str):
        return AdapterFamily(value)
    return AdapterFamily.HOST


def invocation_from_fixture(
    fixture: Mapping[str, Any],
    *,
    default_adapter_id: str = "openclaw.readonly",
) -> AdapterInvocation:
    """Build a kernel adapter invocation envelope from fixture metadata."""

    invocation_data = fixture.get("invocation", {})
    if not isinstance(invocation_data, Mapping):
        raise ValueError("OpenClaw fixture invocation must be a mapping")

    operation = str(invocation_data.get("operation", "inspect_fixture"))
    guard_read_only_operation(operation)
    adapter_id = str(invocation_data.get("adapter_id", default_adapter_id))
    invocation_id = str(
        invocation_data.get(
            "invocation_id",
            f"{adapter_id}:{operation}:{fixture.get('fixture_id', 'fixture')}",
        )
    )
    return AdapterInvocation(
        invocation_id=invocation_id,
        workflow_id=str(invocation_data.get("workflow_id", fixture.get("workflow_id", "openclaw-fixture"))),
        instance_id=str(invocation_data.get("instance_id", fixture.get("instance_id", "openclaw-fixture-instance"))),
        stage_run_id=str(invocation_data.get("stage_run_id", fixture.get("stage_run_id", "openclaw-fixture-stage-run"))),
        adapter_family=_adapter_family(invocation_data.get("adapter_family", AdapterFamily.HOST)),
        adapter_id=adapter_id,
        operation=operation,
        input_ref=invocation_data.get("input_ref", fixture.get("input_ref")),
        context_packet_ref=invocation_data.get("context_packet_ref", fixture.get("context_packet_ref")),
        idempotency_key=invocation_data.get("idempotency_key", fixture.get("fixture_id")),
    )


def _content_hash(item: Mapping[str, Any]) -> str:
    explicit = item.get("content_hash")
    if isinstance(explicit, str) and explicit:
        return explicit
    digest = sha256(canonical_json(dict(item)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def artifact_refs_from_fixture(items: Any) -> tuple[ArtifactRef, ...]:
    """Convert local fixture artifacts into kernel artifact refs."""

    if not items:
        return ()
    refs: list[ArtifactRef] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("artifact entries must be mappings")
        artifact_id = item.get("artifact_id")
        role = item.get("role", item.get("kind"))
        uri = item.get("uri")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("artifact fixture requires non-empty 'artifact_id'")
        if not isinstance(role, str) or not role:
            raise ValueError("artifact fixture requires non-empty 'role' or 'kind'")
        if not isinstance(uri, str) or not uri:
            raise ValueError("artifact fixture requires non-empty 'uri'")
        refs.append(
            ArtifactRef(
                artifact_id=artifact_id,
                role=role,
                uri=uri,
                content_hash=_content_hash(item),
                mime_type=str(item.get("mime_type", "text/plain")),
                size_bytes=item.get("size_bytes"),
                created_by=item.get("created_by"),
                visibility=str(item.get("visibility", "internal")),
            )
        )
    return tuple(refs)


class OpenClawReadOnlyAdapter:
    """Read-only OpenClaw parity adapter backed entirely by supplied fixtures."""

    adapter_id = "openclaw.readonly"
    family = AdapterFamily.HOST
    operations = READ_ONLY_OPERATIONS

    def __init__(self, *, created_at: str = DEFAULT_CREATED_AT) -> None:
        self.created_at = created_at
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("openclaw_compatibility", "fixture_only", "read_only"),
            metadata={"mutation_guard": True},
        )

    def inspect_fixture(self, fixture: Mapping[str, Any]) -> OpenClawReadOnlyInspection:
        """Convert a supplied local fixture into kernel adapter envelopes."""

        invocation = invocation_from_fixture(fixture, default_adapter_id=self.adapter_id)
        guard_read_only_operation(invocation.operation)
        if invocation.operation not in self.operations:
            result = unsupported_operation_result(
                invocation,
                created_at=self.created_at,
                supported_operations=self.operations,
            )
            receipt = make_adapter_receipt(
                invocation,
                status=result.status,
                summary=str(result.outputs.get("error", {}).get("message", "Unsupported operation.")),
                created_at=self.created_at,
                outputs=result.outputs,
                next_action=result.next_hint,
            )
            self.receipts.append(receipt)
            return OpenClawReadOnlyInspection(
                invocation=invocation,
                mapping=mapping_from_fixture(fixture),
                artifact_refs=(),
                receipt=receipt,
                result=result,
            )

        mapping = mapping_from_fixture(fixture)
        artifact_refs = artifact_refs_from_fixture(fixture.get("artifacts"))
        outputs = {
            "fixture_id": fixture.get("fixture_id"),
            "mapping": mapping.to_metadata(),
            "artifact_refs": to_plain_data(artifact_refs),
            "read_only": True,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="OpenClaw read-only fixture inspected without runtime mutation.",
            created_at=str(fixture.get("created_at", self.created_at)),
            artifact_refs=artifact_refs,
            outputs=outputs,
            checks_run=(
                "fixture_supplied",
                "operation_read_only",
                "reference_mapping_built",
            ),
            policy_snapshot={"risk_class": "read_only", "external_effects": False},
            residual_risk=fixture.get("residual_risk"),
            next_action=fixture.get("next_action"),
        )
        self.receipts.append(receipt)
        result = result_from_receipt(
            invocation,
            receipt,
            outputs=outputs,
            artifact_refs=artifact_refs,
            next_hint=fixture.get("next_action"),
        )
        return OpenClawReadOnlyInspection(
            invocation=invocation,
            mapping=mapping,
            artifact_refs=artifact_refs,
            receipt=receipt,
            result=result,
        )

    def blocked_mutation_result(
        self,
        invocation: AdapterInvocation,
        *,
        reason: str | None = None,
    ) -> AdapterResult:
        """Return a structured blocked result for callers that prefer envelopes."""

        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=reason or f"Read-only OpenClaw adapter blocked {invocation.operation}.",
            created_at=self.created_at,
            outputs={
                "blocked": True,
                "operation": invocation.operation,
                "read_only": True,
            },
            checks_run=("operation_read_only",),
            policy_snapshot={"risk_class": "read_only", "external_effects": False},
            residual_risk="Mutation was not attempted.",
            next_action="Use a non-read-only adapter behind an explicit approval gate.",
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=receipt.runtime_provenance["outputs"])
