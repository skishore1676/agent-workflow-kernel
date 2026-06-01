"""Deterministic local adapter implementations for tests and fixtures."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .adapters import (
    ADAPTER_STATUS_BLOCKED,
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
LOCAL_HUMAN_REVIEW_CARD_SCHEMA = "local_human_review_card.v1"
LOCAL_HUMAN_REVIEW_DECISION_SCHEMA = "local_human_review_decision.v1"

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
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("deterministic", "local", "markdown", "obsidian_compatible", "readback"),
            metadata={
                "root_dir": str(self.root_dir),
                "schema": LOCAL_HUMAN_REVIEW_CARD_SCHEMA,
                "non_live_only": True,
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
    frontmatter = "\n".join(
        f"{key}: {json.dumps(value, sort_keys=True)}" for key, value in metadata.items()
    )
    evidence_lines = "\n".join(f"- `{ref}`" for ref in evidence_refs) or "- `none`"
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
