"""OpenClaw Blackboard review pointer adapter.

This adapter is intentionally OpenClaw-specific. The portable kernel should not
know how Northstar's generated Blackboard is refreshed or how artifact-outbox
records are shaped.
"""

from __future__ import annotations

import json
import os
import re
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from agent_workflow_kernel import AdapterFamily, AdapterInvocation, ArtifactRef, Receipt, digest_data, to_plain_data
from agent_workflow_kernel.adapters import (
    ADAPTER_STATUS_BLOCKED,
    ADAPTER_STATUS_SUCCEEDED,
    CapabilitySet,
    SurfaceCapabilityContract,
    make_adapter_receipt,
)


BLACKBOARD_POINTER_RECORD_SCHEMA = "openclaw.blackboard_review_pointer_record.v1"


class RefreshRunner(Protocol):
    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        text: bool,
        capture_output: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        ...


@dataclass(frozen=True, slots=True)
class BlackboardPointerPacket:
    artifact_id: str
    title: str
    review_note: str
    why: str
    next_action: str
    owner: str = "Suman"
    producer: str = "AWK/OpenClaw"
    status: str = "approval_required"
    destination: str = "Northstar Blackboard"
    draft_path: str | None = None
    source_artifact_path: str | None = None
    summary_path: str | None = None
    decision_labels: tuple[str, ...] = ("acknowledged", "needs_follow_up", "blocked")
    lane_id: str | None = None
    gate_id: str | None = None
    action_fingerprint: str | None = None
    receipt_path: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "BlackboardPointerPacket":
        labels = data.get("decision_labels") or data.get("allowed_decisions") or ()
        if isinstance(labels, str):
            labels_tuple = (labels,)
        else:
            labels_tuple = tuple(str(label) for label in labels if str(label).strip())
        return cls(
            artifact_id=_required_text(data, "artifact_id"),
            title=_required_text(data, "title"),
            review_note=_required_text(data, "review_note"),
            why=_required_text(data, "why"),
            next_action=_required_text(data, "next_action"),
            owner=str(data.get("owner") or "Suman"),
            producer=str(data.get("producer") or "AWK/OpenClaw"),
            status=str(data.get("status") or "approval_required"),
            destination=str(data.get("destination") or "Northstar Blackboard"),
            draft_path=_optional_text(data, "draft_path"),
            source_artifact_path=_optional_text(data, "source_artifact_path"),
            summary_path=_optional_text(data, "summary_path"),
            decision_labels=labels_tuple or ("acknowledged", "needs_follow_up", "blocked"),
            lane_id=_optional_text(data, "lane_id"),
            gate_id=_optional_text(data, "gate_id"),
            action_fingerprint=_optional_text(data, "action_fingerprint"),
            receipt_path=_optional_text(data, "receipt_path"),
        )


class OpenClawBlackboardReviewAdapter:
    """Publish and verify OpenClaw Blackboard review pointers."""

    adapter_id = "surface.openclaw.blackboard_review"
    family = AdapterFamily.SURFACE
    operations = ("publish_pointer", "readback", "refresh")

    def __init__(
        self,
        openclaw_root: str | Path,
        vault_root: str | Path,
        *,
        created_at: str | None = None,
        refresh_timeout_seconds: int = 30,
        runner: RefreshRunner | None = None,
    ) -> None:
        self.openclaw_root = Path(openclaw_root).expanduser().resolve()
        self.workspace_main = self.openclaw_root / "workspace-main"
        self.records_dir = self.workspace_main / "state" / "artifact_outbox" / "records"
        self.update_script = self.workspace_main / "scripts" / "update_review_inbox.py"
        self.vault_root = Path(vault_root).expanduser().resolve()
        self.blackboard_path = self.vault_root / "01 Blackboard.md"
        self.created_at = created_at or _now_iso()
        self.refresh_timeout_seconds = refresh_timeout_seconds
        self._runner = runner or subprocess.run

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=AdapterFamily.SURFACE,
            operations=self.operations,
            features=("artifact_outbox_record", "blackboard_refresh", "blackboard_readback"),
            metadata={"record_schema": BLACKBOARD_POINTER_RECORD_SCHEMA},
        )

    def surface_contract(self) -> SurfaceCapabilityContract:
        return SurfaceCapabilityContract(
            surface_kind="openclaw_blackboard",
            mode="live_openclaw_state",
            live_mutation_allowed=True,
            dry_run_only=False,
            readback_required=True,
            decision_ingest_supported=False,
            clear_requires_live_mutation=True,
            external_effects=("write_artifact_outbox_record", "refresh_obsidian_blackboard"),
            receipt_schema=BLACKBOARD_POINTER_RECORD_SCHEMA,
            metadata={
                "openclaw_root": str(self.openclaw_root),
                "vault_root": str(self.vault_root),
                "blackboard": str(self.blackboard_path),
            },
        )

    def publish_pointer(self, invocation: AdapterInvocation, packet: Mapping[str, Any]) -> Receipt:
        if invocation.adapter_family != AdapterFamily.SURFACE:
            raise ValueError("OpenClawBlackboardReviewAdapter requires a surface invocation")
        pointer = BlackboardPointerPacket.from_mapping(packet)
        validation_error = self._validate_publish(pointer)
        if validation_error is not None:
            return self._receipt(
                invocation,
                status=ADAPTER_STATUS_BLOCKED,
                summary=validation_error["message"],
                outputs={"error": validation_error},
                checks_run=("validate_openclaw_paths",),
                residual_risk=validation_error["message"],
                next_action="Fix the OpenClaw Blackboard adapter paths and retry the human-gate publish.",
            )

        record = self._record(pointer)
        record_path = self.records_dir / f"{_safe_id(pointer.artifact_id)}.json"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = record_path.with_suffix(record_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(to_plain_data(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(record_path)

        refresh = self.refresh()
        readback = self.readback({"artifact_id": pointer.artifact_id, "review_note": pointer.review_note})
        status = ADAPTER_STATUS_SUCCEEDED if refresh["status"] == "succeeded" and readback["found"] else ADAPTER_STATUS_BLOCKED
        summary = (
            "OpenClaw Blackboard review pointer published and read back."
            if status == ADAPTER_STATUS_SUCCEEDED
            else "OpenClaw Blackboard review pointer was not verified in Blackboard."
        )
        outputs = {
            "record_path": str(record_path),
            "record_hash": _hash_file(record_path),
            "artifact_id": pointer.artifact_id,
            "blackboard_path": str(self.blackboard_path),
            "blackboard_item_id": f"artifact-{_safe_id(pointer.artifact_id)}",
            "review_note": pointer.review_note,
            "refresh": refresh,
            "readback": readback,
        }
        return self._receipt(
            invocation,
            status=status,
            summary=summary,
            outputs=outputs,
            artifact_refs=(
                ArtifactRef(
                    artifact_id=f"openclaw-blackboard-record-{_safe_id(pointer.artifact_id)}",
                    role="blackboard_pointer_record",
                    uri=str(record_path),
                    content_hash=_hash_file(record_path) or digest_data(record),
                    mime_type="application/json",
                    size_bytes=record_path.stat().st_size,
                    created_by=self.adapter_id,
                ),
            ),
            checks_run=("validate_openclaw_paths", "write_artifact_outbox_record", "refresh_blackboard", "readback_blackboard"),
            residual_risk=None if status == ADAPTER_STATUS_SUCCEEDED else "Blackboard did not include the expected item after refresh.",
            next_action=None if status == ADAPTER_STATUS_SUCCEEDED else "Inspect update_review_inbox output and the generated Blackboard note.",
        )

    def refresh(self) -> dict[str, Any]:
        if not self.update_script.exists():
            return {
                "status": "blocked",
                "returncode": None,
                "error": f"missing update_review_inbox.py: {self.update_script}",
            }
        env = dict(os.environ)
        env["OPENCLAW_OBSIDIAN_VAULT"] = str(self.vault_root)
        cmd = [sys.executable, str(self.update_script.name), "--check-sync", "--validate"]
        try:
            completed = self._runner(
                cmd,
                cwd=str(self.update_script.parent),
                env=env,
                text=True,
                capture_output=True,
                timeout=self.refresh_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "status": "blocked",
                "command": _redacted_command(cmd),
                "cwd": str(self.update_script.parent),
                "error": str(exc),
            }
        return {
            "status": "succeeded" if completed.returncode == 0 else "blocked",
            "command": _redacted_command(cmd),
            "cwd": str(self.update_script.parent),
            "returncode": completed.returncode,
            "stdout": _short_text(completed.stdout),
            "stderr": _short_text(completed.stderr),
        }

    def readback(self, surface_ref: Mapping[str, Any]) -> dict[str, Any]:
        artifact_id = str(surface_ref.get("artifact_id") or "")
        review_note = str(surface_ref.get("review_note") or "")
        item_id = f"artifact-{_safe_id(artifact_id)}" if artifact_id else ""
        if not self.blackboard_path.exists():
            return {
                "found": False,
                "blackboard_path": str(self.blackboard_path),
                "blackboard_hash": None,
                "reason": "blackboard_missing",
            }
        text = self.blackboard_path.read_text(encoding="utf-8")
        review_rel = self._vault_relative_text(review_note) if review_note else ""
        found = bool(item_id and item_id in text and (not review_rel or review_rel in text))
        return {
            "found": found,
            "blackboard_path": str(self.blackboard_path),
            "blackboard_hash": digest_data(text),
            "blackboard_item_id": item_id,
            "review_note_rel": review_rel,
        }

    def _validate_publish(self, pointer: BlackboardPointerPacket) -> dict[str, Any] | None:
        if not self.workspace_main.exists():
            return {"error_class": "openclaw_root_missing", "message": f"missing workspace-main: {self.workspace_main}"}
        if not self.update_script.exists():
            return {"error_class": "blackboard_refresh_script_missing", "message": f"missing refresh script: {self.update_script}"}
        if not self.vault_root.exists():
            return {"error_class": "vault_root_missing", "message": f"missing vault root: {self.vault_root}"}
        note_path = self._vault_path(pointer.review_note)
        if note_path is None:
            return {"error_class": "review_note_outside_vault", "message": "review_note must stay inside the configured vault"}
        if not note_path.exists():
            return {"error_class": "review_note_missing", "message": f"review note does not exist: {note_path}"}
        if _unsafe_record_id(pointer.artifact_id):
            return {"error_class": "unsafe_artifact_id", "message": "artifact_id must contain at least one safe character"}
        return None

    def _record(self, pointer: BlackboardPointerPacket) -> dict[str, Any]:
        note_path = self._vault_path(pointer.review_note)
        note_hash = _hash_file(note_path) if note_path is not None else None
        return {
            "schema": BLACKBOARD_POINTER_RECORD_SCHEMA,
            "artifact_id": pointer.artifact_id,
            "artifact_type": "awk_human_gate_review",
            "status": pointer.status,
            "title": pointer.title,
            "why": pointer.why,
            "next": pointer.next_action,
            "owner": pointer.owner,
            "producer": pointer.producer,
            "from_agent": pointer.producer,
            "destination": pointer.destination,
            "review_note": pointer.review_note,
            "draft_path": pointer.draft_path or pointer.review_note,
            "source_artifact_path": pointer.source_artifact_path,
            "summary_path": pointer.summary_path,
            "created_at": self.created_at,
            "updated_at": self.created_at,
            "decision_labels": list(pointer.decision_labels),
            "surfaces": ["obsidian_note", "blackboard"],
            "file": {
                "path": str(note_path) if note_path is not None else pointer.review_note,
                "hash": note_hash,
                "size_bytes": note_path.stat().st_size if note_path is not None and note_path.exists() else None,
                "verified_at": self.created_at,
            },
            "awk": {
                "lane_id": pointer.lane_id,
                "gate_id": pointer.gate_id,
                "action_fingerprint": pointer.action_fingerprint,
                "receipt_path": pointer.receipt_path,
            },
            "notes": "Generated by AWK OpenClaw Blackboard adapter after publishing the live review note.",
        }

    def _vault_path(self, value: str) -> Path | None:
        raw = value.removeprefix("file://").strip()
        path = Path(raw).expanduser()
        candidate = path if path.is_absolute() else self.vault_root / path
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.vault_root)
        except ValueError:
            return None
        return resolved

    def _vault_relative_text(self, value: str) -> str:
        path = self._vault_path(value)
        if path is None:
            return value
        return path.relative_to(self.vault_root).as_posix()

    def _receipt(
        self,
        invocation: AdapterInvocation,
        *,
        status: str,
        summary: str,
        outputs: Mapping[str, Any],
        artifact_refs: tuple[ArtifactRef, ...] = (),
        checks_run: tuple[str, ...] = (),
        residual_risk: str | None = None,
        next_action: str | None = None,
    ) -> Receipt:
        return make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=self.created_at,
            artifact_refs=artifact_refs,
            outputs=outputs,
            checks_run=checks_run,
            policy_snapshot={
                "public_publish_performed": False,
                "trading_or_money_action_performed": False,
                "auth_or_secret_access_performed": False,
                "destructive_action_performed": False,
            },
            residual_risk=residual_risk,
            next_action=next_action,
        )


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"missing required Blackboard pointer field: {key}")
    return value


def _optional_text(data: Mapping[str, Any], key: str) -> str | None:
    value = str(data.get(key) or "").strip()
    return value or None


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-").lower()
    return normalized or "item"


def _unsafe_record_id(value: str) -> bool:
    return _safe_id(value) == "item"


def _hash_file(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _short_text(value: str | None, limit: int = 600) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _redacted_command(cmd: list[str]) -> list[str]:
    return [str(part) for part in cmd]
