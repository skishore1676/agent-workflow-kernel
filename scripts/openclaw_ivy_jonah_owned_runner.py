#!/usr/bin/env python3
"""AWK-owned production runner for the OpenClaw Ivy/Jonah editorial lane.

This is the clean-cutover entrypoint for OpenClaw's Ivy/Jonah lane. AWK owns
the workflow instance, receipts, and terminal state. The current domain action
is still delegated to OpenClaw's Work Ledger handler as an explicitly marked
legacy compatibility adapter until that domain executor is replaced.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
KERNEL_PATH = ROOT / "packages" / "kernel"
if str(KERNEL_PATH) not in sys.path:
    sys.path.insert(0, str(KERNEL_PATH))

from agent_workflow_kernel import (  # noqa: E402
    AdapterRegistration,
    AdapterRegistry,
    KernelRuntimeConfig,
    LocalFakeRuntimeAdapter,
    StageDef,
    StageType,
    Transition,
    WorkflowDef,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowRunner,
)


SCHEMA = "openclaw.awk_ivy_jonah_owned_runner.v1"
WORKFLOW_ID = "openclaw_ivy_jonah_owned_cutover"
WORKFLOW_VERSION = "1.0.0"
OWNER_ID = "awk-ivy-jonah-owned-runner"
IVY_RUNTIME_REL = "workspace/agents/ivy_writing_ops"
IVY_REVIEW_HANDOFF_REL = f"{IVY_RUNTIME_REL}/handoffs/review_decisions"
IVY_PROJECTS_REL = f"{IVY_RUNTIME_REL}/projects"
IVY_LEDGER_REL = f"{IVY_RUNTIME_REL}/scripts/or_project_ledger.py"
IVY_ATTENTION_REL = f"{IVY_RUNTIME_REL}/scripts/ivy_writing_ops_v2.py"
IVY_ATTENTION_PUBLISHER_REL = "workspace-main/scripts/surfaces/publish_or_research_attention.py"
DETERMINISTIC_COMPAT_NO_PROMPT_REASON = (
    "Deterministic AWK compatibility wrapper stage; it invokes OpenClaw "
    "Work Ledger cargo and does not render model prompts."
)


class OpenClawIvyJonahCompatibilityAdapter(LocalFakeRuntimeAdapter):
    """Run the legacy OpenClaw Ivy/Jonah domain handler under AWK control."""

    adapter_id = "runtime.openclaw_ivy_jonah_compat"

    def __init__(
        self,
        *,
        openclaw_root: Path,
        stale_minutes: int,
        dry_run: bool,
        created_at: str,
        timeout_seconds: int = 2700,
    ) -> None:
        super().__init__(created_at=created_at)
        self.openclaw_root = openclaw_root
        self.stale_minutes = stale_minutes
        self.dry_run = dry_run
        self.timeout_seconds = timeout_seconds

    def invoke(self, invocation, runtime_input):
        result = super().invoke(invocation, runtime_input)
        stage_id = str(runtime_input["stage"]["id"])
        command_result = self._run_stage(stage_id)
        outcome = command_result["outcome"]
        status = "succeeded" if command_result["ok"] else "blocked"
        return replace(
            result,
            status=status,
            outputs={
                **result.outputs,
                "outcome": outcome,
                "legacy_compatibility_adapter": True,
                "legacy_component": "openclaw.work_ledger.ivy_jonah",
                "openclaw_root": str(self.openclaw_root),
                "dry_run": self.dry_run,
                "command": command_result,
            },
            residual_risk=(
                None
                if command_result["ok"]
                else "OpenClaw legacy Ivy/Jonah compatibility adapter blocked under AWK ownership."
            ),
            next_hint=outcome,
        )

    def _run_stage(self, stage_id: str) -> dict[str, Any]:
        if stage_id == "audit_editorial_path":
            self._ensure_runtime_dirs()
            return self._run_command(
                [
                    sys.executable,
                    "scripts/lib/work_ledger/cli.py",
                    "audit-editorial-path",
                    "--handoff-root",
                    IVY_REVIEW_HANDOFF_REL,
                    "--runtime-root",
                    IVY_RUNTIME_REL,
                    "--stale-minutes",
                    str(self.stale_minutes),
                ],
                success_outcome="ok",
                blocked_outcome="blocked",
            )
        if stage_id == "run_review_handoff":
            if self.dry_run:
                return {
                    "ok": True,
                    "outcome": "noop",
                    "skipped": True,
                    "reason": "dry_run",
                    "stdout_json": {"ok": True, "action": "dry_run"},
                }
            result = self._run_command(
                [
                    sys.executable,
                    "scripts/lib/work_ledger/cli.py",
                    "run-next-or-review-handoff",
                    "--handoff-root",
                    IVY_REVIEW_HANDOFF_REL,
                    "--runtime-root",
                    IVY_RUNTIME_REL,
                ],
                success_outcome="handled",
                blocked_outcome="blocked",
            )
            data = result.get("stdout_json")
            if isinstance(data, Mapping):
                if data.get("ok") is False:
                    result["ok"] = False
                    result["outcome"] = "blocked"
                elif not data.get("action") or data.get("action") == "noop":
                    result["outcome"] = "noop"
                else:
                    result["outcome"] = "handled"
            return result
        if stage_id == "advance_lifecycle":
            if self.dry_run:
                return {
                    "ok": True,
                    "outcome": "noop",
                    "skipped": True,
                    "reason": "dry_run",
                    "stdout_json": {"ok": True, "action": "dry_run"},
                }
            return self._advance_lifecycle()
        if stage_id == "refresh_blackboard":
            if self.dry_run:
                return {
                    "ok": True,
                    "outcome": "refreshed",
                    "skipped": True,
                    "reason": "dry_run",
                    "stdout_json": {"ok": True, "action": "dry_run"},
                }
            return self._run_command(
                [
                    sys.executable,
                    "workspace-main/scripts/surfaces/update_review_inbox.py",
                    "--validate",
                ],
                success_outcome="refreshed",
                blocked_outcome="blocked",
            )
        return {
            "ok": False,
            "outcome": "blocked",
            "error": f"unknown AWK Ivy/Jonah owned-runner stage: {stage_id}",
        }

    def _ensure_runtime_dirs(self) -> None:
        for rel in (
            IVY_REVIEW_HANDOFF_REL,
            f"{IVY_RUNTIME_REL}/handoffs/attention",
            f"{IVY_RUNTIME_REL}/reports/review",
            f"{IVY_RUNTIME_REL}/read_models",
            IVY_PROJECTS_REL,
        ):
            (self.openclaw_root / rel).mkdir(parents=True, exist_ok=True)

    def _advance_lifecycle(self) -> dict[str, Any]:
        self._ensure_runtime_dirs()
        project = self._select_project()
        if not project:
            return {
                "ok": True,
                "outcome": "noop",
                "stdout_json": {"ok": True, "action": "noop", "reason": "no_active_ivy_projects"},
            }
        project_id = str(project.get("id") or "")
        gate = _normalize_gate(str(project.get("gate") or "P1"))
        status = str(project.get("status") or "")
        if status == "needs_suman" or bool(project.get("needs_suman")):
            return self._publish_human_gate(project, reason="project_already_waiting_on_human")
        next_gate = _next_gate(gate)
        if not next_gate:
            return self._publish_human_gate(project, reason="project_at_terminal_review_gate")
        agent_gate = _agent_gate_required(project, gate, next_gate)
        if agent_gate:
            return {
                "ok": True,
                "outcome": "agent_gate_required",
                "stdout_json": {
                    "ok": True,
                    "action": "agent_gate_required",
                    "project_id": project_id,
                    "gate": gate,
                    "next_gate": next_gate,
                    **agent_gate,
                },
            }
        advance = self._run_command(
            [
                sys.executable,
                IVY_LEDGER_REL,
                "--root",
                IVY_RUNTIME_REL,
                "advance",
                "--project",
                project_id,
                "--to",
                next_gate,
                "--why",
                f"AWK lifecycle owner advanced machine-owned {gate}->{next_gate}",
                "--actor",
                OWNER_ID,
            ],
            success_outcome="advanced",
            blocked_outcome="blocked",
        )
        if not advance["ok"]:
            return {
                **advance,
                "stdout_json": {
                    "ok": False,
                    "action": "blocked_ivy_lifecycle_advance",
                    "project_id": project_id,
                    "gate": gate,
                    "next_gate": next_gate,
                    "stderr": advance.get("stderr", ""),
                    "stdout": advance.get("stdout", ""),
                },
            }
        self._refresh_read_models()
        updated = self._load_project(project_id) or project
        if updated.get("needs_suman") or str(updated.get("status") or "") == "needs_suman":
            published = self._publish_human_gate(updated, reason=f"advanced_to_human_gate_{next_gate}")
            data = dict(published.get("stdout_json") or {})
            data.setdefault("advanced_from_gate", gate)
            data.setdefault("advanced_to_gate", next_gate)
            published["stdout_json"] = data
            return published
        artifact = _gate_artifact_path(project_id, next_gate)
        return {
            "ok": True,
            "outcome": "advanced",
            "stdout_json": {
                "ok": True,
                "action": "advanced_ivy_lifecycle_project",
                "project_id": project_id,
                "from_gate": gate,
                "to_gate": next_gate,
                "artifact_path": artifact,
                "next_action": updated.get("next_action"),
                "owner": "machine",
            },
        }

    def _select_project(self) -> dict[str, Any] | None:
        projects: list[dict[str, Any]] = []
        for path in sorted((self.openclaw_root / IVY_PROJECTS_REL).glob("*/project.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            status = str(data.get("status") or "")
            if status in {"active", "needs_suman"} or data.get("needs_suman"):
                projects.append(data)
        if not projects:
            return None
        rank = {"P5": 5, "P4": 4, "P3": 3, "P2": 2, "P1": 1}
        projects.sort(
            key=lambda item: (
                1 if item.get("needs_suman") or str(item.get("status") or "") == "needs_suman" else 0,
                rank.get(_normalize_gate(str(item.get("gate") or "P1")), 0),
                str(item.get("last_touched") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        return projects[0]

    def _load_project(self, project_id: str) -> dict[str, Any] | None:
        path = self.openclaw_root / IVY_PROJECTS_REL / project_id / "project.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _refresh_read_models(self) -> None:
        for cmd in (
            [sys.executable, IVY_LEDGER_REL, "--root", IVY_RUNTIME_REL, "source-intake-plan", "--lookback-days", "30"],
            [sys.executable, IVY_LEDGER_REL, "--root", IVY_RUNTIME_REL, "weekly-post-candidate"],
            [sys.executable, IVY_LEDGER_REL, "--root", IVY_RUNTIME_REL, "lint"],
        ):
            subprocess.run(cmd, cwd=str(self.openclaw_root), text=True, capture_output=True, timeout=self.timeout_seconds, check=False)

    def _publish_human_gate(self, project: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
        project_id = str(project.get("id") or "")
        gate = _normalize_gate(str(project.get("gate") or "P1"))
        artifact = _gate_artifact_path(project_id, gate)
        title = f"Review Ivy Writing Ops {project.get('title') or project_id}"
        summary = str(project.get("ambiguity") or project.get("next_action") or "Ivy Writing Ops needs Suman review.")
        attention = self._run_command(
            [
                sys.executable,
                IVY_ATTENTION_REL,
                "--root",
                IVY_RUNTIME_REL,
                "attention-plan",
                "--title",
                title,
                "--artifact-path",
                artifact,
                "--purpose",
                "critique",
                "--urgency",
                "normal",
                "--approval-required",
                "--summary",
                summary,
            ],
            success_outcome="attention_created",
            blocked_outcome="blocked",
        )
        if not attention["ok"]:
            return {
                **attention,
                "stdout_json": {
                    "ok": False,
                    "action": "blocked_ivy_human_gate_attention",
                    "project_id": project_id,
                    "gate": gate,
                    "artifact_path": artifact,
                    "stderr": attention.get("stderr", ""),
                },
            }
        attention_json = attention.get("stdout_json")
        if not isinstance(attention_json, Mapping):
            attention_json = {}
        attention_path = str(attention_json.get("output_path") or "")
        publish_cmd = [
            sys.executable,
            IVY_ATTENTION_PUBLISHER_REL,
            "--or-root",
            IVY_RUNTIME_REL,
            "--validate",
        ]
        if attention_path:
            publish_cmd.extend(["--attention", attention_path])
        publish = self._run_command(
            publish_cmd,
            success_outcome="human_gate_published",
            blocked_outcome="blocked",
        )
        publish_json = publish.get("stdout_json")
        if not isinstance(publish_json, Mapping):
            publish_json = {}
        ok = bool(publish["ok"]) and bool(publish_json.get("ok", True))
        return {
            **publish,
            "ok": ok,
            "outcome": "human_gate_published" if ok else "blocked",
            "stdout_json": {
                "ok": ok,
                "action": "published_ivy_human_gate",
                "project_id": project_id,
                "gate": gate,
                "artifact_path": artifact,
                "attention_path": attention_path,
                "review_note": publish_json.get("review_note"),
                "review_note_rel": publish_json.get("review_note_rel"),
                "artifact_record": publish_json.get("artifact_record"),
                "already_published": publish_json.get("already_published"),
                "reason": reason,
                "owner": "human",
            },
        }

    def _run_command(
        self,
        cmd: Sequence[str],
        *,
        success_outcome: str,
        blocked_outcome: str,
    ) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                list(cmd),
                cwd=str(self.openclaw_root),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "ok": False,
                "outcome": blocked_outcome,
                "argv": list(cmd),
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "error": str(exc),
            }
        parsed = _parse_json(completed.stdout)
        return {
            "ok": completed.returncode == 0,
            "outcome": success_outcome if completed.returncode == 0 else blocked_outcome,
            "argv": list(cmd),
            "returncode": completed.returncode,
            "stdout": _short(completed.stdout),
            "stderr": _short(completed.stderr),
            "stdout_json": parsed,
        }


def ivy_jonah_owned_workflow() -> WorkflowDef:
    return WorkflowDef(
        id=WORKFLOW_ID,
        version=WORKFLOW_VERSION,
        name="OpenClaw Ivy/Jonah AWK-Owned Cutover Runner",
        owner=OWNER_ID,
        description=(
            "AWK-owned production wrapper for the Ivy/Jonah editorial lane. "
            "The OpenClaw Work Ledger handler is retained only as a legacy "
            "compatibility adapter behind AWK receipts. AWK also owns one-step "
            "Ivy lifecycle advancement and human-gate publication."
        ),
        defaults={
            "policy_class": "internal_generation",
            "timeout_seconds": 2700,
            "retry": {"max_attempts": 1},
        },
        stages=(
            StageDef(
                id="audit_editorial_path",
                type=StageType.AGENT_WORK,
                adapter=OpenClawIvyJonahCompatibilityAdapter.adapter_id,
                outcomes=("ok", "blocked"),
                inputs={"operation": "invoke"},
                actors={"runner": OWNER_ID},
                no_prompt_reason=DETERMINISTIC_COMPAT_NO_PROMPT_REASON,
                policy={"class": "read_only"},
            ),
            StageDef(
                id="run_review_handoff",
                type=StageType.AGENT_WORK,
                adapter=OpenClawIvyJonahCompatibilityAdapter.adapter_id,
                outcomes=("handled", "noop", "blocked"),
                inputs={"operation": "invoke"},
                actors={"runner": OWNER_ID},
                no_prompt_reason=DETERMINISTIC_COMPAT_NO_PROMPT_REASON,
                policy={
                    "class": "internal_generation",
                    "forbidden_actions": (
                        "public_publish",
                        "external_send",
                        "auth_or_secret_access",
                        "trade_or_money_action",
                    ),
                },
            ),
            StageDef(
                id="advance_lifecycle",
                type=StageType.AGENT_WORK,
                adapter=OpenClawIvyJonahCompatibilityAdapter.adapter_id,
                outcomes=("advanced", "human_gate_published", "agent_gate_required", "noop", "blocked"),
                inputs={"operation": "invoke"},
                actors={"runner": OWNER_ID},
                no_prompt_reason=DETERMINISTIC_COMPAT_NO_PROMPT_REASON,
                policy={
                    "class": "internal_generation",
                    "forbidden_actions": (
                        "public_publish",
                        "external_send",
                        "auth_or_secret_access",
                        "trade_or_money_action",
                    ),
                },
            ),
            StageDef(
                id="refresh_blackboard",
                type=StageType.AGENT_WORK,
                adapter=OpenClawIvyJonahCompatibilityAdapter.adapter_id,
                outcomes=("refreshed", "blocked"),
                inputs={"operation": "invoke"},
                actors={"runner": OWNER_ID},
                no_prompt_reason=DETERMINISTIC_COMPAT_NO_PROMPT_REASON,
                policy={"class": "internal_state"},
            ),
        ),
        transitions=(
            Transition(from_stage="audit_editorial_path", on="ok", to_stage="run_review_handoff"),
            Transition(from_stage="audit_editorial_path", on="blocked", terminal="blocked"),
            Transition(from_stage="run_review_handoff", on="noop", to_stage="advance_lifecycle"),
            Transition(from_stage="run_review_handoff", on="handled", to_stage="refresh_blackboard"),
            Transition(from_stage="run_review_handoff", on="blocked", terminal="blocked"),
            Transition(from_stage="advance_lifecycle", on="noop", terminal="done"),
            Transition(from_stage="advance_lifecycle", on="advanced", terminal="done"),
            Transition(from_stage="advance_lifecycle", on="agent_gate_required", terminal="done"),
            Transition(from_stage="advance_lifecycle", on="human_gate_published", to_stage="refresh_blackboard"),
            Transition(from_stage="advance_lifecycle", on="blocked", terminal="blocked"),
            Transition(from_stage="refresh_blackboard", on="refreshed", terminal="done"),
            Transition(from_stage="refresh_blackboard", on="blocked", terminal="blocked"),
        ),
    )


def run_owned_ivy_jonah(
    *,
    openclaw_root: str | Path,
    ledger_path: str | Path,
    stale_minutes: int = 90,
    dry_run: bool = False,
    instance_id: str | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    openclaw = Path(openclaw_root).expanduser().resolve()
    ledger_file = Path(ledger_path).expanduser().resolve()
    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = _iso(now)
    workflow = ivy_jonah_owned_workflow()
    ledger = WorkflowLedger(ledger_file)
    resolved_instance_id = instance_id or f"openclaw-ivy-jonah-owned:{timestamp}:{uuid.uuid4().hex[:12]}"
    try:
        ledger.initialize()
        adapter = OpenClawIvyJonahCompatibilityAdapter(
            openclaw_root=openclaw,
            stale_minutes=stale_minutes,
            dry_run=dry_run,
            created_at=timestamp,
        )
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        kernel = WorkflowKernel(
            ledger,
            workflow,
            KernelRuntimeConfig(owner_id=OWNER_ID, adapter_registry=registry),
        )
        if ledger.get_workflow_instance(resolved_instance_id) is None:
            kernel.start(
                instance_id=resolved_instance_id,
                inputs={
                    "openclaw_root": str(openclaw),
                    "stale_minutes": stale_minutes,
                    "dry_run": dry_run,
                    "legacy_compatibility_adapter": "openclaw.work_ledger.ivy_jonah",
                },
                idempotency_key=resolved_instance_id,
                now=timestamp,
            )
        summary = WorkflowRunner(ledger, owner_id=OWNER_ID).run_kernel_until_idle(
            kernel,
            instance_id=resolved_instance_id,
            now=timestamp,
        )
        return _summary(
            ledger=ledger,
            workflow=workflow,
            instance_id=resolved_instance_id,
            summary=summary,
            openclaw_root=openclaw,
            ledger_path=ledger_file,
            timestamp=timestamp,
            dry_run=dry_run,
        )
    finally:
        ledger.close()


def _summary(
    *,
    ledger: WorkflowLedger,
    workflow: WorkflowDef,
    instance_id: str,
    summary: Any,
    openclaw_root: Path,
    ledger_path: Path,
    timestamp: str,
    dry_run: bool,
) -> dict[str, Any]:
    instance = ledger.get_workflow_instance(instance_id)
    rows = ledger.connection.execute(
        """
        SELECT stage_run_id, stage_id, status, receipt_id, failure_class, failure_summary
        FROM stage_runs
        WHERE instance_id = ?
        ORDER BY created_at, stage_run_id
        """,
        (instance_id,),
    ).fetchall()
    receipts = ledger.connection.execute(
        """
        SELECT receipt_json
        FROM receipts
        WHERE instance_id = ?
        ORDER BY created_at, receipt_id
        """,
        (instance_id,),
    ).fetchall()
    receipt_payloads = [json.loads(row["receipt_json"]) for row in receipts]
    handled_receipts = [
        receipt
        for receipt in receipt_payloads
        if (receipt.get("runtime_provenance") or {}).get("outputs", {}).get("outcome") == "handled"
    ]
    noop_receipts = [
        receipt
        for receipt in receipt_payloads
        if (receipt.get("runtime_provenance") or {}).get("outputs", {}).get("outcome") == "noop"
    ]
    stage_payloads = _stage_command_payloads(receipt_payloads)
    handoff_payload = stage_payloads.get("run_review_handoff") or {}
    lifecycle_payload = stage_payloads.get("advance_lifecycle") or {}
    handoff_json = _mapping_or_empty(handoff_payload.get("stdout_json"))
    lifecycle_json = _mapping_or_empty(lifecycle_payload.get("stdout_json"))
    handoff_action = str(handoff_json.get("action") or "")
    lifecycle_action = str(lifecycle_json.get("action") or "")
    action = (
        handoff_action
        if handled_receipts and handoff_action
        else lifecycle_action
        if lifecycle_action and lifecycle_action != "dry_run"
        else "noop"
        if noop_receipts
        else "blocked"
    )
    pass_through_keys = (
        "project_id",
        "handoff_path",
        "operator_summary_path",
        "obsidian_path",
        "browser_plan_path",
        "publish_ready_path",
        "publish_staging_path",
        "artifact_path",
        "attention_path",
        "review_note",
        "review_note_rel",
        "artifact_record",
        "from_gate",
        "to_gate",
        "gate",
        "next_action",
        "owner",
        "reason",
    )
    pass_through_source = {**handoff_json, **lifecycle_json}
    pass_through = {key: pass_through_source.get(key) for key in pass_through_keys if pass_through_source.get(key)}
    stage_order = {stage.id: index for index, stage in enumerate(workflow.stages)}
    stage_runs = sorted(
        [dict(row) for row in rows],
        key=lambda row: (stage_order.get(str(row["stage_id"]), 999), str(row["stage_run_id"])),
    )
    return {
        "schema": SCHEMA,
        "ok": getattr(summary, "status", None) == "done",
        "mode": "dry_run" if dry_run else "run",
        "dry_run": dry_run,
        "created_at": timestamp,
        "workflow_id": workflow.id,
        "workflow_version": workflow.version,
        "instance_id": instance_id,
        "status": getattr(summary, "status", None),
        "stop_reason": getattr(summary, "stop_reason", None),
        "workflow_status": instance.status.value if instance is not None else None,
        "current_stage_id": instance.current_stage_id if instance is not None else None,
        "action": action,
        "compatibility_action": "handled" if handled_receipts else "noop" if noop_receipts else "blocked",
        "runner_result": dict(handoff_json),
        "lifecycle_result": dict(lifecycle_json),
        **pass_through,
        "openclaw_root": str(openclaw_root),
        "ledger_path": str(ledger_path),
        "legacy_compatibility_adapter": "openclaw.work_ledger.ivy_jonah",
        "external_publish_performed": False,
        "public_publish_allowed": False,
        "stage_runs": stage_runs,
        "receipt_ids": [receipt["receipt_id"] for receipt in receipt_payloads],
        "receipts": [
            {
                "stage_id": receipt.get("stage_id"),
                "status": receipt.get("status"),
                "adapter_id": (receipt.get("runtime_provenance") or {}).get("adapter_id"),
                "operation": (receipt.get("runtime_provenance") or {}).get("operation"),
                "outcome": (receipt.get("runtime_provenance") or {}).get("outputs", {}).get("outcome"),
                "legacy_compatibility_adapter": (receipt.get("runtime_provenance") or {})
                .get("outputs", {})
                .get("legacy_compatibility_adapter"),
            }
            for receipt in receipt_payloads
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AWK-owned Ivy/Jonah OpenClaw cutover runner.")
    parser.add_argument("--openclaw-root", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--stale-minutes", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--instance-id")
    parser.add_argument("--summary-json", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = run_owned_ivy_jonah(
        openclaw_root=args.openclaw_root,
        ledger_path=args.ledger,
        stale_minutes=args.stale_minutes,
        dry_run=args.dry_run,
        instance_id=args.instance_id,
    )
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["ok"] else 1


def _iso(now: datetime | str | None) -> str:
    if isinstance(now, str):
        return now
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_gate(value: str) -> str:
    value = value.strip().upper()
    return {"M1": "P1", "M2": "P2", "M3": "P3", "M4": "P4", "M5": "P5"}.get(value, value or "P1")


def _next_gate(gate: str) -> str | None:
    gates = ["P1", "P2", "P3", "P4", "P5"]
    gate = _normalize_gate(gate)
    try:
        index = gates.index(gate)
    except ValueError:
        return None
    return gates[index + 1] if index + 1 < len(gates) else None


def _gate_artifact_path(project_id: str, gate: str) -> str:
    names = {
        "P1": "p1_scout.md",
        "P2": "p2_deep_dive.md",
        "P3": "p3_research_brief.md",
        "P4": "p4_draft_package.md",
        "P5": "p5_final_review.md",
    }
    return f"projects/{project_id}/{names.get(_normalize_gate(gate), 'project.json')}"


def _agent_gate_required(project: Mapping[str, Any], gate: str, next_gate: str) -> dict[str, str] | None:
    if (
        _normalize_gate(gate) == "P4"
        and _normalize_gate(next_gate) == "P5"
        and str(project.get("target_channel") or "") == "substack_medium"
    ):
        return {
            "owner": "jonah_editor",
            "reason": "substack_medium P4 requires Jonah editor review before P5",
            "next_action": "delegate Jonah editor review before P5 final refinement",
        }
    return None


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _short(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _stage_command_payloads(receipt_payloads: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for receipt in receipt_payloads:
        outputs = (receipt.get("runtime_provenance") or {}).get("outputs", {})
        if not isinstance(outputs, Mapping):
            continue
        command = outputs.get("command")
        if isinstance(command, Mapping):
            payloads[str(receipt.get("stage_id") or "")] = dict(command)
    return payloads


if __name__ == "__main__":
    raise SystemExit(main())
