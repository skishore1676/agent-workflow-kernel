"""AWK-owned completion bridge for migrated OpenClaw Blackboard work IDs.

This module closes the narrow migration gap between OpenClaw's existing
Blackboard acknowledgement loop and AWK's durable workflow ledger. OpenClaw
remains the source of truth for Blackboard cards, handoff files, and reviewer
receipts; AWK owns the workflow instance that records whether a migrated lane
has actually reached a terminal kernel state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent_workflow_kernel import (
    AdapterRegistration,
    AdapterRegistry,
    HumanApprovalReceipt,
    KernelRuntimeConfig,
    LocalFakeRuntimeAdapter,
    StageDef,
    StageType,
    Transition,
    WorkflowDef,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowRunner,
    WorkflowStatus,
    digest_data,
)


OWNED_COMPLETION_WORKFLOW_ID = "openclaw_migrated_lane_completion"
OWNED_COMPLETION_WORKFLOW_VERSION = "0.1.0"
OWNED_COMPLETION_SCHEMA = "openclaw.awk_owned_completion.v1"
DEFAULT_OWNER_ID = "openclaw-owned-completion-bridge"


@dataclass(frozen=True, slots=True)
class OpenClawArtifactEvidence:
    artifact_id: str
    lane_id: str | None
    title: str | None
    record_path: Path | None
    handoff_path: Path | None
    runner_receipt_path: Path | None
    record: Mapping[str, Any] | None = None
    handoff: Mapping[str, Any] | None = None
    runner_receipt: Mapping[str, Any] | None = None

    @property
    def acknowledged(self) -> bool:
        if not isinstance(self.handoff, Mapping):
            return False
        handoff_artifact_id = self.handoff.get("artifact_id")
        if handoff_artifact_id is not None and str(handoff_artifact_id) != self.artifact_id:
            return False
        return (
            str(self.handoff.get("action") or "") == "continue_awk_workflow"
            and str(self.handoff.get("status") or "") == "done"
            and str(self.handoff.get("decision") or "") in {"approved", "acknowledged"}
        )

    @property
    def runner_done(self) -> bool:
        if not isinstance(self.runner_receipt, Mapping):
            return False
        receipt_artifact_id = self.runner_receipt.get("artifact_id")
        if receipt_artifact_id is not None and str(receipt_artifact_id) != self.artifact_id:
            return False
        return str(self.runner_receipt.get("status") or "") == "done"


def owned_completion_workflow() -> WorkflowDef:
    return WorkflowDef(
        id=OWNED_COMPLETION_WORKFLOW_ID,
        version=OWNED_COMPLETION_WORKFLOW_VERSION,
        name="OpenClaw Migrated Lane Completion Bridge",
        owner="awk_openclaw",
        description=(
            "Durable AWK workflow that imports an OpenClaw Blackboard acknowledgement "
            "and verifies the corresponding OpenClaw review-runner receipt before "
            "marking a migrated lane work ID terminal."
        ),
        defaults={
            "policy_class": "read_only_review",
            "capability_policy": {
                "allowed": ("read_openclaw_artifact_record", "import_acknowledgement", "verify_review_runner_receipt"),
                "forbidden": (
                    "write_obsidian",
                    "send_telegram",
                    "public_publish",
                    "mutate_openclaw_runtime",
                    "trade_or_money_action",
                    "auth_or_secret_access",
                    "deploy_or_cron_change",
                    "destructive_action",
                ),
            },
        },
        actors={
            "operator": {"adapter": "human.operator", "role": "suman"},
            "openclaw_runner": {"adapter": "host.openclaw", "role": "main"},
        },
        stages=(
            StageDef(
                id="capture_openclaw_surface_artifact",
                type=StageType.SYSTEM_ACTION,
                adapter="runtime.local_fake",
                outcomes=("captured",),
                inputs={"operation": "invoke", "source": "openclaw_artifact_outbox"},
                actors={"worker": "awk_openclaw"},
                policy={"class": "read_only", "external_effects": False},
            ),
            StageDef(
                id="blackboard_acknowledgement",
                type=StageType.HUMAN_GATE,
                adapter="surface.human_review",
                outcomes=("acknowledged", "needs_follow_up", "blocked"),
                inputs={"decision_action": "continue_awk_workflow"},
                actors={"operator": "Suman"},
                surface={
                    "title": "OpenClaw Blackboard acknowledgement",
                    "human_ask": "Import the checked OpenClaw Blackboard decision for this migrated lane work ID.",
                    "allowed_decisions": ("acknowledged", "needs_follow_up", "blocked"),
                    "evidence_refs": ("openclaw:blackboard", "openclaw:review_decision_handoff"),
                },
                policy={"class": "review_only", "requires_explicit_approval": True, "external_effects": False},
            ),
            StageDef(
                id="verify_openclaw_review_runner",
                type=StageType.SYSTEM_ACTION,
                adapter="runtime.local_fake",
                outcomes=("verified",),
                inputs={"operation": "invoke", "source": "openclaw_agent_review_runner_receipt"},
                actors={"openclaw_runner": "main"},
                policy={"class": "read_only", "external_effects": False},
            ),
        ),
        transitions=(
            Transition(from_stage="capture_openclaw_surface_artifact", on="captured", to_stage="blackboard_acknowledgement"),
            Transition(from_stage="blackboard_acknowledgement", on="acknowledged", to_stage="verify_openclaw_review_runner"),
            Transition(from_stage="blackboard_acknowledgement", on="needs_follow_up", terminal="blocked"),
            Transition(from_stage="blackboard_acknowledgement", on="blocked", terminal="blocked"),
            Transition(from_stage="verify_openclaw_review_runner", on="verified", terminal="done"),
        ),
    )


def run_owned_completion_bridge(
    *,
    ledger_path: str | Path,
    openclaw_root: str | Path,
    cutover_receipt_path: str | Path | None = None,
    artifact_ids: Sequence[str] = (),
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Import OpenClaw acknowledgement state into AWK workflow instances.

    The bridge is intentionally read-only with respect to OpenClaw. It writes
    only the AWK SQLite ledger passed by ``ledger_path``.
    """

    openclaw = Path(openclaw_root).expanduser().resolve()
    ledger_file = Path(ledger_path).expanduser().resolve()
    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = _iso(now)
    evidence_items = discover_openclaw_artifacts(
        openclaw_root=openclaw,
        cutover_receipt_path=Path(cutover_receipt_path).expanduser().resolve()
        if cutover_receipt_path is not None
        else None,
        artifact_ids=artifact_ids,
    )
    workflow = owned_completion_workflow()
    ledger = WorkflowLedger(ledger_file)
    try:
        ledger.initialize()
        results = [
            _run_one_artifact(
                ledger=ledger,
                workflow=workflow,
                evidence=evidence,
                now=timestamp,
            )
            for evidence in evidence_items
        ]
    finally:
        ledger.close()
    summary = {
        "schema": OWNED_COMPLETION_SCHEMA,
        "ok": all(result["status"] == "done" for result in results) if results else False,
        "created_at": timestamp,
        "openclaw_root": str(openclaw),
        "ledger_path": str(ledger_file),
        "workflow_id": workflow.id,
        "workflow_version": workflow.version,
        "artifact_count": len(results),
        "results": results,
    }
    return summary


def discover_openclaw_artifacts(
    *,
    openclaw_root: str | Path,
    cutover_receipt_path: str | Path | None = None,
    artifact_ids: Sequence[str] = (),
) -> list[OpenClawArtifactEvidence]:
    openclaw = Path(openclaw_root).expanduser().resolve()
    ids: dict[str, dict[str, Any]] = {}
    if cutover_receipt_path is not None:
        receipt = _load_json(Path(cutover_receipt_path))
        blackboard = receipt.get("blackboard") if isinstance(receipt, Mapping) else None
        if isinstance(blackboard, Mapping):
            for record in blackboard.get("records") or ():
                if not isinstance(record, Mapping):
                    continue
                artifact_id = str(record.get("artifact_id") or "")
                if artifact_id:
                    ids[artifact_id] = {
                        "lane_id": record.get("lane_id"),
                        "title": record.get("title"),
                    }
    for artifact_id in artifact_ids:
        if artifact_id:
            ids.setdefault(str(artifact_id), {})
    return [_evidence_for_artifact(openclaw, artifact_id, metadata) for artifact_id, metadata in sorted(ids.items())]


def _run_one_artifact(
    *,
    ledger: WorkflowLedger,
    workflow: WorkflowDef,
    evidence: OpenClawArtifactEvidence,
    now: str,
) -> dict[str, Any]:
    instance_id = _instance_id(evidence.artifact_id)
    registry = AdapterRegistry(
        (
            AdapterRegistration.from_runtime_adapter(
                LocalFakeRuntimeAdapter(created_at=now),
                replay_safe=True,
            ),
        )
    )
    kernel = WorkflowKernel(
        ledger,
        workflow,
        KernelRuntimeConfig(owner_id=DEFAULT_OWNER_ID, adapter_registry=registry),
    )
    runner = WorkflowRunner(ledger, owner_id=DEFAULT_OWNER_ID)

    existing = ledger.get_workflow_instance(instance_id)
    if existing is None:
        kernel.start(
            instance_id=instance_id,
            inputs={
                "artifact_id": evidence.artifact_id,
                "lane_id": evidence.lane_id,
                "record_path": _path_or_none(evidence.record_path),
                "handoff_path": _path_or_none(evidence.handoff_path),
                "runner_receipt_path": _path_or_none(evidence.runner_receipt_path),
            },
            idempotency_key=f"openclaw-owned-completion:{evidence.artifact_id}",
            now=now,
        )
        ledger.append_event(
            instance_id=instance_id,
            stage_run_id=None,
            event_type="openclaw_artifact_evidence_captured",
            actor=DEFAULT_OWNER_ID,
            payload=_evidence_payload(evidence),
            created_at=now,
        )

    instance = ledger.get_workflow_instance(instance_id)
    if instance is not None and instance.status == WorkflowStatus.DONE:
        return _result(
            evidence=evidence,
            instance_id=instance_id,
            status="done",
            stop_reason="already_terminal",
            ledger=ledger,
        )

    runner.run_kernel_until_idle(kernel, instance_id=instance_id, now=now)
    waiting = ledger.find_waiting_human_stage_run(instance_id=instance_id)
    if waiting is not None:
        if not evidence.acknowledged:
            return _result(
                evidence=evidence,
                instance_id=instance_id,
                status="waiting_on_human",
                stop_reason="openclaw_acknowledgement_missing",
                ledger=ledger,
            )
        decision = _approval_from_openclaw_handoff(
            ledger=ledger,
            instance_id=instance_id,
            evidence=evidence,
            now=now,
        )
        ingest = kernel.ingest_human_decision(instance_id=instance_id, decision=decision, now=now)
        if ingest.decision == "blocked":
            return _result(
                evidence=evidence,
                instance_id=instance_id,
                status="blocked",
                stop_reason=ingest.failure_summary or "human_decision_blocked",
                ledger=ledger,
            )
        ledger.append_event(
            instance_id=instance_id,
            stage_run_id=waiting.stage_run_id,
            event_type="openclaw_handoff_acknowledgement_imported",
            actor=DEFAULT_OWNER_ID,
            payload={
                "artifact_id": evidence.artifact_id,
                "handoff_path": _path_or_none(evidence.handoff_path),
                "handoff_hash": _hash_file(evidence.handoff_path),
                "decision": "acknowledged",
            },
            created_at=now,
        )

    if not evidence.runner_done:
        return _result(
            evidence=evidence,
            instance_id=instance_id,
            status="waiting_on_openclaw_runner",
            stop_reason="openclaw_runner_done_receipt_missing",
            ledger=ledger,
        )

    ledger.append_event(
        instance_id=instance_id,
        stage_run_id=f"{instance_id}:verify_openclaw_review_runner:1",
        event_type="openclaw_runner_receipt_verified",
        actor=DEFAULT_OWNER_ID,
        payload={
            "artifact_id": evidence.artifact_id,
            "runner_receipt_path": _path_or_none(evidence.runner_receipt_path),
            "runner_receipt_hash": _hash_file(evidence.runner_receipt_path),
            "runner_status": evidence.runner_receipt.get("status") if evidence.runner_receipt else None,
        },
        created_at=now,
    )
    final = runner.run_kernel_until_idle(kernel, instance_id=instance_id, now=now)
    return _result(
        evidence=evidence,
        instance_id=instance_id,
        status=final.status,
        stop_reason=final.stop_reason,
        ledger=ledger,
    )


def _approval_from_openclaw_handoff(
    *,
    ledger: WorkflowLedger,
    instance_id: str,
    evidence: OpenClawArtifactEvidence,
    now: str,
) -> HumanApprovalReceipt:
    gate_event = next(
        event
        for event in ledger.list_events()
        if event["instance_id"] == instance_id and event["event_type"] == "human_gate_waiting"
    )
    payload = gate_event["payload"]
    return HumanApprovalReceipt(
        approval_id=f"openclaw-ack:{evidence.artifact_id}:{digest_data(evidence.handoff or {})[7:19]}",
        gate_id=str(payload["gate_id"]),
        human_ref="Suman",
        canonical_surface="openclaw_blackboard",
        decision="acknowledged",  # type: ignore[arg-type]
        exact_action_approved=str(payload["requested_action"]),
        action_fingerprint=str(payload["action_fingerprint"]),
        evidence_refs=tuple(
            ref
            for ref in (
                f"event:{gate_event['event_id']}",
                _path_or_none(evidence.record_path),
                _path_or_none(evidence.handoff_path),
            )
            if ref
        ),
        constraints={
            "source": "openclaw_blackboard_decision_ingest",
            "artifact_id": evidence.artifact_id,
            "handoff_status": evidence.handoff.get("status") if evidence.handoff else None,
            "handoff_action": evidence.handoff.get("action") if evidence.handoff else None,
        },
        created_at=now,
        transcript_or_message_ref=_path_or_none(evidence.handoff_path),
    )


def _evidence_for_artifact(
    openclaw_root: Path,
    artifact_id: str,
    metadata: Mapping[str, Any],
) -> OpenClawArtifactEvidence:
    record_path = openclaw_root / "workspace-main" / "state" / "artifact_outbox" / "records" / f"{artifact_id}.json"
    handoff_path = openclaw_root / "workspace" / "agents" / "codex" / "handoffs" / "review_decisions" / f"{artifact_id}.json"
    runner_receipt_path, runner_receipt = _latest_runner_receipt(openclaw_root, artifact_id)
    record = _load_json_if_exists(record_path)
    handoff = _load_json_if_exists(handoff_path)
    return OpenClawArtifactEvidence(
        artifact_id=artifact_id,
        lane_id=_string_or_none(metadata.get("lane_id")),
        title=_string_or_none(metadata.get("title")) or _string_or_none(record.get("title") if record else None),
        record_path=record_path if record_path.exists() else None,
        handoff_path=handoff_path if handoff_path.exists() else None,
        runner_receipt_path=runner_receipt_path,
        record=record,
        handoff=handoff,
        runner_receipt=runner_receipt,
    )


def _latest_runner_receipt(openclaw_root: Path, artifact_id: str) -> tuple[Path | None, Mapping[str, Any] | None]:
    receipts = (
        openclaw_root
        / "workspace-main"
        / "state"
        / "agent_review_runner"
        / "receipts"
        / "awk_openclaw"
    )
    candidates = sorted(receipts.glob(f"{artifact_id}-*.json"))
    for path in reversed(candidates):
        data = _load_json_if_exists(path)
        if isinstance(data, Mapping) and data.get("status") == "done":
            return path, data
    if candidates:
        latest = candidates[-1]
        data = _load_json_if_exists(latest)
        return latest, data if isinstance(data, Mapping) else None
    return None, None


def _result(
    *,
    evidence: OpenClawArtifactEvidence,
    instance_id: str,
    status: str,
    stop_reason: str | None,
    ledger: WorkflowLedger,
) -> dict[str, Any]:
    instance = ledger.get_workflow_instance(instance_id)
    stage_rows = ledger.connection.execute(
        "SELECT stage_run_id, stage_id, status, actor_ref FROM stage_runs WHERE instance_id = ? ORDER BY stage_run_id",
        (instance_id,),
    ).fetchall()
    terminal_count = ledger.connection.execute(
        "SELECT COUNT(*) AS count FROM events WHERE instance_id = ? AND event_type = ?",
        (instance_id, "workflow_terminal"),
    ).fetchone()["count"]
    return {
        "artifact_id": evidence.artifact_id,
        "lane_id": evidence.lane_id,
        "title": evidence.title,
        "instance_id": instance_id,
        "status": status,
        "stop_reason": stop_reason,
        "workflow_status": instance.status.value if instance else None,
        "current_stage_id": instance.current_stage_id if instance else None,
        "terminal_event_count": int(terminal_count),
        "record_path": _path_or_none(evidence.record_path),
        "handoff_path": _path_or_none(evidence.handoff_path),
        "runner_receipt_path": _path_or_none(evidence.runner_receipt_path),
        "acknowledged": evidence.acknowledged,
        "runner_done": evidence.runner_done,
        "stage_runs": [dict(row) for row in stage_rows],
    }


def _instance_id(artifact_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.:-]+", "-", artifact_id.strip()).strip("-")
    return f"openclaw-owned:{safe or digest_data(artifact_id)[7:19]}"


def _evidence_payload(evidence: OpenClawArtifactEvidence) -> dict[str, Any]:
    return {
        "artifact_id": evidence.artifact_id,
        "lane_id": evidence.lane_id,
        "title": evidence.title,
        "record_path": _path_or_none(evidence.record_path),
        "handoff_path": _path_or_none(evidence.handoff_path),
        "runner_receipt_path": _path_or_none(evidence.runner_receipt_path),
        "record_hash": _hash_file(evidence.record_path),
        "handoff_hash": _hash_file(evidence.handoff_path),
        "runner_receipt_hash": _hash_file(evidence.runner_receipt_path),
    }


def _load_json(path: Path) -> Mapping[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _load_json_if_exists(path: Path) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    return _load_json(path)


def _hash_file(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return digest_data(json.loads(path.read_text(encoding="utf-8")))


def _path_or_none(path: Path | None) -> str | None:
    return str(path) if path is not None else None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _iso(value: datetime | str | None) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "DEFAULT_OWNER_ID",
    "OWNED_COMPLETION_SCHEMA",
    "OWNED_COMPLETION_WORKFLOW_ID",
    "OWNED_COMPLETION_WORKFLOW_VERSION",
    "OpenClawArtifactEvidence",
    "discover_openclaw_artifacts",
    "owned_completion_workflow",
    "run_owned_completion_bridge",
]
