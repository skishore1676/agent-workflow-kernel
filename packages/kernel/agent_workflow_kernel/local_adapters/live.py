"""Live Obsidian markdown surface adapter (guarded real-vault writes)."""

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
        prompt_provenance = _prompt_provenance_from_packet(packet)
        operator_brief = _operator_brief_from_packet(packet)
        choice_options = _choice_options_from_packet(packet)
        choice_manifest_hash = str(packet.get("choice_manifest_hash") or "")
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
            prompt_provenance=prompt_provenance,
            operator_brief=operator_brief,
            choice_options=choice_options,
            choice_manifest_hash=choice_manifest_hash,
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
            "prompt_provenance": prompt_provenance,
            "operator_brief": operator_brief,
            "choice_options": list(choice_options),
            "choice_manifest_hash": choice_manifest_hash or None,
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



