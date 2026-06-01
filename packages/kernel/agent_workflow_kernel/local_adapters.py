"""Deterministic local adapter implementations for tests and fixtures."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .adapters import (
    ADAPTER_STATUS_BLOCKED,
    ADAPTER_STATUS_CANCELLED,
    ADAPTER_STATUS_SUCCEEDED,
    CapabilitySet,
    HostDescriptor,
    LaneDescriptor,
    RuntimeRef,
    SurfaceCapabilityContract,
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
LOCAL_HUMAN_REVIEW_CARD_SCHEMA = "local_human_review_card.v1"
LOCAL_HUMAN_REVIEW_DECISION_SCHEMA = "local_human_review_decision.v1"
DRY_RUN_SURFACE_PACKET_SCHEMA = "dry_run_surface_packet.v1"
DRY_RUN_SURFACE_DECISION_SCHEMA = "dry_run_surface_decision.v1"
OBSIDIAN_SANDBOX_NOTE_SCHEMA = "obsidian_sandbox_note.v1"
TELEGRAM_SANDBOX_MESSAGE_SCHEMA = "telegram_sandbox_message.v1"
SANDBOX_SURFACE_DECISION_SCHEMA = "sandbox_surface_decision.v1"
LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA = "live_obsidian_markdown_note.v1"
LIVE_OPERATOR_SURFACE_DECISION_SCHEMA = "live_operator_surface_decision.v1"

_CHECKBOX_RE = re.compile(
    r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+`?(?P<label>[^`\n]+?)`?\s*$"
)
_FINGERPRINT_RE = re.compile(r"Action fingerprint:\s*`(?P<value>[^`]+)`")
_FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---", re.DOTALL)


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


class DryRunSurfaceAdapter:
    """Deterministic non-live adapter skeleton for human-visible surfaces."""

    adapter_id = "surface.dry_run"
    family = AdapterFamily.SURFACE
    operations = ("publish", "readback", "ingest_decisions", "clear", "validate")
    surface_kind = "generic_surface"
    target_surface = "generic"
    features = ("deterministic", "dry_run", "readback", "decision_ingest", "non_live_only")
    external_effects: tuple[str, ...] = ()

    def __init__(self, *, created_at: str = DETERMINISTIC_CREATED_AT) -> None:
        self.created_at = created_at
        self.receipts: list[Receipt] = []
        self.published_packets: dict[str, dict[str, Any]] = {}

    def surface_contract(self) -> SurfaceCapabilityContract:
        return SurfaceCapabilityContract(
            surface_kind=self.surface_kind,
            mode="dry_run",
            live_mutation_allowed=False,
            dry_run_only=True,
            readback_required=True,
            decision_ingest_supported=True,
            clear_requires_live_mutation=True,
            external_effects=self.external_effects,
            receipt_schema=DRY_RUN_SURFACE_PACKET_SCHEMA,
            metadata={"target_surface": self.target_surface},
        )

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=self.features,
            metadata={
                "schema": DRY_RUN_SURFACE_PACKET_SCHEMA,
                "non_live_only": True,
                "surface_contract": self.surface_contract().as_metadata(),
            },
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

        packet = dict(surface_packet)
        safety_error = _non_live_safety_error(packet, require_test_only=True)
        title = str(packet.get("title") or invocation.operation)
        if safety_error is not None:
            outputs = self._blocked_outputs(
                error=safety_error,
                surface_packet=packet,
                surface_ref=None,
            )
            receipt = make_adapter_receipt(
                invocation,
                status=ADAPTER_STATUS_BLOCKED,
                summary=safety_error["message"],
                created_at=self.created_at,
                outputs=outputs,
                checks_run=("operation_supported", "non_live_surface_guard"),
                residual_risk=safety_error["message"],
                next_action="retry with test_only and non_live true, or use an explicitly live adapter after approval",
            )
            self.receipts.append(receipt)
            return result_from_receipt(invocation, receipt, outputs=outputs)

        surface_ref = SurfaceRef(
            surface_id=f"surface:{invocation.invocation_id}",
            kind=self.surface_kind,
            external_id=f"dry-run:{invocation.idempotency_key or invocation.invocation_id}",
            title=title,
            readback_required=True,
            status="dry_run_planned",
        )
        self.published_packets[surface_ref.surface_id] = packet
        outputs = self._base_outputs(
            surface_ref=surface_ref,
            surface_packet=packet,
            extra={
                "would_publish": True,
                "readback_available": True,
                "decision_ingest_available": True,
            },
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Dry-run {self.target_surface} surface publish planned without live mutation.",
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("operation_supported", "non_live_surface_guard", "packet_contract_recorded"),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def readback(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="readback",
            idempotency_key=str(ref.get("surface_id") or "dry-run-surface"),
        )
        packet = self.published_packets.get(str(ref.get("surface_id")))
        exists = packet is not None
        outputs = {
            "schema": DRY_RUN_SURFACE_PACKET_SCHEMA,
            "surface_kind": self.surface_kind,
            "target_surface": self.target_surface,
            "dry_run": True,
            "non_live": True,
            "live_mutation_performed": False,
            "surface_ref": ref,
            "packet": packet or {},
            "readback_confirmed": exists,
        }
        status = ADAPTER_STATUS_SUCCEEDED if exists else ADAPTER_STATUS_BLOCKED
        summary = (
            f"Dry-run {self.target_surface} surface readback confirmed."
            if exists
            else f"Dry-run {self.target_surface} surface readback blocked because no planned packet was found."
        )
        receipt = make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("surface_ref_read", "dry_run_packet_lookup"),
            residual_risk=None if exists else summary,
            next_action=None if exists else "publish a dry-run packet before readback",
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
            idempotency_key=str(surface_query.get("query_id", "dry-run-decision")),
        )
        live_error = _non_live_safety_error(surface_query, require_test_only=True)
        if bool(surface_query.get("read_live_surface", False)):
            live_error = _live_mutation_error("live surface decision read is not allowed in this dry-run adapter")

        decision = str(
            surface_query.get("decision") or surface_query.get("synthetic_decision") or ""
        ).strip()
        allowed_decisions = _string_tuple(surface_query.get("allowed_decisions", ()))
        error: dict[str, Any] | None = live_error
        if error is None and not decision:
            error = {
                "error_class": "missing_synthetic_decision",
                "message": "Dry-run surface decision ingest requires a supplied synthetic decision.",
            }
        if error is None and allowed_decisions and decision not in allowed_decisions:
            error = {
                "error_class": "decision_not_allowed",
                "message": "Dry-run surface decision ingest blocked because the supplied decision is not allowed.",
                "allowed_decisions": list(allowed_decisions),
            }

        status = ADAPTER_STATUS_BLOCKED if error is not None else ADAPTER_STATUS_SUCCEEDED
        summary = (
            str(error["message"])
            if error is not None
            else f"Dry-run {self.target_surface} surface decision ingested: {decision}."
        )
        outputs = {
            "schema": DRY_RUN_SURFACE_DECISION_SCHEMA,
            "surface_kind": self.surface_kind,
            "target_surface": self.target_surface,
            "canonical_surface": self.adapter_id,
            "gate_id": str(surface_query.get("gate_id") or ""),
            "human_ref": str(surface_query.get("human_ref") or "dry-run-human"),
            "decision": decision or None,
            "requested_action": str(surface_query.get("requested_action") or ""),
            "exact_action_approved": str(surface_query.get("exact_action") or ""),
            "action_fingerprint": str(
                surface_query.get("expected_action_fingerprint")
                or surface_query.get("action_fingerprint")
                or ""
            ),
            "evidence_refs": list(_string_tuple(surface_query.get("evidence_refs", ()))),
            "allowed_decisions": list(allowed_decisions),
            "test_only": bool(surface_query.get("test_only", True)),
            "non_live": bool(surface_query.get("non_live", True)),
            "dry_run": True,
            "live_mutation_performed": False,
        }
        if error is not None:
            outputs["error"] = error
        receipt = make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("non_live_surface_guard", "synthetic_decision_contract"),
            residual_risk=None if status == ADAPTER_STATUS_SUCCEEDED else summary,
            next_action=None if status == ADAPTER_STATUS_SUCCEEDED else "provide exactly one allowed synthetic decision",
        )
        self.receipts.append(receipt)
        return [receipt]

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
            idempotency_key=str(ref.get("surface_id") or "dry-run-surface"),
        )
        error = _live_mutation_error(
            f"Clearing {self.target_surface} would require a live surface mutation."
        )
        outputs = self._blocked_outputs(error=error, surface_packet={"reason": reason}, surface_ref=ref)
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=error["message"],
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("operation_supported", "live_mutation_refused"),
            residual_risk=error["message"],
            next_action="use an explicitly live adapter only after approval",
        )
        self.receipts.append(receipt)
        return receipt

    def validate(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        readback = self.readback(ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="validate",
            idempotency_key=str(ref.get("surface_id") or "dry-run-surface"),
        )
        valid = readback.status == ADAPTER_STATUS_SUCCEEDED
        outputs = {
            "schema": DRY_RUN_SURFACE_PACKET_SCHEMA,
            "surface_ref": ref,
            "valid": valid,
            "readback_receipt_ref": readback.receipt_id,
            "dry_run": True,
            "non_live": True,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if valid else ADAPTER_STATUS_BLOCKED,
            summary=f"Dry-run {self.target_surface} surface validation completed.",
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("readback_exists",),
            residual_risk=None if valid else readback.summary,
        )
        self.receipts.append(receipt)
        return receipt

    def _base_outputs(
        self,
        *,
        surface_ref: SurfaceRef,
        surface_packet: Mapping[str, Any],
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        outputs = {
            "schema": DRY_RUN_SURFACE_PACKET_SCHEMA,
            "surface_kind": self.surface_kind,
            "target_surface": self.target_surface,
            "surface_ref": to_plain_data(asdict(surface_ref)),
            "surface_packet": dict(surface_packet),
            "test_only": bool(surface_packet.get("test_only", True)),
            "non_live": bool(surface_packet.get("non_live", True)),
            "dry_run": True,
            "live_mutation_allowed": False,
            "live_mutation_performed": False,
            "external_effects_blocked": list(self.external_effects),
        }
        outputs.update(dict(extra or {}))
        return outputs

    def _blocked_outputs(
        self,
        *,
        error: Mapping[str, Any],
        surface_packet: Mapping[str, Any],
        surface_ref: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        outputs = {
            "schema": DRY_RUN_SURFACE_PACKET_SCHEMA,
            "surface_kind": self.surface_kind,
            "target_surface": self.target_surface,
            "surface_packet": dict(surface_packet),
            "surface_ref": dict(surface_ref or {}),
            "test_only": bool(surface_packet.get("test_only", False)),
            "non_live": bool(surface_packet.get("non_live", False)),
            "dry_run": True,
            "live_mutation_allowed": False,
            "live_mutation_performed": False,
            "external_effects_blocked": list(self.external_effects),
            "error": dict(error),
        }
        return outputs


class DryRunObsidianSurfaceAdapter(DryRunSurfaceAdapter):
    adapter_id = "surface.obsidian_dry_run"
    surface_kind = "obsidian_note"
    target_surface = "obsidian"
    features = DryRunSurfaceAdapter.features + ("obsidian", "markdown")
    external_effects = ("obsidian_note_write", "vault_mutation")


class DryRunTelegramSurfaceAdapter(DryRunSurfaceAdapter):
    adapter_id = "surface.telegram_dry_run"
    surface_kind = "telegram_message"
    target_surface = "telegram"
    features = DryRunSurfaceAdapter.features + ("telegram", "message")
    external_effects = ("telegram_send", "chat_mutation")


class DryRunSheetsSurfaceAdapter(DryRunSurfaceAdapter):
    adapter_id = "surface.sheets_dry_run"
    surface_kind = "sheet_range"
    target_surface = "sheets"
    features = DryRunSurfaceAdapter.features + ("sheets", "tabular")
    external_effects = ("sheet_range_write", "workbook_mutation")


class SandboxObsidianMarkdownSurfaceAdapter:
    """File-backed Obsidian-style Markdown adapter for sandbox review packets."""

    adapter_id = "surface.obsidian_sandbox_markdown"
    family = AdapterFamily.SURFACE
    operations = ("publish", "readback", "ingest_decisions", "clear", "validate")

    def __init__(
        self,
        root_dir: str | Path,
        *,
        created_at: str = DETERMINISTIC_CREATED_AT,
        mutation_mode: str = "sandbox",
        allow_live_writes: bool = False,
        canonical_surface: str = "obsidian_sandbox_markdown",
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.created_at = created_at
        self.mutation_mode = mutation_mode
        self.allow_live_writes = allow_live_writes
        self.canonical_surface = canonical_surface
        self.receipts: list[Receipt] = []
        self._configuration_error = _sandbox_configuration_error(
            mutation_mode=mutation_mode,
            allow_live_mutation=allow_live_writes,
            live_effect_name="Obsidian vault write",
        )
        if self._configuration_error is None:
            self.root_dir.mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> CapabilitySet:
        contract = SurfaceCapabilityContract(
            surface_kind="obsidian_markdown_note",
            mode=self.mutation_mode,
            live_mutation_allowed=False,
            dry_run_only=False,
            readback_required=True,
            decision_ingest_supported=True,
            clear_requires_live_mutation=False,
            external_effects=(),
            receipt_schema=OBSIDIAN_SANDBOX_NOTE_SCHEMA,
            metadata={
                "sandbox_root": str(self.root_dir),
                "writes_local_root_only": True,
                "path_traversal_guard": True,
                "blocked_live_effects": ("obsidian_vault_write",),
            },
        )
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=(
                "deterministic",
                "sandbox",
                "local",
                "markdown",
                "obsidian_compatible",
                "readback",
                "decision_ingest",
                "path_traversal_guard",
            ),
            metadata={
                "schema": OBSIDIAN_SANDBOX_NOTE_SCHEMA,
                "decision_schema": SANDBOX_SURFACE_DECISION_SCHEMA,
                "mutation_mode": self.mutation_mode,
                "write_class": "sandbox",
                "sandbox": self._configuration_error is None,
                "test": self.mutation_mode == "test",
                "live": False,
                "live_mutation_allowed": False,
                "network_calls_allowed": False,
                "risk_policy": {
                    "side_effect": "local_draft",
                    "external_send": False,
                    "production_effect": False,
                    "fail_closed_on_unknown_or_live": True,
                },
                "surface_contract": contract.as_metadata(),
                "configuration_error": self._configuration_error,
            },
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

        packet = dict(surface_packet)
        stage_id = str(packet.get("stage_id") or invocation.stage_run_id)
        safety_error = self._configuration_error or _non_live_safety_error(
            packet,
            require_test_only=False,
        )
        if safety_error is None and bool(packet.get("write_to_real_vault", False)):
            safety_error = _live_mutation_error("Obsidian sandbox adapter refused a real vault write request.")
        if safety_error is not None:
            return self._blocked_result(
                invocation,
                status_summary=safety_error["message"],
                stage_id=stage_id,
                outputs={
                    "schema": OBSIDIAN_SANDBOX_NOTE_SCHEMA,
                    "error": safety_error,
                    "surface_packet": _redact_sensitive_mapping(packet),
                    "live_mutation_performed": False,
                },
                checks_run=("operation_supported", "sandbox_configuration_guard", "non_live_surface_guard"),
                next_action="configure a sandbox root and mark the packet non_live",
            )

        allowed_decisions = _string_tuple(packet.get("allowed_decisions", ("approved", "rejected")))
        action_fingerprint = str(packet.get("action_fingerprint", "")).strip()
        exact_action = str(
            packet.get("exact_action")
            or packet.get("exact_action_approved")
            or packet.get("requested_action")
            or ""
        ).strip()
        missing_fields = tuple(
            name
            for name, value in (
                ("action_fingerprint", action_fingerprint),
                ("exact_action", exact_action),
            )
            if not value
        )
        if missing_fields:
            return self._blocked_result(
                invocation,
                status_summary="Obsidian sandbox note was not published because required review fields were missing.",
                stage_id=stage_id,
                outputs={
                    "schema": OBSIDIAN_SANDBOX_NOTE_SCHEMA,
                    "error": {
                        "error_class": "invalid_surface_packet",
                        "message": "surface packet is missing required review fields",
                        "missing_fields": list(missing_fields),
                    },
                    "surface_packet": _redact_sensitive_mapping(packet),
                },
                checks_run=("operation_supported", "required_review_fields_present"),
                next_action="provide exact_action and action_fingerprint",
            )

        try:
            note_path = self._note_path(invocation, packet)
        except ValueError as exc:
            return self._blocked_result(
                invocation,
                status_summary=str(exc),
                stage_id=stage_id,
                outputs={
                    "schema": OBSIDIAN_SANDBOX_NOTE_SCHEMA,
                    "error": {
                        "error_class": "path_traversal_refused",
                        "message": str(exc),
                    },
                    "requested_path": str(
                        packet.get("note_path")
                        or packet.get("target_path")
                        or packet.get("relative_path")
                        or ""
                    ),
                    "live_mutation_performed": False,
                },
                checks_run=("operation_supported", "root_scoped_path"),
                next_action="choose a relative note path beneath the sandbox root",
            )

        note_path.parent.mkdir(parents=True, exist_ok=True)
        title = str(packet.get("title") or "Sandbox Obsidian review")
        human_ref = str(packet.get("human_ref") or "Suman(test)")
        evidence_refs = _string_tuple(packet.get("evidence_refs", ()))
        gate_id = str(packet.get("gate_id") or "").strip()
        requested_action = str(packet.get("requested_action") or exact_action).strip()
        artifact_review = _artifact_review_from_packet(packet)
        note_text = _render_review_card(
            invocation=invocation,
            stage_id=stage_id,
            title=title,
            human_ask=str(packet.get("human_ask") or packet.get("ask") or ""),
            human_ref=human_ref,
            canonical_surface=self.canonical_surface,
            gate_id=gate_id,
            allowed_decisions=allowed_decisions,
            requested_action=requested_action,
            exact_action=exact_action,
            action_fingerprint=action_fingerprint,
            evidence_refs=evidence_refs,
            artifact_review=artifact_review,
            test_only=bool(packet.get("test_only", True)),
            non_live=True,
            created_at=self.created_at,
        )
        existed = note_path.exists()
        if existed:
            existing_text = note_path.read_text(encoding="utf-8")
            existing_metadata = _extract_frontmatter(existing_text)
            if (
                str(existing_metadata.get("gate_id") or "") == gate_id
                and _extract_action_fingerprint(existing_text) == action_fingerprint
            ):
                note_text = existing_text
                idempotency_replayed = True
            elif existing_text == note_text:
                idempotency_replayed = True
            else:
                return self._blocked_result(
                    invocation,
                    status_summary="Obsidian sandbox note already exists with different review content.",
                    stage_id=stage_id,
                    outputs={
                        "schema": OBSIDIAN_SANDBOX_NOTE_SCHEMA,
                        "error": {
                            "error_class": "idempotency_conflict",
                            "message": "target note already exists with a different action fingerprint or gate id",
                        },
                        "note_path": str(note_path),
                        "content_hash": f"sha256:{_sha256_text(existing_text)}",
                    },
                    checks_run=("operation_supported", "root_scoped_path", "idempotency_conflict_check"),
                    next_action="use a fresh idempotency key or preserve the existing note fingerprint",
                )
        else:
            note_path.write_text(note_text, encoding="utf-8")
            idempotency_replayed = False

        content_hash = _sha256_text(note_text)
        surface_ref = {
            "surface_id": f"surface:{invocation.invocation_id}",
            "kind": "obsidian_markdown_note",
            "external_id": self._relative(note_path),
            "title": title,
            "readback_required": True,
            "status": "published",
            "note_path": str(note_path),
        }
        artifact = ArtifactRef(
            artifact_id=f"artifact:{invocation.invocation_id}:obsidian-note",
            role="surface_note",
            uri=f"sandbox-obsidian://{self._relative(note_path)}",
            content_hash=f"sha256:{content_hash}",
            mime_type="text/markdown",
            size_bytes=len(note_text.encode("utf-8")),
            created_by=self.adapter_id,
        )
        outputs = {
            "schema": OBSIDIAN_SANDBOX_NOTE_SCHEMA,
            "workflow_id": invocation.workflow_id,
            "instance_id": invocation.instance_id,
            "stage_id": stage_id,
            "stage_run_id": invocation.stage_run_id,
            "surface_ref": surface_ref,
            "note_path": str(note_path),
            "content_hash": f"sha256:{content_hash}",
            "canonical_surface": self.canonical_surface,
            "gate_id": gate_id,
            "requested_action": requested_action,
            "human_ref": human_ref,
            "allowed_decisions": list(allowed_decisions),
            "exact_action": exact_action,
            "action_fingerprint": action_fingerprint,
            "evidence_refs": list(evidence_refs),
            "artifact_review": artifact_review,
            "mutation_mode": self.mutation_mode,
            "write_class": "sandbox",
            "test_only": bool(packet.get("test_only", True)),
            "non_live": True,
            "live_mutation_performed": False,
            "idempotency_key": invocation.idempotency_key,
            "idempotency_replayed": idempotency_replayed,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Sandbox Obsidian Markdown note published locally.",
            created_at=self.created_at,
            stage_id=stage_id,
            artifact_refs=(artifact,),
            outputs=outputs,
            checks_run=("operation_supported", "sandbox_configuration_guard", "root_scoped_path", "markdown_note_written"),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs, artifact_refs=(artifact,))

    def readback(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="readback",
            idempotency_key=str(ref.get("surface_id") or ref.get("note_path") or "obsidian-sandbox"),
        )
        try:
            note_path = self._path_from_ref(ref)
        except ValueError as exc:
            return self._blocked_receipt(
                invocation,
                summary=str(exc),
                outputs={
                    "schema": OBSIDIAN_SANDBOX_NOTE_SCHEMA,
                    "surface_ref": ref,
                    "error": {"error_class": "path_traversal_refused", "message": str(exc)},
                },
                checks_run=("root_scoped_path",),
            )
        exists = note_path.exists()
        outputs: dict[str, Any] = {
            "schema": OBSIDIAN_SANDBOX_NOTE_SCHEMA,
            "surface_ref": ref,
            "note_path": str(note_path),
            "exists": exists,
            "canonical_surface": self.canonical_surface,
            "mutation_mode": self.mutation_mode,
            "non_live": True,
            "live_mutation_performed": False,
        }
        status = ADAPTER_STATUS_SUCCEEDED if exists else ADAPTER_STATUS_BLOCKED
        summary = "Sandbox Obsidian note read back." if exists else "Sandbox Obsidian note is missing."
        if exists:
            text = note_path.read_text(encoding="utf-8")
            outputs.update(
                {
                    "content_hash": f"sha256:{_sha256_text(text)}",
                    "bytes": len(text.encode("utf-8")),
                    "action_fingerprint": _extract_action_fingerprint(text),
                    "readback_confirmed": True,
                }
            )
        receipt = make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("root_scoped_path", "note_exists"),
            residual_risk=None if exists else summary,
            next_action=None if exists else "re-publish the sandbox note",
        )
        self.receipts.append(receipt)
        return receipt

    def ingest_decisions(self, surface_query: Mapping[str, Any]) -> list[Receipt]:
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="ingest_decisions",
            idempotency_key=str(surface_query.get("query_id", "obsidian-sandbox-decision")),
        )
        if self._configuration_error is not None:
            return [
                self._decision_receipt(
                    invocation,
                    status=ADAPTER_STATUS_BLOCKED,
                    summary=self._configuration_error["message"],
                    note_path=self.root_dir / "blocked.md",
                    surface_query=surface_query,
                    error_class=self._configuration_error["error_class"],
                    checked_decisions=(),
                    allowed_decisions=(),
                )
            ]
        try:
            note_path = self._path_from_query(surface_query)
        except ValueError as exc:
            return [
                self._decision_receipt(
                    invocation,
                    status=ADAPTER_STATUS_BLOCKED,
                    summary=str(exc),
                    note_path=self.root_dir / "blocked.md",
                    surface_query=surface_query,
                    error_class="path_traversal_refused",
                    checked_decisions=(),
                    allowed_decisions=(),
                )
            ]
        return [self._decision_from_markdown(invocation, note_path, surface_query)]

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
            idempotency_key=str(ref.get("surface_id") or "obsidian-sandbox"),
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Sandbox Obsidian note clear recorded: {reason}",
            created_at=self.created_at,
            outputs={
                "schema": OBSIDIAN_SANDBOX_NOTE_SCHEMA,
                "surface_ref": ref,
                "reason": reason,
                "cleared": True,
                "non_live": True,
                "live_mutation_performed": False,
            },
            checks_run=("clear_recorded_without_external_effect",),
        )
        self.receipts.append(receipt)
        return receipt

    def validate(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        readback = self.readback(ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="validate",
            idempotency_key=str(ref.get("surface_id") or "obsidian-sandbox"),
        )
        valid = readback.status == ADAPTER_STATUS_SUCCEEDED
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if valid else ADAPTER_STATUS_BLOCKED,
            summary="Sandbox Obsidian note validation completed.",
            created_at=self.created_at,
            outputs={
                "schema": OBSIDIAN_SANDBOX_NOTE_SCHEMA,
                "surface_ref": ref,
                "valid": valid,
                "readback_receipt_ref": readback.receipt_id,
                "non_live": True,
            },
            checks_run=("readback_exists",),
            residual_risk=None if valid else readback.summary,
        )
        self.receipts.append(receipt)
        return receipt

    def _blocked_result(
        self,
        invocation: AdapterInvocation,
        *,
        status_summary: str,
        stage_id: str | None,
        outputs: Mapping[str, Any],
        checks_run: tuple[str, ...],
        next_action: str,
    ) -> AdapterResult:
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=status_summary,
            created_at=self.created_at,
            stage_id=stage_id,
            outputs=outputs,
            checks_run=checks_run,
            residual_risk=status_summary,
            next_action=next_action,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def _blocked_receipt(
        self,
        invocation: AdapterInvocation,
        *,
        summary: str,
        outputs: Mapping[str, Any],
        checks_run: tuple[str, ...],
    ) -> Receipt:
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=checks_run,
            residual_risk=summary,
        )
        self.receipts.append(receipt)
        return receipt

    def _decision_from_markdown(
        self,
        invocation: AdapterInvocation,
        note_path: Path,
        surface_query: Mapping[str, Any],
    ) -> Receipt:
        expected_fingerprint = str(
            surface_query.get("expected_action_fingerprint")
            or surface_query.get("action_fingerprint")
            or ""
        ).strip()
        exact_action = str(
            surface_query.get("exact_action")
            or surface_query.get("exact_action_approved")
            or surface_query.get("requested_action")
            or ""
        ).strip()
        allowed_decisions = _string_tuple(surface_query.get("allowed_decisions", ()))
        if not note_path.exists():
            return self._decision_receipt(
                invocation,
                status=ADAPTER_STATUS_BLOCKED,
                summary="Sandbox Obsidian decision ingest blocked because the source note is missing.",
                note_path=note_path,
                surface_query=surface_query,
                error_class="missing_review_note",
                checked_decisions=(),
                allowed_decisions=allowed_decisions,
            )

        text = note_path.read_text(encoding="utf-8")
        metadata = _extract_frontmatter(text)
        if not allowed_decisions:
            allowed_decisions = _extract_allowed_decisions(text)
        checked_decisions = _extract_checked_decisions(text)
        unknown_checked = tuple(decision for decision in checked_decisions if decision not in allowed_decisions)
        note_fingerprint = _extract_action_fingerprint(text)
        if not expected_fingerprint:
            expected_fingerprint = note_fingerprint
        if not exact_action:
            exact_action = str(metadata.get("exact_action", ""))
        expected_gate_id = str(surface_query.get("gate_id") or metadata.get("gate_id") or "").strip()
        note_gate_id = str(metadata.get("gate_id") or "").strip()

        block_reason: str | None = None
        error_class: str | None = None
        if not note_fingerprint:
            block_reason = "Sandbox Obsidian decision ingest blocked because the source note is missing an action fingerprint."
            error_class = "missing_action_fingerprint"
        elif note_fingerprint != expected_fingerprint:
            block_reason = "Sandbox Obsidian decision ingest blocked because the source note fingerprint does not match the expected action."
            error_class = "action_fingerprint_mismatch"
        elif note_gate_id and expected_gate_id and note_gate_id != expected_gate_id:
            block_reason = "Sandbox Obsidian decision ingest blocked because the source note gate id does not match the expected waiting gate."
            error_class = "gate_id_mismatch"
        elif unknown_checked:
            block_reason = "Sandbox Obsidian decision ingest blocked because the note contains a checked unknown decision."
            error_class = "unknown_checked_decision"
        elif len(checked_decisions) != 1:
            block_reason = "Sandbox Obsidian decision ingest blocked because exactly one allowed decision must be checked."
            error_class = "ambiguous_decision_count"

        if block_reason is not None:
            return self._decision_receipt(
                invocation,
                status=ADAPTER_STATUS_BLOCKED,
                summary=block_reason,
                note_path=note_path,
                surface_query=surface_query,
                error_class=error_class or "decision_ingest_blocked",
                checked_decisions=checked_decisions,
                allowed_decisions=allowed_decisions,
                note_action_fingerprint=note_fingerprint,
            )

        return self._decision_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Sandbox Obsidian decision ingested: {checked_decisions[0]}.",
            note_path=note_path,
            surface_query={**dict(surface_query), "exact_action": exact_action, "gate_id": expected_gate_id},
            decision=checked_decisions[0],
            checked_decisions=checked_decisions,
            allowed_decisions=allowed_decisions,
            note_action_fingerprint=note_fingerprint,
        )

    def _decision_receipt(
        self,
        invocation: AdapterInvocation,
        *,
        status: str,
        summary: str,
        note_path: Path,
        surface_query: Mapping[str, Any],
        checked_decisions: tuple[str, ...],
        allowed_decisions: tuple[str, ...],
        decision: str | None = None,
        note_action_fingerprint: str | None = None,
        error_class: str | None = None,
    ) -> Receipt:
        outputs: dict[str, Any] = _decision_outputs(
            schema=SANDBOX_SURFACE_DECISION_SCHEMA,
            canonical_surface=self.canonical_surface,
            surface_query=surface_query,
            decision=decision,
            source_ref=str(note_path),
            transcript_or_message_ref=str(note_path),
            checked_decisions=checked_decisions,
            allowed_decisions=allowed_decisions,
            note_action_fingerprint=note_action_fingerprint,
        )
        if error_class is not None:
            outputs["error"] = {"error_class": error_class, "message": summary}
        receipt = make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("source_note_read", "one_allowed_checkbox_checked", "action_fingerprint_matches"),
            residual_risk=None if status == ADAPTER_STATUS_SUCCEEDED else summary,
            next_action=None if status == ADAPTER_STATUS_SUCCEEDED else "check exactly one allowed decision and preserve the fingerprint",
        )
        self.receipts.append(receipt)
        return receipt

    def _note_path(self, invocation: AdapterInvocation, packet: Mapping[str, Any]) -> Path:
        raw_path = packet.get("note_path") or packet.get("target_path") or packet.get("relative_path")
        if raw_path:
            path = Path(str(raw_path))
            if path.suffix != ".md":
                path = path.with_suffix(".md")
            return self._checked_path(path)
        key = invocation.idempotency_key or invocation.invocation_id
        return self._checked_path(Path("notes") / f"{_slug(str(key))}.md")

    def _path_from_ref(self, surface_ref: Mapping[str, Any]) -> Path:
        raw_path = surface_ref.get("note_path") or surface_ref.get("external_id")
        if raw_path is None:
            raise ValueError("surface_ref must include note_path or external_id")
        return self._checked_path(Path(str(raw_path)))

    def _path_from_query(self, surface_query: Mapping[str, Any]) -> Path:
        if "note_path" in surface_query:
            return self._checked_path(Path(str(surface_query["note_path"])))
        surface_ref = surface_query.get("surface_ref")
        if isinstance(surface_ref, Mapping):
            return self._path_from_ref(surface_ref)
        raise ValueError("surface_query must include note_path or surface_ref")

    def _checked_path(self, path: Path) -> Path:
        candidate = path if path.is_absolute() else self.root_dir / path
        resolved = candidate.resolve()
        if resolved != self.root_dir and self.root_dir not in resolved.parents:
            raise ValueError("sandbox Obsidian path must stay beneath adapter root_dir")
        return resolved

    def _relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root_dir))


class SandboxTelegramOutboxSurfaceAdapter:
    """Telegram-style surface adapter that writes only a local outbox spool."""

    adapter_id = "surface.telegram_sandbox_outbox"
    family = AdapterFamily.SURFACE
    operations = ("publish", "readback", "ingest_decisions", "clear", "validate")

    def __init__(
        self,
        outbox_dir: str | Path,
        *,
        created_at: str = DETERMINISTIC_CREATED_AT,
        mutation_mode: str = "sandbox",
        allow_network_send: bool = False,
        canonical_surface: str = "telegram_sandbox_outbox",
    ) -> None:
        self.outbox_dir = Path(outbox_dir).resolve()
        self.created_at = created_at
        self.mutation_mode = mutation_mode
        self.allow_network_send = allow_network_send
        self.canonical_surface = canonical_surface
        self.receipts: list[Receipt] = []
        self._configuration_error = _sandbox_configuration_error(
            mutation_mode=mutation_mode,
            allow_live_mutation=allow_network_send,
            live_effect_name="Telegram network send",
        )
        if self._configuration_error is None:
            (self.outbox_dir / "messages").mkdir(parents=True, exist_ok=True)
            (self.outbox_dir / "decisions").mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> CapabilitySet:
        contract = SurfaceCapabilityContract(
            surface_kind="telegram_outbox_message",
            mode=self.mutation_mode,
            live_mutation_allowed=False,
            dry_run_only=False,
            readback_required=True,
            decision_ingest_supported=True,
            clear_requires_live_mutation=False,
            external_effects=(),
            receipt_schema=TELEGRAM_SANDBOX_MESSAGE_SCHEMA,
            metadata={
                "outbox_dir": str(self.outbox_dir),
                "writes_local_spool_only": True,
                "network_calls_allowed": False,
                "blocked_live_effects": ("telegram_send",),
            },
        )
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=(
                "deterministic",
                "sandbox",
                "local",
                "telegram_compatible",
                "outbox_spool",
                "readback",
                "decision_ingest",
            ),
            metadata={
                "schema": TELEGRAM_SANDBOX_MESSAGE_SCHEMA,
                "decision_schema": SANDBOX_SURFACE_DECISION_SCHEMA,
                "mutation_mode": self.mutation_mode,
                "write_class": "sandbox",
                "sandbox": self._configuration_error is None,
                "test": self.mutation_mode == "test",
                "live": False,
                "live_mutation_allowed": False,
                "network_calls_allowed": False,
                "risk_policy": {
                    "side_effect": "local_draft",
                    "external_send": False,
                    "production_effect": False,
                    "fail_closed_on_unknown_or_live": True,
                },
                "surface_contract": contract.as_metadata(),
                "configuration_error": self._configuration_error,
            },
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
        packet = dict(surface_packet)
        stage_id = str(packet.get("stage_id") or invocation.stage_run_id)
        safety_error = self._configuration_error or _non_live_safety_error(
            packet,
            require_test_only=False,
        )
        if safety_error is None and (
            bool(packet.get("send_now", False))
            or bool(packet.get("network_send_requested", False))
            or bool(packet.get("telegram_api_call_requested", False))
        ):
            safety_error = _live_mutation_error("Telegram sandbox adapter refused a network send request.")
        if safety_error is not None:
            return self._blocked_result(
                invocation,
                status_summary=safety_error["message"],
                outputs={
                    "schema": TELEGRAM_SANDBOX_MESSAGE_SCHEMA,
                    "error": safety_error,
                    "surface_packet": _redact_sensitive_mapping(packet),
                    "live_mutation_performed": False,
                    "network_call_performed": False,
                },
                checks_run=("operation_supported", "sandbox_configuration_guard", "network_send_refused"),
                next_action="configure a sandbox outbox and do not request Telegram network sends",
            )

        message_path = self._message_path(invocation)
        decision_path = self._decision_path_for_message(message_path)
        payload = self._message_payload(
            invocation=invocation,
            packet=packet,
            message_path=message_path,
            decision_path=decision_path,
        )
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        existed = message_path.exists()
        if existed:
            existing_payload = json.loads(message_path.read_text(encoding="utf-8"))
            if existing_payload.get("idempotency_key") == invocation.idempotency_key:
                payload = existing_payload
                serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
                idempotency_replayed = True
            else:
                return self._blocked_result(
                    invocation,
                    status_summary="Telegram sandbox outbox message already exists for a different idempotency key.",
                    outputs={
                        "schema": TELEGRAM_SANDBOX_MESSAGE_SCHEMA,
                        "error": {
                            "error_class": "idempotency_conflict",
                            "message": "message spool path already exists for a different idempotency key",
                        },
                        "message_path": str(message_path),
                    },
                    checks_run=("operation_supported", "idempotency_conflict_check"),
                    next_action="use a fresh idempotency key",
                )
        else:
            message_path.write_text(serialized, encoding="utf-8")
            idempotency_replayed = False

        content_hash = _sha256_text(serialized)
        surface_ref = {
            "surface_id": f"surface:{invocation.invocation_id}",
            "kind": "telegram_outbox_message",
            "external_id": self._relative(message_path),
            "title": payload["title"],
            "readback_required": True,
            "status": "spooled",
            "message_path": str(message_path),
            "decision_path": str(decision_path),
        }
        artifact = ArtifactRef(
            artifact_id=f"artifact:{invocation.invocation_id}:telegram-outbox",
            role="surface_message",
            uri=f"sandbox-telegram://{self._relative(message_path)}",
            content_hash=f"sha256:{content_hash}",
            mime_type="application/json",
            size_bytes=len(serialized.encode("utf-8")),
            created_by=self.adapter_id,
        )
        outputs = {
            "schema": TELEGRAM_SANDBOX_MESSAGE_SCHEMA,
            "surface_ref": surface_ref,
            "message_path": str(message_path),
            "decision_path": str(decision_path),
            "content_hash": f"sha256:{content_hash}",
            "canonical_surface": self.canonical_surface,
            "mutation_mode": self.mutation_mode,
            "write_class": "sandbox",
            "test_only": bool(packet.get("test_only", True)),
            "non_live": True,
            "network_call_performed": False,
            "live_mutation_performed": False,
            "idempotency_key": invocation.idempotency_key,
            "idempotency_replayed": idempotency_replayed,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Telegram sandbox message spooled locally without network send.",
            created_at=self.created_at,
            stage_id=stage_id,
            artifact_refs=(artifact,),
            outputs=outputs,
            checks_run=("operation_supported", "sandbox_configuration_guard", "network_send_refused", "outbox_message_written"),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs, artifact_refs=(artifact,))

    def readback(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="readback",
            idempotency_key=str(ref.get("surface_id") or ref.get("message_path") or "telegram-sandbox"),
        )
        try:
            message_path = self._path_from_ref(ref, "message_path")
        except ValueError as exc:
            return self._blocked_receipt(
                invocation,
                summary=str(exc),
                outputs={
                    "schema": TELEGRAM_SANDBOX_MESSAGE_SCHEMA,
                    "surface_ref": ref,
                    "error": {"error_class": "path_traversal_refused", "message": str(exc)},
                    "network_call_performed": False,
                },
                checks_run=("root_scoped_path",),
            )
        exists = message_path.exists()
        payload: dict[str, Any] = {}
        if exists:
            payload = json.loads(message_path.read_text(encoding="utf-8"))
        outputs = {
            "schema": TELEGRAM_SANDBOX_MESSAGE_SCHEMA,
            "surface_ref": ref,
            "message_path": str(message_path),
            "exists": exists,
            "message": payload,
            "readback_confirmed": exists,
            "non_live": True,
            "network_call_performed": False,
            "live_mutation_performed": False,
        }
        summary = "Telegram sandbox outbox message read back." if exists else "Telegram sandbox outbox message is missing."
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if exists else ADAPTER_STATUS_BLOCKED,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("root_scoped_path", "message_spool_exists"),
            residual_risk=None if exists else summary,
            next_action=None if exists else "re-publish the sandbox message",
        )
        self.receipts.append(receipt)
        return receipt

    def inject_decision(
        self,
        surface_ref: SurfaceRef | Mapping[str, Any],
        *,
        decision: str,
        human_ref: str = "Suman(test)",
        gate_id: str | None = None,
        action_fingerprint: str | None = None,
        exact_action: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        allowed_decisions: tuple[str, ...] = (),
    ) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="inject_decision",
            idempotency_key=str(ref.get("surface_id") or "telegram-sandbox-inject"),
        )
        try:
            decision_path = self._path_from_ref(ref, "decision_path")
            message_path = self._path_from_ref(ref, "message_path")
        except ValueError as exc:
            return self._blocked_receipt(
                invocation,
                summary=str(exc),
                outputs={
                    "schema": SANDBOX_SURFACE_DECISION_SCHEMA,
                    "surface_ref": ref,
                    "error": {"error_class": "path_traversal_refused", "message": str(exc)},
                },
                checks_run=("root_scoped_path",),
            )
        message = json.loads(message_path.read_text(encoding="utf-8")) if message_path.exists() else {}
        payload = {
            "schema": SANDBOX_SURFACE_DECISION_SCHEMA,
            "surface_ref": ref,
            "message_path": str(message_path),
            "decision_path": str(decision_path),
            "decision": decision,
            "human_ref": human_ref,
            "gate_id": gate_id if gate_id is not None else str(message.get("gate_id") or ""),
            "exact_action": exact_action if exact_action is not None else str(message.get("exact_action") or ""),
            "action_fingerprint": action_fingerprint if action_fingerprint is not None else str(message.get("action_fingerprint") or ""),
            "evidence_refs": list(evidence_refs or _string_tuple(message.get("evidence_refs", ()))),
            "allowed_decisions": list(allowed_decisions or _string_tuple(message.get("allowed_decisions", ()))),
            "test_only": True,
            "non_live": True,
            "created_at": self.created_at,
        }
        decision_path.parent.mkdir(parents=True, exist_ok=True)
        decision_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Telegram sandbox test decision injected: {decision}.",
            created_at=self.created_at,
            outputs={
                "schema": SANDBOX_SURFACE_DECISION_SCHEMA,
                "decision_path": str(decision_path),
                "message_path": str(message_path),
                "decision": decision,
                "test_only": True,
                "non_live": True,
                "network_call_performed": False,
            },
            checks_run=("root_scoped_path", "test_decision_written"),
        )
        self.receipts.append(receipt)
        return receipt

    def ingest_decisions(self, surface_query: Mapping[str, Any]) -> list[Receipt]:
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="ingest_decisions",
            idempotency_key=str(surface_query.get("query_id", "telegram-sandbox-decision")),
        )
        if self._configuration_error is not None:
            return [
                self._decision_receipt(
                    invocation,
                    status=ADAPTER_STATUS_BLOCKED,
                    summary=self._configuration_error["message"],
                    surface_query=surface_query,
                    decision_payload={},
                    source_ref=str(self.outbox_dir / "blocked.json"),
                    error_class=self._configuration_error["error_class"],
                )
            ]
        try:
            decision_path = self._decision_path_from_query(surface_query)
        except ValueError as exc:
            return [
                self._decision_receipt(
                    invocation,
                    status=ADAPTER_STATUS_BLOCKED,
                    summary=str(exc),
                    surface_query=surface_query,
                    decision_payload={},
                    source_ref=str(self.outbox_dir / "blocked.json"),
                    error_class="path_traversal_refused",
                )
            ]
        if not decision_path.exists():
            return [
                self._decision_receipt(
                    invocation,
                    status=ADAPTER_STATUS_BLOCKED,
                    summary="Telegram sandbox decision ingest blocked because no test decision artifact exists.",
                    surface_query=surface_query,
                    decision_payload={},
                    source_ref=str(decision_path),
                    error_class="missing_test_decision",
                )
            ]

        payload = json.loads(decision_path.read_text(encoding="utf-8"))
        return [self._decision_from_payload(invocation, surface_query, payload, decision_path)]

    def clear(self, surface_ref: SurfaceRef | Mapping[str, Any], reason: str) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="clear",
            idempotency_key=str(ref.get("surface_id") or "telegram-sandbox"),
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Telegram sandbox outbox clear recorded: {reason}",
            created_at=self.created_at,
            outputs={
                "schema": TELEGRAM_SANDBOX_MESSAGE_SCHEMA,
                "surface_ref": ref,
                "reason": reason,
                "cleared": True,
                "non_live": True,
                "network_call_performed": False,
                "live_mutation_performed": False,
            },
            checks_run=("clear_recorded_without_network_send",),
        )
        self.receipts.append(receipt)
        return receipt

    def validate(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        readback = self.readback(ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="validate",
            idempotency_key=str(ref.get("surface_id") or "telegram-sandbox"),
        )
        valid = readback.status == ADAPTER_STATUS_SUCCEEDED
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if valid else ADAPTER_STATUS_BLOCKED,
            summary="Telegram sandbox outbox validation completed.",
            created_at=self.created_at,
            outputs={
                "schema": TELEGRAM_SANDBOX_MESSAGE_SCHEMA,
                "surface_ref": ref,
                "valid": valid,
                "readback_receipt_ref": readback.receipt_id,
                "non_live": True,
                "network_call_performed": False,
            },
            checks_run=("readback_exists",),
            residual_risk=None if valid else readback.summary,
        )
        self.receipts.append(receipt)
        return receipt

    def _message_payload(
        self,
        *,
        invocation: AdapterInvocation,
        packet: Mapping[str, Any],
        message_path: Path,
        decision_path: Path,
    ) -> dict[str, Any]:
        exact_action = str(
            packet.get("exact_action")
            or packet.get("exact_action_approved")
            or packet.get("requested_action")
            or ""
        )
        return {
            "schema": TELEGRAM_SANDBOX_MESSAGE_SCHEMA,
            "adapter_id": self.adapter_id,
            "workflow_id": invocation.workflow_id,
            "instance_id": invocation.instance_id,
            "stage_run_id": invocation.stage_run_id,
            "invocation_id": invocation.invocation_id,
            "idempotency_key": invocation.idempotency_key,
            "title": str(packet.get("title") or "Sandbox Telegram message"),
            "body": str(packet.get("human_ask") or packet.get("body") or packet.get("ask") or ""),
            "gate_id": str(packet.get("gate_id") or ""),
            "requested_action": str(packet.get("requested_action") or exact_action),
            "exact_action": exact_action,
            "action_fingerprint": str(packet.get("action_fingerprint") or ""),
            "allowed_decisions": list(_string_tuple(packet.get("allowed_decisions", ()))),
            "evidence_refs": list(_string_tuple(packet.get("evidence_refs", ()))),
            "message_path": str(message_path),
            "decision_path": str(decision_path),
            "test_only": bool(packet.get("test_only", True)),
            "non_live": True,
            "network_call_performed": False,
            "live_mutation_performed": False,
            "created_at": self.created_at,
        }

    def _decision_from_payload(
        self,
        invocation: AdapterInvocation,
        surface_query: Mapping[str, Any],
        payload: Mapping[str, Any],
        decision_path: Path,
    ) -> Receipt:
        decision = str(payload.get("decision") or "").strip()
        allowed_decisions = _string_tuple(surface_query.get("allowed_decisions", ())) or _string_tuple(
            payload.get("allowed_decisions", ())
        )
        expected_fingerprint = str(
            surface_query.get("expected_action_fingerprint")
            or surface_query.get("action_fingerprint")
            or payload.get("action_fingerprint")
            or ""
        ).strip()
        payload_fingerprint = str(payload.get("action_fingerprint") or "").strip()
        expected_gate_id = str(surface_query.get("gate_id") or payload.get("gate_id") or "").strip()
        payload_gate_id = str(payload.get("gate_id") or "").strip()
        error_class: str | None = None
        summary: str | None = None
        if not decision:
            error_class = "missing_test_decision"
            summary = "Telegram sandbox decision artifact does not include a decision."
        elif allowed_decisions and decision not in allowed_decisions:
            error_class = "decision_not_allowed"
            summary = "Telegram sandbox decision is not in the allowed decision set."
        elif not payload_fingerprint:
            error_class = "missing_action_fingerprint"
            summary = "Telegram sandbox decision artifact is missing an action fingerprint."
        elif payload_fingerprint != expected_fingerprint:
            error_class = "action_fingerprint_mismatch"
            summary = "Telegram sandbox decision fingerprint does not match the expected waiting gate."
        elif payload_gate_id and expected_gate_id and payload_gate_id != expected_gate_id:
            error_class = "gate_id_mismatch"
            summary = "Telegram sandbox decision gate id does not match the expected waiting gate."

        if error_class is not None:
            return self._decision_receipt(
                invocation,
                status=ADAPTER_STATUS_BLOCKED,
                summary=summary or "Telegram sandbox decision ingest blocked.",
                surface_query=surface_query,
                decision_payload=payload,
                source_ref=str(decision_path),
                error_class=error_class,
            )
        return self._decision_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Telegram sandbox decision ingested: {decision}.",
            surface_query=surface_query,
            decision_payload=payload,
            source_ref=str(decision_path),
        )

    def _decision_receipt(
        self,
        invocation: AdapterInvocation,
        *,
        status: str,
        summary: str,
        surface_query: Mapping[str, Any],
        decision_payload: Mapping[str, Any],
        source_ref: str,
        error_class: str | None = None,
    ) -> Receipt:
        outputs = _decision_outputs(
            schema=SANDBOX_SURFACE_DECISION_SCHEMA,
            canonical_surface=self.canonical_surface,
            surface_query=surface_query,
            decision=str(decision_payload.get("decision") or "") or None,
            source_ref=source_ref,
            transcript_or_message_ref=str(decision_payload.get("message_path") or source_ref),
            decision_payload=decision_payload,
            allowed_decisions=_string_tuple(
                surface_query.get("allowed_decisions", decision_payload.get("allowed_decisions", ()))
            ),
        )
        if error_class is not None:
            outputs["error"] = {"error_class": error_class, "message": summary}
        receipt = make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("test_decision_read", "decision_allowed", "action_fingerprint_matches"),
            residual_risk=None if status == ADAPTER_STATUS_SUCCEEDED else summary,
            next_action=None if status == ADAPTER_STATUS_SUCCEEDED else "inject one allowed test decision with the expected fingerprint",
        )
        self.receipts.append(receipt)
        return receipt

    def _blocked_result(
        self,
        invocation: AdapterInvocation,
        *,
        status_summary: str,
        outputs: Mapping[str, Any],
        checks_run: tuple[str, ...],
        next_action: str,
    ) -> AdapterResult:
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=status_summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=checks_run,
            residual_risk=status_summary,
            next_action=next_action,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def _blocked_receipt(
        self,
        invocation: AdapterInvocation,
        *,
        summary: str,
        outputs: Mapping[str, Any],
        checks_run: tuple[str, ...],
    ) -> Receipt:
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=checks_run,
            residual_risk=summary,
        )
        self.receipts.append(receipt)
        return receipt

    def _message_path(self, invocation: AdapterInvocation) -> Path:
        key = _slug(str(invocation.idempotency_key or invocation.invocation_id))
        return self._checked_path(Path("messages") / f"{key}.json")

    def _decision_path_for_message(self, message_path: Path) -> Path:
        return self._checked_path(Path("decisions") / f"{message_path.stem}.decision.json")

    def _decision_path_from_query(self, surface_query: Mapping[str, Any]) -> Path:
        if "decision_path" in surface_query:
            return self._checked_path(Path(str(surface_query["decision_path"])))
        surface_ref = surface_query.get("surface_ref")
        if isinstance(surface_ref, Mapping):
            return self._path_from_ref(surface_ref, "decision_path")
        raise ValueError("surface_query must include decision_path or surface_ref")

    def _path_from_ref(self, surface_ref: Mapping[str, Any], key: str) -> Path:
        raw_path = surface_ref.get(key)
        if raw_path is None and key == "message_path":
            raw_path = surface_ref.get("external_id")
        if raw_path is None:
            raise ValueError(f"surface_ref must include {key}")
        return self._checked_path(Path(str(raw_path)))

    def _checked_path(self, path: Path) -> Path:
        candidate = path if path.is_absolute() else self.outbox_dir / path
        resolved = candidate.resolve()
        if resolved != self.outbox_dir and self.outbox_dir not in resolved.parents:
            raise ValueError("sandbox Telegram path must stay beneath adapter outbox_dir")
        return resolved

    def _relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.outbox_dir))


class LiveObsidianMarkdownSurfaceAdapter:
    """Guarded live Obsidian/Northstar Markdown adapter.

    This adapter performs one approved operator-surface write: creating or
    reusing a Markdown review note beneath a configured vault prefix. It does
    not authorize public publish or any other live effect named by the packet.
    """

    adapter_id = "surface.obsidian_live_markdown"
    family = AdapterFamily.SURFACE
    operations = ("publish", "readback", "ingest_decisions", "clear", "validate")

    def __init__(
        self,
        vault_root: str | Path,
        *,
        allowed_relative_prefix: str | Path,
        allow_live_write: bool = False,
        created_at: str = DETERMINISTIC_CREATED_AT,
        canonical_surface: str = "obsidian_live_markdown",
    ) -> None:
        self.vault_root = Path(vault_root).resolve()
        self.allowed_relative_prefix = Path(allowed_relative_prefix)
        self.allow_live_write = allow_live_write
        self.created_at = created_at
        self.canonical_surface = canonical_surface
        self.receipts: list[Receipt] = []
        self._configuration_error = self._live_configuration_error()
        if self._configuration_error is None:
            self._allowed_root.mkdir(parents=True, exist_ok=True)

    @property
    def _allowed_root(self) -> Path:
        return (self.vault_root / self.allowed_relative_prefix).resolve()

    def capabilities(self) -> CapabilitySet:
        contract = SurfaceCapabilityContract(
            surface_kind="obsidian_markdown_note",
            mode="live",
            live_mutation_allowed=self.allow_live_write and self._configuration_error is None,
            dry_run_only=False,
            readback_required=True,
            decision_ingest_supported=True,
            clear_requires_live_mutation=True,
            external_effects=("obsidian_vault_write",),
            receipt_schema=LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
            metadata={
                "vault_root": str(self.vault_root),
                "allowed_relative_prefix": str(self.allowed_relative_prefix),
                "path_traversal_guard": True,
                "public_publish_blocked": True,
                "requires_packet_live_operator_surface_allowed": True,
            },
        )
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=(
                "live",
                "markdown",
                "obsidian_compatible",
                "northstar_compatible",
                "readback",
                "decision_ingest",
                "path_traversal_guard",
                "fail_closed",
            ),
            metadata={
                "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
                "decision_schema": LIVE_OPERATOR_SURFACE_DECISION_SCHEMA,
                "mutation_mode": "live",
                "write_class": "live_operator_surface",
                "live": self._configuration_error is None,
                "live_mutation_allowed": self.allow_live_write and self._configuration_error is None,
                "network_calls_allowed": False,
                "risk_policy": {
                    "side_effect": "internal_state",
                    "external_send": False,
                    "public_publish_blocked": True,
                    "production_effect": False,
                    "fail_closed_on_unknown_or_unsafe": True,
                },
                "surface_contract": contract.as_metadata(),
                "configuration_error": self._configuration_error,
            },
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

        packet = dict(surface_packet)
        stage_id = str(packet.get("stage_id") or invocation.stage_run_id)
        safety_error = self._configuration_error or _live_operator_surface_safety_error(
            packet,
            effect_name="Obsidian/Northstar Markdown write",
        )
        if safety_error is not None:
            return self._blocked_result(
                invocation,
                status_summary=safety_error["message"],
                stage_id=stage_id,
                outputs={
                    "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
                    "error": safety_error,
                    "surface_packet": _redact_sensitive_mapping(packet),
                    "live_operator_surface_allowed": bool(packet.get("live_operator_surface_allowed", False)),
                    "public_publish_blocked": packet.get("public_publish_blocked", True) is not False,
                    "live_mutation_performed": False,
                },
                checks_run=("operation_supported", "live_operator_surface_authorized", "unsafe_action_guard"),
                next_action="retry only with explicit live operator-surface authorization and safe review scope",
            )

        allowed_decisions = _string_tuple(packet.get("allowed_decisions", ("approved", "rejected")))
        action_fingerprint = str(packet.get("action_fingerprint", "")).strip()
        exact_action = str(
            packet.get("exact_action")
            or packet.get("exact_action_approved")
            or packet.get("requested_action")
            or ""
        ).strip()
        missing_fields = tuple(
            name
            for name, value in (
                ("action_fingerprint", action_fingerprint),
                ("exact_action", exact_action),
            )
            if not value
        )
        if missing_fields:
            return self._blocked_result(
                invocation,
                status_summary="Live Obsidian note was not published because required review fields were missing.",
                stage_id=stage_id,
                outputs={
                    "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
                    "error": {
                        "error_class": "invalid_surface_packet",
                        "message": "surface packet is missing required review fields",
                        "missing_fields": list(missing_fields),
                    },
                    "surface_packet": _redact_sensitive_mapping(packet),
                    "live_mutation_performed": False,
                },
                checks_run=("operation_supported", "required_review_fields_present"),
                next_action="provide exact_action and action_fingerprint",
            )

        try:
            note_path = self._note_path(invocation, packet)
        except ValueError as exc:
            return self._blocked_result(
                invocation,
                status_summary=str(exc),
                stage_id=stage_id,
                outputs={
                    "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
                    "error": {"error_class": "path_traversal_refused", "message": str(exc)},
                    "requested_path": str(
                        packet.get("note_path")
                        or packet.get("target_path")
                        or packet.get("relative_path")
                        or ""
                    ),
                    "live_mutation_performed": False,
                },
                checks_run=("operation_supported", "vault_prefix_scoped_path"),
                next_action="choose a relative note path beneath the allowed vault prefix",
            )

        note_path.parent.mkdir(parents=True, exist_ok=True)
        title = str(packet.get("title") or "Live Obsidian review")
        human_ref = str(packet.get("human_ref") or "Suman")
        evidence_refs = _string_tuple(packet.get("evidence_refs", ()))
        gate_id = str(packet.get("gate_id") or "").strip()
        requested_action = str(packet.get("requested_action") or exact_action).strip()
        artifact_review = _artifact_review_from_packet(packet)
        note_text = _render_live_review_card(
            invocation=invocation,
            stage_id=stage_id,
            title=title,
            human_ask=str(packet.get("human_ask") or packet.get("ask") or ""),
            human_ref=human_ref,
            canonical_surface=self.canonical_surface,
            gate_id=gate_id,
            allowed_decisions=allowed_decisions,
            requested_action=requested_action,
            exact_action=exact_action,
            action_fingerprint=action_fingerprint,
            evidence_refs=evidence_refs,
            artifact_review=artifact_review,
            created_at=self.created_at,
        )
        idempotency_replayed = False
        if note_path.exists():
            existing_text = note_path.read_text(encoding="utf-8")
            existing_metadata = _extract_frontmatter(existing_text)
            if (
                str(existing_metadata.get("gate_id") or "") == gate_id
                and _extract_action_fingerprint(existing_text) == action_fingerprint
            ):
                note_text = existing_text
                idempotency_replayed = True
            elif existing_text == note_text:
                idempotency_replayed = True
            else:
                return self._blocked_result(
                    invocation,
                    status_summary="Live Obsidian note already exists with different review content.",
                    stage_id=stage_id,
                    outputs={
                        "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
                        "error": {
                            "error_class": "idempotency_conflict",
                            "message": "target note already exists with a different action fingerprint or gate id",
                        },
                        "note_path": str(note_path),
                        "content_hash": f"sha256:{_sha256_text(existing_text)}",
                        "live_mutation_performed": False,
                    },
                    checks_run=("operation_supported", "vault_prefix_scoped_path", "idempotency_conflict_check"),
                    next_action="use a fresh idempotency key or preserve the existing note fingerprint",
                )
        else:
            note_path.write_text(note_text, encoding="utf-8")

        content_hash = _sha256_text(note_text)
        relative_path = self._relative(note_path)
        surface_ref = {
            "surface_id": f"surface:{invocation.invocation_id}",
            "kind": "obsidian_markdown_note",
            "external_id": relative_path,
            "title": title,
            "readback_required": True,
            "status": "published",
            "note_path": str(note_path),
            "content_hash": f"sha256:{content_hash}",
        }
        artifact = ArtifactRef(
            artifact_id=f"artifact:{invocation.invocation_id}:live-obsidian-note",
            role="surface_note",
            uri=f"obsidian-live://{relative_path}",
            content_hash=f"sha256:{content_hash}",
            mime_type="text/markdown",
            size_bytes=len(note_text.encode("utf-8")),
            created_by=self.adapter_id,
        )
        outputs = {
            "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
            "workflow_id": invocation.workflow_id,
            "instance_id": invocation.instance_id,
            "stage_id": stage_id,
            "stage_run_id": invocation.stage_run_id,
            "surface_ref": surface_ref,
            "note_path": str(note_path),
            "relative_path": relative_path,
            "content_hash": f"sha256:{content_hash}",
            "canonical_surface": self.canonical_surface,
            "gate_id": gate_id,
            "requested_action": requested_action,
            "human_ref": human_ref,
            "allowed_decisions": list(allowed_decisions),
            "exact_action": exact_action,
            "action_fingerprint": action_fingerprint,
            "evidence_refs": list(evidence_refs),
            "artifact_review": artifact_review,
            "mutation_mode": "live",
            "write_class": "live_operator_surface",
            "live_operator_surface_allowed": True,
            "public_publish_blocked": True,
            "live_mutation_performed": True,
            "idempotency_key": invocation.idempotency_key,
            "idempotency_replayed": idempotency_replayed,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Live Obsidian/Northstar Markdown note published with readback-required metadata.",
            created_at=self.created_at,
            stage_id=stage_id,
            artifact_refs=(artifact,),
            outputs=outputs,
            checks_run=(
                "operation_supported",
                "live_operator_surface_authorized",
                "unsafe_action_guard",
                "vault_prefix_scoped_path",
                "markdown_note_written",
            ),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs, artifact_refs=(artifact,))

    def readback(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="readback",
            idempotency_key=str(ref.get("surface_id") or ref.get("note_path") or "obsidian-live"),
        )
        try:
            note_path = self._path_from_ref(ref)
        except ValueError as exc:
            return self._blocked_receipt(
                invocation,
                summary=str(exc),
                outputs={
                    "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
                    "surface_ref": ref,
                    "error": {"error_class": "path_traversal_refused", "message": str(exc)},
                },
                checks_run=("vault_prefix_scoped_path",),
            )
        exists = note_path.exists()
        expected_hash = str(ref.get("content_hash") or "")
        outputs: dict[str, Any] = {
            "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
            "surface_ref": ref,
            "note_path": str(note_path),
            "relative_path": self._relative(note_path),
            "exists": exists,
            "canonical_surface": self.canonical_surface,
            "mutation_mode": "live",
            "write_class": "live_operator_surface",
            "live_operator_surface_allowed": self.allow_live_write,
            "public_publish_blocked": True,
        }
        status = ADAPTER_STATUS_SUCCEEDED if exists else ADAPTER_STATUS_BLOCKED
        summary = "Live Obsidian note read back." if exists else "Live Obsidian note is missing."
        if exists:
            text = note_path.read_text(encoding="utf-8")
            actual_hash = f"sha256:{_sha256_text(text)}"
            outputs.update(
                {
                    "content_hash": actual_hash,
                    "expected_content_hash": expected_hash or None,
                    "hash_matches": not expected_hash or expected_hash == actual_hash,
                    "bytes": len(text.encode("utf-8")),
                    "action_fingerprint": _extract_action_fingerprint(text),
                    "readback_confirmed": True,
                }
            )
        receipt = make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("vault_prefix_scoped_path", "note_exists", "content_hash_recorded"),
            residual_risk=None if exists else summary,
            next_action=None if exists else "re-publish the live operator-surface note",
        )
        self.receipts.append(receipt)
        return receipt

    def ingest_decisions(self, surface_query: Mapping[str, Any]) -> list[Receipt]:
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="ingest_decisions",
            idempotency_key=str(surface_query.get("query_id", "obsidian-live-decision")),
        )
        if self._configuration_error is not None:
            return [
                self._decision_receipt(
                    invocation,
                    status=ADAPTER_STATUS_BLOCKED,
                    summary=self._configuration_error["message"],
                    note_path=self._allowed_root / "blocked.md",
                    surface_query=surface_query,
                    error_class=self._configuration_error["error_class"],
                    checked_decisions=(),
                    allowed_decisions=(),
                )
            ]
        try:
            note_path = self._path_from_query(surface_query)
        except ValueError as exc:
            return [
                self._decision_receipt(
                    invocation,
                    status=ADAPTER_STATUS_BLOCKED,
                    summary=str(exc),
                    note_path=self._allowed_root / "blocked.md",
                    surface_query=surface_query,
                    error_class="path_traversal_refused",
                    checked_decisions=(),
                    allowed_decisions=(),
                )
            ]
        return [self._decision_from_markdown(invocation, note_path, surface_query)]

    def clear(self, surface_ref: SurfaceRef | Mapping[str, Any], reason: str) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="clear",
            idempotency_key=str(ref.get("surface_id") or "obsidian-live"),
        )
        error = _live_mutation_error("Live Obsidian clear/delete is not authorized by this adapter.")
        outputs = {
            "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
            "surface_ref": ref,
            "reason": reason,
            "cleared": False,
            "error": error,
            "public_publish_blocked": True,
            "live_mutation_performed": False,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=error["message"],
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("destructive_or_live_clear_refused",),
            residual_risk=error["message"],
            next_action="create a fresh explicit gate for any live clear/delete request",
        )
        self.receipts.append(receipt)
        return receipt

    def validate(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        readback = self.readback(ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="validate",
            idempotency_key=str(ref.get("surface_id") or "obsidian-live"),
        )
        valid = readback.status == ADAPTER_STATUS_SUCCEEDED
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if valid else ADAPTER_STATUS_BLOCKED,
            summary="Live Obsidian note validation completed.",
            created_at=self.created_at,
            outputs={
                "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
                "surface_ref": ref,
                "valid": valid,
                "readback_receipt_ref": readback.receipt_id,
                "public_publish_blocked": True,
            },
            checks_run=("readback_exists",),
            residual_risk=None if valid else readback.summary,
        )
        self.receipts.append(receipt)
        return receipt

    def _live_configuration_error(self) -> dict[str, Any] | None:
        if not self.allow_live_write:
            return _live_mutation_error("Live Obsidian/Northstar write requires allow_live_write=True.")
        if self.allowed_relative_prefix.is_absolute() or ".." in self.allowed_relative_prefix.parts:
            return {
                "error_class": "invalid_allowed_prefix",
                "message": "Live Obsidian allowed_relative_prefix must be a relative path without traversal.",
                "retryable": False,
            }
        return None

    def _blocked_result(
        self,
        invocation: AdapterInvocation,
        *,
        status_summary: str,
        stage_id: str | None,
        outputs: Mapping[str, Any],
        checks_run: tuple[str, ...],
        next_action: str,
    ) -> AdapterResult:
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=status_summary,
            created_at=self.created_at,
            stage_id=stage_id,
            outputs=outputs,
            checks_run=checks_run,
            residual_risk=status_summary,
            next_action=next_action,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def _blocked_receipt(
        self,
        invocation: AdapterInvocation,
        *,
        summary: str,
        outputs: Mapping[str, Any],
        checks_run: tuple[str, ...],
    ) -> Receipt:
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=checks_run,
            residual_risk=summary,
        )
        self.receipts.append(receipt)
        return receipt

    def _decision_from_markdown(
        self,
        invocation: AdapterInvocation,
        note_path: Path,
        surface_query: Mapping[str, Any],
    ) -> Receipt:
        expected_fingerprint = str(
            surface_query.get("expected_action_fingerprint")
            or surface_query.get("action_fingerprint")
            or ""
        ).strip()
        exact_action = str(
            surface_query.get("exact_action")
            or surface_query.get("exact_action_approved")
            or surface_query.get("requested_action")
            or ""
        ).strip()
        allowed_decisions = _string_tuple(surface_query.get("allowed_decisions", ()))
        if not note_path.exists():
            return self._decision_receipt(
                invocation,
                status=ADAPTER_STATUS_BLOCKED,
                summary="Live Obsidian decision ingest blocked because the source note is missing.",
                note_path=note_path,
                surface_query=surface_query,
                error_class="missing_review_note",
                checked_decisions=(),
                allowed_decisions=allowed_decisions,
            )

        text = note_path.read_text(encoding="utf-8")
        metadata = _extract_frontmatter(text)
        if not allowed_decisions:
            allowed_decisions = _extract_allowed_decisions(text)
        checked_decisions = _extract_checked_decisions(text)
        unknown_checked = tuple(decision for decision in checked_decisions if decision not in allowed_decisions)
        note_fingerprint = _extract_action_fingerprint(text)
        if not expected_fingerprint:
            expected_fingerprint = note_fingerprint
        if not exact_action:
            exact_action = str(metadata.get("exact_action", ""))
        expected_gate_id = str(surface_query.get("gate_id") or metadata.get("gate_id") or "").strip()
        note_gate_id = str(metadata.get("gate_id") or "").strip()

        block_reason: str | None = None
        error_class: str | None = None
        if not note_fingerprint:
            block_reason = "Live Obsidian decision ingest blocked because the source note is missing an action fingerprint."
            error_class = "missing_action_fingerprint"
        elif note_fingerprint != expected_fingerprint:
            block_reason = "Live Obsidian decision ingest blocked because the source note fingerprint does not match the expected action."
            error_class = "action_fingerprint_mismatch"
        elif note_gate_id and expected_gate_id and note_gate_id != expected_gate_id:
            block_reason = "Live Obsidian decision ingest blocked because the source note gate id does not match the expected waiting gate."
            error_class = "gate_id_mismatch"
        elif unknown_checked:
            block_reason = "Live Obsidian decision ingest blocked because the note contains a checked unknown decision."
            error_class = "unknown_checked_decision"
        elif len(checked_decisions) != 1:
            block_reason = "Live Obsidian decision ingest blocked because exactly one allowed decision must be checked."
            error_class = "ambiguous_decision_count"

        if block_reason is not None:
            return self._decision_receipt(
                invocation,
                status=ADAPTER_STATUS_BLOCKED,
                summary=block_reason,
                note_path=note_path,
                surface_query=surface_query,
                error_class=error_class or "decision_ingest_blocked",
                checked_decisions=checked_decisions,
                allowed_decisions=allowed_decisions,
                note_action_fingerprint=note_fingerprint,
            )

        return self._decision_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Live Obsidian decision ingested: {checked_decisions[0]}.",
            note_path=note_path,
            surface_query={**dict(surface_query), "exact_action": exact_action, "gate_id": expected_gate_id},
            decision=checked_decisions[0],
            checked_decisions=checked_decisions,
            allowed_decisions=allowed_decisions,
            note_action_fingerprint=note_fingerprint,
        )

    def _decision_receipt(
        self,
        invocation: AdapterInvocation,
        *,
        status: str,
        summary: str,
        note_path: Path,
        surface_query: Mapping[str, Any],
        checked_decisions: tuple[str, ...],
        allowed_decisions: tuple[str, ...],
        decision: str | None = None,
        note_action_fingerprint: str | None = None,
        error_class: str | None = None,
    ) -> Receipt:
        outputs: dict[str, Any] = _decision_outputs(
            schema=LIVE_OPERATOR_SURFACE_DECISION_SCHEMA,
            canonical_surface=self.canonical_surface,
            surface_query=surface_query,
            decision=decision,
            source_ref=str(note_path),
            transcript_or_message_ref=str(note_path),
            checked_decisions=checked_decisions,
            allowed_decisions=allowed_decisions,
            note_action_fingerprint=note_action_fingerprint,
            test_only=False,
            non_live=False,
            live_operator_surface_allowed=True,
            public_publish_blocked=True,
            live_mutation_performed=False,
        )
        if error_class is not None:
            outputs["error"] = {"error_class": error_class, "message": summary}
        receipt = make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("source_note_read", "one_allowed_checkbox_checked", "action_fingerprint_matches"),
            residual_risk=None if status == ADAPTER_STATUS_SUCCEEDED else summary,
            next_action=None if status == ADAPTER_STATUS_SUCCEEDED else "check exactly one allowed decision and preserve the fingerprint",
        )
        self.receipts.append(receipt)
        return receipt

    def _note_path(self, invocation: AdapterInvocation, packet: Mapping[str, Any]) -> Path:
        raw_path = packet.get("note_path") or packet.get("target_path") or packet.get("relative_path")
        if raw_path:
            path = Path(str(raw_path))
            if path.suffix != ".md":
                path = path.with_suffix(".md")
            return self._checked_path(path)
        key = invocation.idempotency_key or invocation.invocation_id
        return self._checked_path(Path("notes") / f"{_slug(str(key))}.md")

    def _path_from_ref(self, surface_ref: Mapping[str, Any]) -> Path:
        raw_path = surface_ref.get("note_path") or surface_ref.get("external_id")
        if raw_path is None:
            raise ValueError("surface_ref must include note_path or external_id")
        return self._checked_path(Path(str(raw_path)))

    def _path_from_query(self, surface_query: Mapping[str, Any]) -> Path:
        if "note_path" in surface_query:
            return self._checked_path(Path(str(surface_query["note_path"])))
        surface_ref = surface_query.get("surface_ref")
        if isinstance(surface_ref, Mapping):
            return self._path_from_ref(surface_ref)
        raise ValueError("surface_query must include note_path or surface_ref")

    def _checked_path(self, path: Path) -> Path:
        if path.is_absolute():
            candidate = path
        else:
            prefix = self.allowed_relative_prefix
            relative = path if path.parts[: len(prefix.parts)] == prefix.parts else prefix / path
            candidate = self.vault_root / relative
        resolved = candidate.resolve()
        if resolved != self._allowed_root and self._allowed_root not in resolved.parents:
            raise ValueError("live Obsidian path must stay beneath allowed_relative_prefix")
        return resolved

    def _relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.vault_root))



class LocalMarkdownHumanReviewSurfaceAdapter:
    """Local Markdown review-card adapter for fixture-safe human gates.

    The adapter writes only beneath ``root_dir``. It models an Obsidian-compatible
    Markdown note without knowing any real vault, Telegram, OpenClaw, or host
    runtime paths.
    """

    adapter_id = "surface.local_markdown_human_review"
    family = AdapterFamily.SURFACE
    operations = ("publish", "readback", "ingest_decisions", "clear", "validate")

    def __init__(
        self,
        root_dir: str | Path,
        *,
        created_at: str = DETERMINISTIC_CREATED_AT,
        canonical_surface: str = "local_markdown_human_review",
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.created_at = created_at
        self.canonical_surface = canonical_surface
        self.receipts: list[Receipt] = []
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> CapabilitySet:
        contract = SurfaceCapabilityContract(
            surface_kind="local_markdown",
            mode="local_artifact",
            live_mutation_allowed=False,
            dry_run_only=False,
            readback_required=True,
            decision_ingest_supported=True,
            clear_requires_live_mutation=False,
            external_effects=(),
            receipt_schema=LOCAL_HUMAN_REVIEW_CARD_SCHEMA,
            metadata={"writes_local_root_only": True},
        )
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("deterministic", "local", "markdown", "obsidian_compatible", "readback"),
            metadata={
                "root_dir": str(self.root_dir),
                "schema": LOCAL_HUMAN_REVIEW_CARD_SCHEMA,
                "non_live_only": True,
                "surface_contract": contract.as_metadata(),
            },
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

        packet = dict(surface_packet)
        allowed_decisions = _string_tuple(packet.get("allowed_decisions", ("approved", "rejected")))
        action_fingerprint = str(packet.get("action_fingerprint", "")).strip()
        exact_action = str(
            packet.get("exact_action")
            or packet.get("exact_action_approved")
            or packet.get("requested_action")
            or ""
        ).strip()
        evidence_refs = _string_tuple(packet.get("evidence_refs", ()))
        stage_id = str(packet.get("stage_id") or invocation.stage_run_id)
        title = str(packet.get("title") or "Local human review")
        human_ref = str(packet.get("human_ref") or "Suman(test)")
        test_only = bool(packet.get("test_only", True))
        non_live = bool(packet.get("non_live", True))
        gate_id = str(packet.get("gate_id") or "").strip()
        requested_action = str(packet.get("requested_action") or exact_action).strip()
        artifact_review = _artifact_review_from_packet(packet)

        safety_error = _non_live_safety_error(packet, require_test_only=False)
        if safety_error is not None:
            outputs = {
                "schema": LOCAL_HUMAN_REVIEW_CARD_SCHEMA,
                "error": safety_error,
                "test_only": test_only,
                "non_live": non_live,
            }
            receipt = make_adapter_receipt(
                invocation,
                status=ADAPTER_STATUS_BLOCKED,
                summary=safety_error["message"],
                created_at=self.created_at,
                stage_id=stage_id,
                outputs=outputs,
                checks_run=("operation_supported", "non_live_surface_guard"),
                next_action="publish through a non-live local surface packet",
            )
            self.receipts.append(receipt)
            return result_from_receipt(invocation, receipt, outputs=outputs)

        missing_fields = tuple(
            name
            for name, value in (
                ("action_fingerprint", action_fingerprint),
                ("exact_action", exact_action),
            )
            if not value
        )
        if missing_fields:
            outputs = {
                "schema": LOCAL_HUMAN_REVIEW_CARD_SCHEMA,
                "error": {
                    "error_class": "invalid_surface_packet",
                    "message": "surface packet is missing required review fields",
                    "missing_fields": list(missing_fields),
                },
            }
            receipt = make_adapter_receipt(
                invocation,
                status=ADAPTER_STATUS_BLOCKED,
                summary="Local Markdown review card was not published because required fields were missing.",
                created_at=self.created_at,
                stage_id=stage_id,
                outputs=outputs,
                checks_run=("operation_supported", "required_review_fields_present"),
                next_action="provide exact_action and action_fingerprint",
            )
            self.receipts.append(receipt)
            return result_from_receipt(invocation, receipt, outputs=outputs)

        note_path = self._note_path(invocation, stage_id=stage_id)
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_text = _render_review_card(
            invocation=invocation,
            stage_id=stage_id,
            title=title,
            human_ask=str(packet.get("human_ask") or packet.get("ask") or ""),
            human_ref=human_ref,
            canonical_surface=self.canonical_surface,
            gate_id=gate_id,
            allowed_decisions=allowed_decisions,
            requested_action=requested_action,
            exact_action=exact_action,
            action_fingerprint=action_fingerprint,
            evidence_refs=evidence_refs,
            artifact_review=artifact_review,
            test_only=test_only,
            non_live=non_live,
            created_at=self.created_at,
        )
        note_path.write_text(note_text, encoding="utf-8")
        content_hash = _sha256_text(note_text)
        surface_ref = SurfaceRef(
            surface_id=f"surface:{invocation.invocation_id}",
            kind="local_markdown",
            external_id=self._relative(note_path),
            title=title,
            readback_required=True,
            status="published",
        )
        outputs = {
            "schema": LOCAL_HUMAN_REVIEW_CARD_SCHEMA,
            "workflow_id": invocation.workflow_id,
            "instance_id": invocation.instance_id,
            "stage_id": stage_id,
            "stage_run_id": invocation.stage_run_id,
            "surface_ref": to_plain_data(asdict(surface_ref)),
            "note_path": str(note_path),
            "content_hash": f"sha256:{content_hash}",
            "canonical_surface": self.canonical_surface,
            "gate_id": gate_id,
            "requested_action": requested_action,
            "human_ref": human_ref,
            "allowed_decisions": list(allowed_decisions),
            "exact_action": exact_action,
            "action_fingerprint": action_fingerprint,
            "evidence_refs": list(evidence_refs),
            "artifact_review": artifact_review,
            "test_only": test_only,
            "non_live": non_live,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Local Markdown human review card published.",
            created_at=self.created_at,
            stage_id=stage_id,
            outputs=outputs,
            checks_run=("operation_supported", "root_scoped_path", "markdown_card_written"),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def readback(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="readback",
            idempotency_key=str(ref.get("surface_id") or ref.get("note_path") or "local-review"),
        )
        note_path = self._path_from_ref(ref)
        exists = note_path.exists()
        outputs: dict[str, Any] = {
            "schema": LOCAL_HUMAN_REVIEW_CARD_SCHEMA,
            "surface_ref": ref,
            "note_path": str(note_path),
            "exists": exists,
            "canonical_surface": self.canonical_surface,
        }
        status = ADAPTER_STATUS_SUCCEEDED if exists else ADAPTER_STATUS_BLOCKED
        summary = "Local Markdown human review card read back."
        checks_run = ["root_scoped_path", "note_exists"]
        if exists:
            text = note_path.read_text(encoding="utf-8")
            outputs.update(
                {
                    "content_hash": f"sha256:{_sha256_text(text)}",
                    "bytes": len(text.encode("utf-8")),
                    "action_fingerprint": _extract_action_fingerprint(text),
                }
            )
        else:
            summary = "Local Markdown human review card is missing."
        receipt = make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=tuple(checks_run),
            next_action=None if exists else "re-publish the local review card",
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
            idempotency_key=str(surface_query.get("query_id", "local-review-decision")),
        )
        note_path = self._path_from_query(surface_query)
        expected_fingerprint = str(
            surface_query.get("expected_action_fingerprint")
            or surface_query.get("action_fingerprint")
            or ""
        ).strip()
        exact_action = str(
            surface_query.get("exact_action")
            or surface_query.get("exact_action_approved")
            or surface_query.get("requested_action")
            or ""
        ).strip()
        expected_gate_id = str(surface_query.get("gate_id") or "").strip()
        requested_action = str(surface_query.get("requested_action") or exact_action).strip()
        allowed_decisions = _string_tuple(surface_query.get("allowed_decisions", ()))
        evidence_refs = _string_tuple(surface_query.get("evidence_refs", ()))
        human_ref = str(surface_query.get("human_ref") or "Suman(test)")
        if not note_path.exists():
            return [
                self._decision_receipt(
                    invocation,
                    status=ADAPTER_STATUS_BLOCKED,
                    summary="Local Markdown decision ingest blocked because the source note is missing.",
                    note_path=note_path,
                    human_ref=human_ref,
                    gate_id=expected_gate_id,
                    requested_action=requested_action,
                    exact_action=exact_action,
                    action_fingerprint=expected_fingerprint,
                    evidence_refs=evidence_refs,
                    error_class="missing_review_note",
                    next_action="re-publish the local review card",
                )
            ]

        text = note_path.read_text(encoding="utf-8")
        note_metadata = _extract_frontmatter(text)
        note_allowed = _extract_allowed_decisions(text)
        if not allowed_decisions:
            allowed_decisions = note_allowed
        checked_decisions = _extract_checked_decisions(text)
        unknown_checked = tuple(decision for decision in checked_decisions if decision not in allowed_decisions)
        note_fingerprint = _extract_action_fingerprint(text)
        if not expected_fingerprint:
            expected_fingerprint = note_fingerprint
        if not exact_action:
            exact_action = str(note_metadata.get("exact_action", ""))
        if not requested_action:
            requested_action = str(note_metadata.get("requested_action") or exact_action)
        note_gate_id = str(note_metadata.get("gate_id") or "")
        if not expected_gate_id:
            expected_gate_id = note_gate_id
        if not evidence_refs:
            evidence_refs = _string_tuple(note_metadata.get("evidence_refs", ()))

        block_reason: str | None = None
        error_class: str | None = None
        if not note_fingerprint:
            block_reason = "Local Markdown decision ingest blocked because the source note is missing an action fingerprint."
            error_class = "missing_action_fingerprint"
        elif note_fingerprint != expected_fingerprint:
            block_reason = "Local Markdown decision ingest blocked because the source note fingerprint does not match the expected action."
            error_class = "action_fingerprint_mismatch"
        elif note_gate_id and expected_gate_id and note_gate_id != expected_gate_id:
            block_reason = "Local Markdown decision ingest blocked because the source note gate id does not match the expected waiting gate."
            error_class = "gate_id_mismatch"
        elif unknown_checked:
            block_reason = "Local Markdown decision ingest blocked because the note contains a checked unknown decision."
            error_class = "unknown_checked_decision"
        elif len(checked_decisions) != 1:
            block_reason = "Local Markdown decision ingest blocked because exactly one allowed decision must be checked."
            error_class = "ambiguous_decision_count"

        if block_reason is not None:
            return [
                self._decision_receipt(
                    invocation,
                    status=ADAPTER_STATUS_BLOCKED,
                    summary=block_reason,
                    note_path=note_path,
                    human_ref=human_ref,
                    gate_id=expected_gate_id,
                    requested_action=requested_action,
                    exact_action=exact_action,
                    action_fingerprint=expected_fingerprint,
                    evidence_refs=evidence_refs,
                    checked_decisions=checked_decisions,
                    allowed_decisions=allowed_decisions,
                    note_action_fingerprint=note_fingerprint,
                    error_class=error_class or "decision_ingest_blocked",
                    next_action="leave exactly one allowed checkbox checked and preserve the action fingerprint",
                )
            ]

        decision = checked_decisions[0]
        receipt = self._decision_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Local Markdown human review decision ingested: {decision}.",
            note_path=note_path,
            human_ref=human_ref,
            gate_id=expected_gate_id,
            requested_action=requested_action,
            decision=decision,
            exact_action=exact_action,
            action_fingerprint=expected_fingerprint,
            evidence_refs=evidence_refs,
            checked_decisions=checked_decisions,
            allowed_decisions=allowed_decisions,
            note_action_fingerprint=note_fingerprint,
        )
        return [receipt]

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
            idempotency_key=str(ref.get("surface_id") or "local-review"),
        )
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"Local Markdown human review cleared: {reason}",
            created_at=self.created_at,
            outputs={"surface_ref": ref, "reason": reason},
        )
        self.receipts.append(receipt)
        return receipt

    def validate(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        readback = self.readback(ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="validate",
            idempotency_key=str(ref.get("surface_id") or "local-review"),
        )
        valid = readback.status == ADAPTER_STATUS_SUCCEEDED
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if valid else ADAPTER_STATUS_BLOCKED,
            summary="Local Markdown human review validation completed.",
            created_at=self.created_at,
            outputs={"surface_ref": ref, "valid": valid, "readback_receipt_ref": readback.receipt_id},
            checks_run=("readback_exists",),
        )
        self.receipts.append(receipt)
        return receipt

    def _decision_receipt(
        self,
        invocation: AdapterInvocation,
        *,
        status: str,
        summary: str,
        note_path: Path,
        human_ref: str,
        gate_id: str,
        requested_action: str,
        exact_action: str,
        action_fingerprint: str,
        evidence_refs: tuple[str, ...],
        decision: str | None = None,
        checked_decisions: tuple[str, ...] = (),
        allowed_decisions: tuple[str, ...] = (),
        note_action_fingerprint: str | None = None,
        error_class: str | None = None,
        next_action: str | None = None,
    ) -> Receipt:
        outputs: dict[str, Any] = {
            "schema": LOCAL_HUMAN_REVIEW_DECISION_SCHEMA,
            "canonical_surface": self.canonical_surface,
            "gate_id": gate_id,
            "human_ref": human_ref,
            "decision": decision,
            "requested_action": requested_action,
            "exact_action_approved": exact_action,
            "action_fingerprint": action_fingerprint,
            "note_action_fingerprint": note_action_fingerprint,
            "evidence_refs": list(evidence_refs),
            "source_note_path": str(note_path),
            "checked_decisions": list(checked_decisions),
            "allowed_decisions": list(allowed_decisions),
            "test_only": True,
            "non_live": True,
        }
        if error_class is not None:
            outputs["error"] = {"error_class": error_class, "message": summary}
        receipt = make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=(
                "source_note_read",
                "one_allowed_checkbox_checked",
                "action_fingerprint_present",
                "action_fingerprint_matches",
            ),
            residual_risk=None if status == ADAPTER_STATUS_SUCCEEDED else summary,
            next_action=next_action,
        )
        self.receipts.append(receipt)
        return receipt

    def _note_path(self, invocation: AdapterInvocation, *, stage_id: str) -> Path:
        filename = "-".join(
            _slug(part)
            for part in (
                invocation.workflow_id,
                invocation.instance_id,
                stage_id,
                invocation.stage_run_id,
                invocation.invocation_id,
            )
            if part
        )
        return self._checked_path(Path("review_cards") / f"{filename}.md")

    def _path_from_ref(self, surface_ref: Mapping[str, Any]) -> Path:
        raw_path = surface_ref.get("note_path") or surface_ref.get("external_id")
        if raw_path is None:
            raise ValueError("surface_ref must include note_path or external_id")
        return self._checked_path(Path(str(raw_path)))

    def _path_from_query(self, surface_query: Mapping[str, Any]) -> Path:
        if "note_path" in surface_query:
            return self._checked_path(Path(str(surface_query["note_path"])))
        surface_ref = surface_query.get("surface_ref")
        if isinstance(surface_ref, Mapping):
            return self._path_from_ref(surface_ref)
        raise ValueError("surface_query must include note_path or surface_ref")

    def _checked_path(self, path: Path) -> Path:
        candidate = path if path.is_absolute() else self.root_dir / path
        resolved = candidate.resolve()
        if resolved != self.root_dir and self.root_dir not in resolved.parents:
            raise ValueError("local review path must stay beneath adapter root_dir")
        return resolved

    def _relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root_dir))


def _artifact_review_from_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    title = str(packet.get("artifact_title") or packet.get("artifact_label") or "").strip()
    intro = str(packet.get("artifact_intro") or "").strip()
    link = str(packet.get("artifact_link") or packet.get("artifact_path") or "").strip()
    markdown = str(packet.get("artifact_markdown") or packet.get("artifact_body") or "").strip()
    if not any((title, intro, link, markdown)):
        return {}
    if not title:
        title = "Artifact To Review"
    return {
        "title": title,
        "intro": intro,
        "link": link,
        "markdown": markdown,
        "embedded": bool(markdown),
    }


def _artifact_review_metadata(artifact_review: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": str(artifact_review.get("title") or ""),
        "link": str(artifact_review.get("link") or ""),
        "embedded": bool(artifact_review.get("embedded")),
    }


def _render_artifact_review_section(artifact_review: Mapping[str, Any]) -> list[str]:
    if not artifact_review:
        return []
    title = str(artifact_review.get("title") or "Artifact To Review")
    intro = str(artifact_review.get("intro") or "").strip()
    link = str(artifact_review.get("link") or "").strip()
    markdown = str(artifact_review.get("markdown") or "").strip()
    lines = ["## Artifact To Review", "", f"### {title}", ""]
    if intro:
        lines.extend([intro, ""])
    if link:
        lines.extend([f"- Review source: [{title}](<{link}>)", ""])
    if markdown:
        lines.extend([markdown, ""])
    return lines


def _render_review_card(
    *,
    invocation: AdapterInvocation,
    stage_id: str,
    title: str,
    human_ask: str,
    human_ref: str,
    canonical_surface: str,
    gate_id: str,
    allowed_decisions: tuple[str, ...],
    requested_action: str,
    exact_action: str,
    action_fingerprint: str,
    evidence_refs: tuple[str, ...],
    artifact_review: Mapping[str, Any],
    test_only: bool,
    non_live: bool,
    created_at: str,
) -> str:
    metadata = {
        "schema": LOCAL_HUMAN_REVIEW_CARD_SCHEMA,
        "canonical_surface": canonical_surface,
        "workflow_id": invocation.workflow_id,
        "instance_id": invocation.instance_id,
        "stage_id": stage_id,
        "stage_run_id": invocation.stage_run_id,
        "invocation_id": invocation.invocation_id,
        "gate_id": gate_id,
        "allowed_decisions": list(allowed_decisions),
        "requested_action": requested_action,
        "exact_action": exact_action,
        "evidence_refs": list(evidence_refs),
        "test_only": test_only,
        "non_live": non_live,
        "created_at": created_at,
    }
    if artifact_review:
        metadata["artifact_review"] = _artifact_review_metadata(artifact_review)
    frontmatter = "\n".join(
        f"{key}: {json.dumps(value, sort_keys=True)}" for key, value in metadata.items()
    )
    evidence_lines = "\n".join(f"- `{ref}`" for ref in evidence_refs) or "- `none`"
    artifact_lines = _render_artifact_review_section(artifact_review)
    decision_lines = "\n".join(f"- [ ] `{decision}`" for decision in allowed_decisions)
    label = "TEST ONLY - NON-LIVE LOCAL REVIEW PACKET" if test_only else "LOCAL REVIEW PACKET - NON-LIVE"
    ask = human_ask or "Choose exactly one allowed decision below."
    return "\n".join(
        [
            "---",
            frontmatter,
            "---",
            "",
            f"# {title}",
            "",
            f"**{label}**",
            "",
            "## Review Context",
            f"- Workflow ID: `{invocation.workflow_id}`",
            f"- Instance ID: `{invocation.instance_id}`",
            f"- Stage ID: `{stage_id}`",
            f"- Stage Run ID: `{invocation.stage_run_id}`",
            f"- Gate ID: `{gate_id or 'not-provided'}`",
            f"- Invocation ID: `{invocation.invocation_id}`",
            f"- Canonical surface: `{canonical_surface}`",
            f"- Human ref: `{human_ref}`",
            f"- Requested action: `{requested_action}`",
            f"- Exact action: `{exact_action}`",
            f"- Action fingerprint: `{action_fingerprint}`",
            "",
            *artifact_lines,
            "## Evidence",
            evidence_lines,
            "",
            "## Decision",
            ask,
            "",
            "Check exactly one allowed decision. Comments are context only and do not authorize any live, external, destructive, auth, money, deploy, publish, Telegram, OpenClaw, oldmac, or trading action.",
            "",
            decision_lines,
            "",
        ]
    )


def _render_live_review_card(
    *,
    invocation: AdapterInvocation,
    stage_id: str,
    title: str,
    human_ask: str,
    human_ref: str,
    canonical_surface: str,
    gate_id: str,
    allowed_decisions: tuple[str, ...],
    requested_action: str,
    exact_action: str,
    action_fingerprint: str,
    evidence_refs: tuple[str, ...],
    artifact_review: Mapping[str, Any],
    created_at: str,
) -> str:
    metadata = {
        "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
        "canonical_surface": canonical_surface,
        "workflow_id": invocation.workflow_id,
        "instance_id": invocation.instance_id,
        "stage_id": stage_id,
        "stage_run_id": invocation.stage_run_id,
        "invocation_id": invocation.invocation_id,
        "gate_id": gate_id,
        "allowed_decisions": list(allowed_decisions),
        "requested_action": requested_action,
        "exact_action": exact_action,
        "evidence_refs": list(evidence_refs),
        "live_operator_surface_allowed": True,
        "public_publish_blocked": True,
        "created_at": created_at,
    }
    if artifact_review:
        metadata["artifact_review"] = _artifact_review_metadata(artifact_review)
    frontmatter = "\n".join(
        f"{key}: {json.dumps(value, sort_keys=True)}" for key, value in metadata.items()
    )
    evidence_lines = "\n".join(f"- `{ref}`" for ref in evidence_refs) or "- `none`"
    artifact_lines = _render_artifact_review_section(artifact_review)
    decision_lines = "\n".join(f"- [ ] `{decision}`" for decision in allowed_decisions)
    ask = human_ask or "Choose exactly one allowed decision below."
    return "\n".join(
        [
            "---",
            frontmatter,
            "---",
            "",
            f"# {title}",
            "",
            "**LIVE OPERATOR-SURFACE WRITE AUTHORIZED - PUBLIC PUBLISH BLOCKED**",
            "",
            "## Review Context",
            f"- Workflow ID: `{invocation.workflow_id}`",
            f"- Instance ID: `{invocation.instance_id}`",
            f"- Stage ID: `{stage_id}`",
            f"- Stage Run ID: `{invocation.stage_run_id}`",
            f"- Gate ID: `{gate_id or 'not-provided'}`",
            f"- Invocation ID: `{invocation.invocation_id}`",
            f"- Canonical surface: `{canonical_surface}`",
            f"- Human ref: `{human_ref}`",
            f"- Requested action: `{requested_action}`",
            f"- Exact action: `{exact_action}`",
            f"- Action fingerprint: `{action_fingerprint}`",
            f"- Public publish blocked: `true`",
            "",
            *artifact_lines,
            "## Evidence",
            evidence_lines,
            "",
            "## Decision",
            ask,
            "",
            "Check exactly one allowed decision. Comments are context only and do not authorize public publish, deploy, trading, money movement, auth, secrets, destructive changes, or unscoped live mutation.",
            "",
            decision_lines,
            "",
        ]
    )


def _render_telegram_operator_message(packet: Mapping[str, Any]) -> str:
    title = str(packet.get("title") or "OpenClaw operator review")
    ask = str(packet.get("human_ask") or packet.get("ask") or packet.get("body") or "")
    exact_action = str(packet.get("exact_action") or packet.get("requested_action") or "")
    fingerprint = str(packet.get("action_fingerprint") or "")
    decisions = ", ".join(_string_tuple(packet.get("allowed_decisions", ()))) or "none"
    parts = [title]
    if ask:
        parts.append(ask)
    if exact_action:
        parts.append(f"Exact action: {exact_action}")
    if fingerprint:
        parts.append(f"Action fingerprint: {fingerprint}")
    parts.append(f"Allowed decisions: {decisions}")
    parts.append("Live operator-surface send authorized; public publish remains blocked.")
    return "\n".join(parts)


def _non_live_safety_error(
    payload: Mapping[str, Any],
    *,
    require_test_only: bool,
) -> dict[str, Any] | None:
    if bool(payload.get("live_mutation_requested", False)) or bool(
        payload.get("mutation_permission_granted", False)
    ):
        return _live_mutation_error("Surface adapter refused a request that asked for live mutation.")
    if not bool(payload.get("non_live", True)):
        return _live_mutation_error("Surface adapter refused a packet that was not marked non_live.")
    if require_test_only and not bool(payload.get("test_only", False)):
        return {
            "error_class": "test_only_required",
            "message": "Dry-run surface adapter requires test_only true.",
        }
    return None


_UNSAFE_LIVE_SURFACE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("public_publish", r"\b(public\s+publish|publish\s+publicly|substack|medium|social\s+post|public\s+website|public\s+repo\s+release)\b"),
    ("trading_or_money", r"\b(live\s+trade|trade|trading|broker|order\s+placement|order\s+cancellation|position\s+size|money\s+movement|spend|purchase|transfer|billing|invoice)\b"),
    ("auth_or_secret", r"\b(auth|oauth|credential|credentials|secret|token|api\s*key|login|session)\b"),
    ("deploy_or_production", r"\b(deploy|deployment|production\s+mutation|migration|service\s+restart|runtime\s+mutation|oldmac\s+mutation)\b"),
    ("destructive", r"\b(delete|deletion|destroy|destructive|archive\s+without\s+recovery|irreversible|overwrite|prune|cleanup\s+job)\b"),
    ("unscoped_live_mutation", r"\b(unscoped\s+live|any\s+live\s+mutation|arbitrary\s+mutation|mutate\s+anything)\b"),
)


def _live_operator_surface_safety_error(
    payload: Mapping[str, Any],
    *,
    effect_name: str,
) -> dict[str, Any] | None:
    if not bool(payload.get("live_operator_surface_allowed", False)):
        return _live_mutation_error(f"{effect_name} requires live_operator_surface_allowed=True.")
    if bool(payload.get("public_publish_allowed", False)) or payload.get("public_publish_blocked", True) is False:
        return _live_mutation_error(f"{effect_name} refused a packet that did not keep public publish blocked.")
    if bool(payload.get("mutation_permission_granted", False)):
        return _live_mutation_error(f"{effect_name} refused ambiguous mutation permission.")
    unsafe = _unsafe_live_surface_hits(payload)
    if unsafe:
        return {
            "error_class": "unsafe_live_surface_scope_refused",
            "message": f"{effect_name} refused unsafe or out-of-scope live action language.",
            "retryable": False,
            "unsafe_hits": unsafe,
        }
    return None


def _unsafe_live_surface_hits(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    scan_keys = {
        "title",
        "human_ask",
        "ask",
        "body",
        "message",
        "requested_action",
        "exact_action",
        "exact_action_approved",
        "allowed_scope",
        "requested_effects",
        "risk_classes",
        "forbidden_actions",
        "side_effects",
    }
    hits: list[dict[str, str]] = []
    for key in scan_keys:
        if key not in payload:
            continue
        text = _stringify_for_scan(payload[key]).lower()
        if not text:
            continue
        for category, pattern in _UNSAFE_LIVE_SURFACE_PATTERNS:
            if re.search(pattern, text):
                hits.append({"field": key, "category": category})
    return hits


def _stringify_for_scan(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return " ".join(f"{key} {_stringify_for_scan(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringify_for_scan(item) for item in value)
    return str(value)


def _live_mutation_error(message: str) -> dict[str, Any]:
    return {
        "error_class": "live_mutation_refused",
        "message": message,
        "retryable": False,
    }


def _sandbox_configuration_error(
    *,
    mutation_mode: str,
    allow_live_mutation: bool,
    live_effect_name: str,
) -> dict[str, Any] | None:
    if allow_live_mutation:
        return _live_mutation_error(
            f"{live_effect_name} is not allowed by the sandbox surface adapter."
        )
    if mutation_mode not in {"sandbox", "test", "local"}:
        return {
            "error_class": "unknown_mutation_mode",
            "message": (
                "Sandbox surface adapter refused an unknown or live mutation mode: "
                f"{mutation_mode!r}."
            ),
            "retryable": False,
        }
    return None


def _redact_sensitive_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    sensitive_markers = ("token", "secret", "password", "api_key", "apikey", "credential")
    for key, value in payload.items():
        key_text = str(key).lower()
        if any(marker in key_text for marker in sensitive_markers):
            redacted[str(key)] = "<redacted>"
        elif isinstance(value, Mapping):
            redacted[str(key)] = _redact_sensitive_mapping(value)
        else:
            redacted[str(key)] = value
    return redacted


def _redact_sensitive_text(text: str) -> str:
    if not text:
        return ""
    redacted = text
    for pattern in (
        r"(?i)(token|secret|password|api[_-]?key|credential)=\S+",
        r"(?i)(token|secret|password|api[_-]?key|credential):\s*\S+",
    ):
        redacted = re.sub(pattern, r"\1=<redacted>", redacted)
    return redacted


def _redacted_telegram_command(command: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            redacted.append("<redacted-message>")
            skip_next = False
            continue
        redacted.append(part)
        if part == "--message":
            skip_next = True
    return redacted


def _loads_json_mapping(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {"value": payload}


def _decision_outputs(
    *,
    schema: str,
    canonical_surface: str,
    surface_query: Mapping[str, Any],
    decision: str | None,
    source_ref: str,
    transcript_or_message_ref: str,
    checked_decisions: tuple[str, ...] = (),
    allowed_decisions: tuple[str, ...] = (),
    note_action_fingerprint: str | None = None,
    decision_payload: Mapping[str, Any] | None = None,
    test_only: bool = True,
    non_live: bool = True,
    live_operator_surface_allowed: bool = False,
    public_publish_blocked: bool = True,
    live_mutation_performed: bool = False,
    network_call_performed: bool = False,
) -> dict[str, Any]:
    payload = dict(decision_payload or {})
    exact_action = str(
        payload.get("exact_action")
        or surface_query.get("exact_action")
        or surface_query.get("exact_action_approved")
        or surface_query.get("requested_action")
        or ""
    )
    action_fingerprint = str(
        payload.get("action_fingerprint")
        or surface_query.get("expected_action_fingerprint")
        or surface_query.get("action_fingerprint")
        or ""
    )
    gate_id = str(payload.get("gate_id") or surface_query.get("gate_id") or "")
    human_ref = str(payload.get("human_ref") or surface_query.get("human_ref") or "Suman(test)")
    evidence_refs = _string_tuple(
        payload.get("evidence_refs", surface_query.get("evidence_refs", ()))
    )
    resolved_allowed = allowed_decisions or _string_tuple(
        payload.get("allowed_decisions", surface_query.get("allowed_decisions", ()))
    )
    return {
        "schema": schema,
        "canonical_surface": canonical_surface,
        "gate_id": gate_id,
        "human_ref": human_ref,
        "decision": decision,
        "requested_action": str(surface_query.get("requested_action") or exact_action),
        "exact_action_approved": exact_action,
        "action_fingerprint": action_fingerprint,
        "note_action_fingerprint": note_action_fingerprint,
        "evidence_refs": list(evidence_refs),
        "source_ref": source_ref,
        "source_note_path": source_ref,
        "transcript_or_message_ref": transcript_or_message_ref,
        "checked_decisions": list(checked_decisions),
        "allowed_decisions": list(resolved_allowed),
        "decision_payload": _redact_sensitive_mapping(payload),
        "test_only": test_only,
        "non_live": non_live,
        "live_operator_surface_allowed": live_operator_surface_allowed,
        "public_publish_blocked": public_publish_blocked,
        "live_mutation_performed": live_mutation_performed,
        "network_call_performed": network_call_performed,
    }


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return slug or "item"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_action_fingerprint(text: str) -> str:
    match = _FINGERPRINT_RE.search(text)
    return match.group("value").strip() if match else ""


def _extract_allowed_decisions(text: str) -> tuple[str, ...]:
    metadata = _extract_frontmatter(text)
    if "allowed_decisions" in metadata:
        return _string_tuple(metadata["allowed_decisions"])

    decisions: list[str] = []
    in_decision_section = False
    for line in text.splitlines():
        if line.strip() == "## Decision":
            in_decision_section = True
            continue
        if in_decision_section and line.startswith("## "):
            break
        if not in_decision_section:
            continue
        match = _CHECKBOX_RE.match(line)
        if match:
            decisions.append(match.group("label").strip())
    return tuple(decisions)


def _extract_checked_decisions(text: str) -> tuple[str, ...]:
    checked: list[str] = []
    in_decision_section = False
    for line in text.splitlines():
        if line.strip() == "## Decision":
            in_decision_section = True
            continue
        if in_decision_section and line.startswith("## "):
            break
        if not in_decision_section:
            continue
        match = _CHECKBOX_RE.match(line)
        if match and match.group("mark").lower() == "x":
            checked.append(match.group("label").strip())
    return tuple(checked)


def _extract_frontmatter(text: str) -> dict[str, Any]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    metadata: dict[str, Any] = {}
    for line in match.group("body").splitlines():
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        try:
            metadata[key.strip()] = json.loads(raw_value.strip())
        except json.JSONDecodeError:
            metadata[key.strip()] = raw_value.strip()
    return metadata


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
