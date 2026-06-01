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
import sqlite3
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

from .mapping import OpenClawIdentityCrosswalk, openclaw_identity_crosswalk_conflicts


OWNED_COMPLETION_WORKFLOW_ID = "openclaw_migrated_lane_completion"
OWNED_COMPLETION_WORKFLOW_VERSION = "0.1.0"
OWNED_COMPLETION_SCHEMA = "openclaw.awk_owned_completion.v1"
OWNED_COMPLETION_SCHEDULER_SCHEMA = "openclaw.awk_owned_completion_scheduler.v1"
DEFAULT_OWNER_ID = "openclaw-owned-completion-bridge"
CROSSWALK_RECORDED_EVENT = "openclaw_identity_crosswalk_recorded"
CROSSWALK_REJECTED_EVENT = "openclaw_identity_crosswalk_rejected"
TERMINAL_WORKFLOW_STATUSES = {"done", "policy_denied", "cancelled"}
NON_RESUMABLE_WORKFLOW_STATUSES = {*TERMINAL_WORKFLOW_STATUSES, "blocked"}


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


def plan_owned_completion_run(
    *,
    ledger_path: str | Path,
    openclaw_root: str | Path,
    cutover_receipt_path: str | Path | None = None,
    artifact_ids: Sequence[str] = (),
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Return a scheduler-safe no-op plan for OpenClaw owned completion.

    Planning only reads OpenClaw files and an existing AWK ledger. If the ledger
    path does not exist, it is not created.
    """

    openclaw = Path(openclaw_root).expanduser().resolve()
    ledger_file = Path(ledger_path).expanduser().resolve()
    timestamp = _iso(now)
    evidence_items = discover_openclaw_artifacts(
        openclaw_root=openclaw,
        cutover_receipt_path=Path(cutover_receipt_path).expanduser().resolve()
        if cutover_receipt_path is not None
        else None,
        artifact_ids=artifact_ids,
    )
    workflow = owned_completion_workflow()
    results = [
        _planned_result(
            workflow=workflow,
            evidence=evidence,
            ledger_path=ledger_file,
        )
        for evidence in evidence_items
    ]
    runnable = [
        result
        for result in results
        if result["planned_action"] in {"create_or_resume", "resume"}
        and result["predicted_stop_reason"] != "openclaw_acknowledgement_missing"
    ]
    return {
        "schema": OWNED_COMPLETION_SCHEDULER_SCHEMA,
        "ok": True,
        "mode": "plan",
        "dry_run": True,
        "read_only": True,
        "live_mutation_enabled": False,
        "created_at": timestamp,
        "openclaw_root": str(openclaw),
        "ledger_path": str(ledger_file),
        "ledger_exists": ledger_file.exists(),
        "workflow_id": workflow.id,
        "workflow_version": workflow.version,
        "artifact_count": len(results),
        "runnable_count": len(runnable),
        "openclaw_write_count": 0,
        "ledger_write_enabled": False,
        "summary_write_only": True,
        "results": results,
    }


def run_owned_completion_scheduler(
    *,
    ledger_path: str | Path,
    openclaw_root: str | Path,
    cutover_receipt_path: str | Path | None = None,
    artifact_ids: Sequence[str] = (),
    run: bool = False,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Scheduler entry point; defaults to no-op planning."""

    if not run:
        return plan_owned_completion_run(
            ledger_path=ledger_path,
            openclaw_root=openclaw_root,
            cutover_receipt_path=cutover_receipt_path,
            artifact_ids=artifact_ids,
            now=now,
        )
    summary = run_owned_completion_bridge(
        ledger_path=ledger_path,
        openclaw_root=openclaw_root,
        cutover_receipt_path=cutover_receipt_path,
        artifact_ids=artifact_ids,
        now=now,
    )
    summary.update(
        {
            "mode": "run",
            "dry_run": False,
            "read_only": True,
            "live_mutation_enabled": False,
            "openclaw_write_count": 0,
            "ledger_write_enabled": True,
            "summary_write_only": True,
        }
    )
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

    identity_mismatches = _source_identity_mismatches(evidence)
    if identity_mismatches:
        return _result(
            evidence=evidence,
            instance_id=instance_id,
            status="identity_mismatch",
            stop_reason="openclaw_identity_crosswalk_mismatch",
            ledger=ledger,
            now=now,
            crosswalk_errors=identity_mismatches,
        )

    instance = ledger.get_workflow_instance(instance_id)
    if instance is not None and instance.status == WorkflowStatus.DONE:
        return _result(
            evidence=evidence,
            instance_id=instance_id,
            status="done",
            stop_reason="already_terminal",
            ledger=ledger,
            now=now,
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
                now=now,
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
                now=now,
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
            now=now,
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
        now=now,
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
    now: str,
    crosswalk_errors: Sequence[Mapping[str, Any]] = (),
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
    terminal_event = _terminal_event(ledger, instance_id)
    crosswalk = _identity_crosswalk_for_result(
        evidence=evidence,
        instance_id=instance_id,
        instance=instance,
        terminal_event=terminal_event,
    )
    crosswalk_persistence = _record_identity_crosswalk(
        ledger=ledger,
        instance_id=instance_id,
        crosswalk=crosswalk,
        created_at=now,
        errors=crosswalk_errors,
    )
    result_status = status
    result_stop_reason = stop_reason
    if crosswalk_persistence["status"] == "rejected":
        result_status = "identity_mismatch"
        result_stop_reason = "openclaw_identity_crosswalk_mismatch"
    return {
        "artifact_id": evidence.artifact_id,
        "lane_id": evidence.lane_id,
        "title": evidence.title,
        "instance_id": instance_id,
        "status": result_status,
        "stop_reason": result_stop_reason,
        "workflow_status": instance.status.value if instance else None,
        "current_stage_id": instance.current_stage_id if instance else None,
        "terminal_event_count": int(terminal_count),
        "record_path": _path_or_none(evidence.record_path),
        "handoff_path": _path_or_none(evidence.handoff_path),
        "runner_receipt_path": _path_or_none(evidence.runner_receipt_path),
        "acknowledged": evidence.acknowledged,
        "runner_done": evidence.runner_done,
        "identity_crosswalk": crosswalk.to_metadata(),
        "identity_crosswalk_hash": crosswalk.fingerprint(),
        "identity_crosswalk_status": crosswalk_persistence["status"],
        "identity_crosswalk_errors": crosswalk_persistence["errors"],
        "next": _next_from_instance(workflow=owned_completion_workflow(), instance=instance, stage_rows=stage_rows),
        "stage_runs": [dict(row) for row in stage_rows],
    }


def _planned_result(
    *,
    workflow: WorkflowDef,
    evidence: OpenClawArtifactEvidence,
    ledger_path: Path,
) -> dict[str, Any]:
    instance_id = _instance_id(evidence.artifact_id)
    existing = _read_existing_instance(ledger_path, instance_id)
    if existing is None:
        graph_stage = workflow.stages[0]
        planned_action = "create_or_resume"
        workflow_status = None
        current_stage_id = None
        stage_rows: list[Mapping[str, Any]] = []
        terminal_event_count = 0
    else:
        graph_stage = _stage_for_id(workflow, existing.get("current_stage_id")) or workflow.stages[0]
        workflow_status = existing.get("status")
        current_stage_id = existing.get("current_stage_id")
        stage_rows = list(existing.get("stage_runs") or [])
        terminal_event_count = int(existing.get("terminal_event_count") or 0)
        if workflow_status == WorkflowStatus.DONE.value:
            planned_action = "already_terminal"
        elif workflow_status == WorkflowStatus.BLOCKED.value:
            planned_action = "report_blocked"
        elif workflow_status in TERMINAL_WORKFLOW_STATUSES:
            planned_action = "report_terminal"
        else:
            planned_action = "resume"
    predicted_stage = _predicted_stage_after_run(workflow, evidence, workflow_status)
    latest_stage_row = next((row for row in reversed(stage_rows) if row["stage_id"] == graph_stage.id), None)
    return {
        "artifact_id": evidence.artifact_id,
        "lane_id": evidence.lane_id,
        "title": evidence.title,
        "instance_id": instance_id,
        "status": workflow_status or "not_started",
        "planned_action": planned_action,
        "predicted_stop_reason": _predicted_stop_reason(evidence, workflow_status),
        "workflow_status": workflow_status,
        "current_stage_id": current_stage_id,
        "terminal_event_count": terminal_event_count,
        "record_path": _path_or_none(evidence.record_path),
        "handoff_path": _path_or_none(evidence.handoff_path),
        "runner_receipt_path": _path_or_none(evidence.runner_receipt_path),
        "acknowledged": evidence.acknowledged,
        "runner_done": evidence.runner_done,
        "next": _next_stage_payload(
            graph_stage,
            stage_status=latest_stage_row["status"] if latest_stage_row is not None else None,
        ),
        "predicted_next": _next_stage_payload(predicted_stage, stage_status=None) if predicted_stage else None,
        "stage_runs": [dict(row) for row in stage_rows],
    }


def _read_existing_instance(ledger_path: Path, instance_id: str) -> dict[str, Any] | None:
    if not ledger_path.exists():
        return None
    uri = f"file:{ledger_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT instance_id, status, current_stage_id FROM workflow_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if row is None:
            return None
        stage_rows = conn.execute(
            "SELECT stage_run_id, stage_id, status, actor_ref FROM stage_runs WHERE instance_id = ? ORDER BY stage_run_id",
            (instance_id,),
        ).fetchall()
        terminal_count = conn.execute(
            "SELECT COUNT(*) AS count FROM events WHERE instance_id = ? AND event_type = ?",
            (instance_id, "workflow_terminal"),
        ).fetchone()["count"]
        return {
            "instance_id": row["instance_id"],
            "status": row["status"],
            "current_stage_id": row["current_stage_id"],
            "terminal_event_count": int(terminal_count),
            "stage_runs": [dict(stage_row) for stage_row in stage_rows],
        }
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _predicted_stop_reason(evidence: OpenClawArtifactEvidence, workflow_status: str | None) -> str:
    if workflow_status == WorkflowStatus.DONE.value:
        return "already_terminal"
    if workflow_status in NON_RESUMABLE_WORKFLOW_STATUSES:
        return str(workflow_status)
    if not evidence.acknowledged:
        return "openclaw_acknowledgement_missing"
    if not evidence.runner_done:
        return "openclaw_runner_done_receipt_missing"
    return "would_reach_terminal"


def _predicted_stage_after_run(
    workflow: WorkflowDef,
    evidence: OpenClawArtifactEvidence,
    workflow_status: str | None,
) -> StageDef | None:
    if workflow_status in NON_RESUMABLE_WORKFLOW_STATUSES:
        return None
    if not evidence.acknowledged:
        return _stage_for_id(workflow, "blackboard_acknowledgement")
    if not evidence.runner_done:
        return _stage_for_id(workflow, "verify_openclaw_review_runner")
    return None


def _next_from_instance(
    *,
    workflow: WorkflowDef,
    instance: Any,
    stage_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if instance is None or instance.current_stage_id is None:
        return None
    stage = _stage_for_id(workflow, instance.current_stage_id)
    if stage is None:
        return None
    row = next((row for row in reversed(stage_rows) if row["stage_id"] == stage.id), None)
    return _next_stage_payload(stage, stage_status=row["status"] if row is not None else None)


def _stage_for_id(workflow: WorkflowDef, stage_id: Any) -> StageDef | None:
    if stage_id is None:
        return None
    text = str(stage_id)
    return next((stage for stage in workflow.stages if stage.id == text), None)


def _next_stage_payload(stage: StageDef, *, stage_status: str | None) -> dict[str, Any]:
    actor_refs = tuple(str(value) for value in stage.actors.values())
    owner = actor_refs[0] if actor_refs else None
    return {
        "stage_id": stage.id,
        "stage_type": stage.type.value,
        "owner": owner,
        "actor_refs": list(actor_refs),
        "status": stage_status,
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


def _identity_crosswalk_for_result(
    *,
    evidence: OpenClawArtifactEvidence,
    instance_id: str,
    instance: Any,
    terminal_event: Mapping[str, Any] | None,
) -> OpenClawIdentityCrosswalk:
    terminal_payload = terminal_event.get("payload") if isinstance(terminal_event, Mapping) else None
    return OpenClawIdentityCrosswalk(
        crosswalk_id=_crosswalk_id(instance_id, evidence.artifact_id),
        awk_instance_id=instance_id,
        workflow_id=OWNED_COMPLETION_WORKFLOW_ID,
        workflow_version=OWNED_COMPLETION_WORKFLOW_VERSION,
        openclaw_artifact_id=evidence.artifact_id,
        current_stage_id=instance.current_stage_id if instance else None,
        terminal_stage_id=terminal_payload.get("from_stage") if isinstance(terminal_payload, Mapping) else None,
        lane_id=evidence.lane_id,
        openclaw_artifact_record_path=_path_or_none(evidence.record_path),
        handoff_path=_path_or_none(evidence.handoff_path),
        runner_receipt_path=_path_or_none(evidence.runner_receipt_path),
        work_ledger_id=_first_source_string(evidence, "work_ledger_id"),
        work_id=_first_source_string(evidence, "work_id"),
        work_item_id=_first_source_string(evidence, "work_item_id"),
        work_ledger_handoff_id=_first_source_string(evidence, "handoff_id"),
        work_ledger_receipt_id=_first_source_string(evidence, "receipt_id"),
        source_hashes={
            "openclaw_artifact_record": _hash_file(evidence.record_path),
            "openclaw_handoff": _hash_file(evidence.handoff_path),
            "openclaw_runner_receipt": _hash_file(evidence.runner_receipt_path),
        },
        terminal_event_id=terminal_event.get("event_id") if isinstance(terminal_event, Mapping) else None,
    )


def _record_identity_crosswalk(
    *,
    ledger: WorkflowLedger,
    instance_id: str,
    crosswalk: OpenClawIdentityCrosswalk,
    created_at: str,
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate = crosswalk.to_metadata()
    crosswalk_hash = crosswalk.fingerprint()
    if errors:
        payload = {
            "crosswalk_id": crosswalk.crosswalk_id,
            "crosswalk_hash": crosswalk_hash,
            "errors": [dict(error) for error in errors],
            "candidate": candidate,
        }
        rejection_hash = digest_data(payload)
        if not _event_payload_hash_exists(ledger, instance_id, CROSSWALK_REJECTED_EVENT, rejection_hash):
            ledger.append_event(
                instance_id=instance_id,
                stage_run_id=None,
                event_type=CROSSWALK_REJECTED_EVENT,
                actor=DEFAULT_OWNER_ID,
                payload={**payload, "rejection_hash": rejection_hash},
                created_at=created_at,
            )
        return {"status": "rejected", "errors": [dict(error) for error in errors]}

    recorded_events = _crosswalk_recorded_events(ledger, instance_id)
    for event in recorded_events:
        payload = event["payload"]
        if payload.get("crosswalk_hash") == crosswalk_hash:
            return {"status": "already_recorded", "errors": []}

    if recorded_events:
        existing = recorded_events[-1]["payload"].get("crosswalk")
        if isinstance(existing, Mapping):
            conflicts = openclaw_identity_crosswalk_conflicts(existing, candidate)
            if conflicts:
                payload = {
                    "crosswalk_id": crosswalk.crosswalk_id,
                    "crosswalk_hash": crosswalk_hash,
                    "errors": list(conflicts),
                    "candidate": candidate,
                    "existing": existing,
                }
                rejection_hash = digest_data(payload)
                if not _event_payload_hash_exists(ledger, instance_id, CROSSWALK_REJECTED_EVENT, rejection_hash):
                    ledger.append_event(
                        instance_id=instance_id,
                        stage_run_id=None,
                        event_type=CROSSWALK_REJECTED_EVENT,
                        actor=DEFAULT_OWNER_ID,
                        payload={**payload, "rejection_hash": rejection_hash},
                        created_at=created_at,
                    )
                return {"status": "rejected", "errors": list(conflicts)}

    ledger.append_event(
        instance_id=instance_id,
        stage_run_id=None,
        event_type=CROSSWALK_RECORDED_EVENT,
        actor=DEFAULT_OWNER_ID,
        payload={
            "crosswalk_id": crosswalk.crosswalk_id,
            "crosswalk_hash": crosswalk_hash,
            "crosswalk": candidate,
        },
        created_at=created_at,
    )
    return {"status": "recorded", "errors": []}


def _source_identity_mismatches(evidence: OpenClawArtifactEvidence) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for source_name, source in (
        ("openclaw_artifact_record", evidence.record),
        ("openclaw_handoff", evidence.handoff),
        ("openclaw_runner_receipt", evidence.runner_receipt),
    ):
        if not isinstance(source, Mapping):
            continue
        for key in ("artifact_id", "openclaw_artifact_id"):
            actual = source.get(key)
            if actual is not None and str(actual) != evidence.artifact_id:
                mismatches.append(
                    {
                        "source": source_name,
                        "field": key,
                        "expected": evidence.artifact_id,
                        "actual": actual,
                    }
                )
        actual_lane = source.get("lane_id")
        if evidence.lane_id is not None and actual_lane is not None and str(actual_lane) != evidence.lane_id:
            mismatches.append(
                {
                    "source": source_name,
                    "field": "lane_id",
                    "expected": evidence.lane_id,
                    "actual": actual_lane,
                }
            )
    return mismatches


def _terminal_event(ledger: WorkflowLedger, instance_id: str) -> Mapping[str, Any] | None:
    for event in reversed(ledger.list_events()):
        if event["instance_id"] == instance_id and event["event_type"] == "workflow_terminal":
            return event
    return None


def _crosswalk_recorded_events(ledger: WorkflowLedger, instance_id: str) -> list[dict[str, Any]]:
    return [
        event
        for event in ledger.list_events()
        if event["instance_id"] == instance_id and event["event_type"] == CROSSWALK_RECORDED_EVENT
    ]


def _event_payload_hash_exists(
    ledger: WorkflowLedger,
    instance_id: str,
    event_type: str,
    payload_hash: str,
) -> bool:
    for event in ledger.list_events():
        if event["instance_id"] != instance_id or event["event_type"] != event_type:
            continue
        payload = event["payload"]
        if payload.get("rejection_hash") == payload_hash or payload.get("crosswalk_hash") == payload_hash:
            return True
    return False


def _crosswalk_id(instance_id: str, artifact_id: str) -> str:
    return f"openclaw-awk-crosswalk:{digest_data({'instance_id': instance_id, 'artifact_id': artifact_id})[7:19]}"


def _first_source_string(evidence: OpenClawArtifactEvidence, key: str) -> str | None:
    for source in (evidence.record, evidence.handoff, evidence.runner_receipt):
        value = _nested_source_value(source, key)
        if value is not None:
            return value
    return None


def _nested_source_value(source: Mapping[str, Any] | None, key: str) -> str | None:
    if not isinstance(source, Mapping):
        return None
    direct = _string_or_none(source.get(key))
    if direct is not None:
        return direct
    for nested_key in ("work_ledger", "work_ledger_ids"):
        nested = source.get(nested_key)
        if isinstance(nested, Mapping):
            value = _string_or_none(nested.get(key))
            if value is not None:
                return value
    return None


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
    "OWNED_COMPLETION_SCHEDULER_SCHEMA",
    "OWNED_COMPLETION_WORKFLOW_ID",
    "OWNED_COMPLETION_WORKFLOW_VERSION",
    "OpenClawArtifactEvidence",
    "discover_openclaw_artifacts",
    "owned_completion_workflow",
    "plan_owned_completion_run",
    "run_owned_completion_bridge",
    "run_owned_completion_scheduler",
]
