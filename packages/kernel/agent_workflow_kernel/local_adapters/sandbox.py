"""Sandbox surface adapters (file-backed Obsidian + Telegram outbox)."""

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
        prompt_provenance = _prompt_provenance_from_packet(packet)
        operator_brief = _operator_brief_from_packet(packet)
        choice_options = _choice_options_from_packet(packet)
        choice_manifest_hash = str(packet.get("choice_manifest_hash") or "")
        # OPERATOR-FACING LABELS ONLY. A genuine gate may be non-test while
        # still fail-closed/non-live; the frontmatter should say exactly that
        # so the operator does not mistake a real approval boundary for a live
        # external action.
        label_test_only = bool(packet.get("test_only", True))
        label_non_live = bool(packet.get("non_live", True))
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
            test_only=label_test_only,
            non_live=label_non_live,
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
            "prompt_provenance": prompt_provenance,
            "operator_brief": operator_brief,
            "choice_options": list(choice_options),
            "choice_manifest_hash": choice_manifest_hash or None,
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


