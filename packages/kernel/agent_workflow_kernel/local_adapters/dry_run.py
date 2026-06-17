"""Dry-run surface adapters (no external effects)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..adapters import (
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
from ..contracts import (
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    ArtifactRef,
    Receipt,
    StageRun,
    to_plain_data,
)

from ._shared import *  # noqa: F401,F403 (shared constants + render helpers)


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
        choice_options = _choice_options_from_packet(surface_query)
        selected_option = _selected_choice_option(decision, choice_options)
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
            "selected_option": selected_option,
            "choice_options": list(choice_options),
            "choice_manifest_hash": surface_query.get("choice_manifest_hash"),
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


