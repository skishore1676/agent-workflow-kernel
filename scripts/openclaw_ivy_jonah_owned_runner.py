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
            "compatibility adapter behind AWK receipts."
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
            Transition(from_stage="run_review_handoff", on="noop", terminal="done"),
            Transition(from_stage="run_review_handoff", on="handled", to_stage="refresh_blackboard"),
            Transition(from_stage="run_review_handoff", on="blocked", terminal="blocked"),
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
    handoff_json = handoff_payload.get("stdout_json")
    if not isinstance(handoff_json, Mapping):
        handoff_json = {}
    handoff_action = str(handoff_json.get("action") or "")
    action = (
        handoff_action
        if handled_receipts and handoff_action
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
    )
    pass_through = {key: handoff_json.get(key) for key in pass_through_keys if handoff_json.get(key)}
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
