"""Local markdown human-review surface adapter."""

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
        prompt_provenance = _prompt_provenance_from_packet(packet)
        operator_brief = _operator_brief_from_packet(packet)
        choice_options = _choice_options_from_packet(packet)
        choice_manifest_hash = str(packet.get("choice_manifest_hash") or "")

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
            prompt_provenance=prompt_provenance,
            operator_brief=operator_brief,
            choice_options=choice_options,
            choice_manifest_hash=choice_manifest_hash,
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
            "prompt_provenance": prompt_provenance,
            "operator_brief": operator_brief,
            "choice_options": list(choice_options),
            "choice_manifest_hash": choice_manifest_hash or None,
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
        choice_options = _choice_options_from_packet(surface_query)
        choice_manifest_hash = str(surface_query.get("choice_manifest_hash") or "")
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
        if not choice_options:
            choice_options = _choice_options_from_packet(note_metadata)
        if not choice_manifest_hash:
            choice_manifest_hash = str(note_metadata.get("choice_manifest_hash") or "")

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
            selected_option=_selected_choice_option(decision, choice_options),
            choice_options=choice_options,
            choice_manifest_hash=choice_manifest_hash,
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
        selected_option: Mapping[str, Any] | None = None,
        choice_options: tuple[dict[str, Any], ...] = (),
        choice_manifest_hash: str | None = None,
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
            "selected_option": dict(selected_option or {}),
            "choice_options": list(choice_options),
            "choice_manifest_hash": choice_manifest_hash,
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


