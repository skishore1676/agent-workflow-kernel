#!/usr/bin/env python3
"""AWK-owned OpenClaw Blackboard bus runner.

This runner owns the workflow receipt boundary for Blackboard decision ingestion
and attention publishing. OpenClaw's deterministic scripts remain compatibility
cargo behind the OpenClaw adapter until equivalent portable primitives replace
them.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
KERNEL_PATH = ROOT / "packages" / "kernel"
OPENCLAW_ADAPTER_PATH = ROOT / "packages" / "adapters" / "openclaw"


def _ensure_source_checkout_imports() -> None:
    for package_path in (str(KERNEL_PATH), str(OPENCLAW_ADAPTER_PATH)):
        if package_path not in sys.path:
            sys.path.insert(0, package_path)


try:
    from agent_workflow_kernel import (  # noqa: E402
        AdapterFamily,
        AdapterInvocation,
        FailureClass,
        StageRun,
        StageRunStatus,
        WorkflowInstance,
        WorkflowLedger,
        WorkflowStatus,
        digest_data,
        make_adapter_receipt,
        to_plain_data,
    )
    from agent_workflow_kernel_openclaw import OpenClawBlackboardDecisionLoopAdapter  # noqa: E402
except ModuleNotFoundError:
    _ensure_source_checkout_imports()
    from agent_workflow_kernel import (  # noqa: E402
        AdapterFamily,
        AdapterInvocation,
        FailureClass,
        StageRun,
        StageRunStatus,
        WorkflowInstance,
        WorkflowLedger,
        WorkflowStatus,
        digest_data,
        make_adapter_receipt,
        to_plain_data,
    )
    from agent_workflow_kernel_openclaw import OpenClawBlackboardDecisionLoopAdapter  # noqa: E402


SCHEMA = "openclaw.awk_blackboard_bus_runner.v1"
WORKFLOW_ID = "openclaw_blackboard_bus"
WORKFLOW_VERSION = "0.1.0"
RUNNER_OWNER = "awk.openclaw.blackboard_bus"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_slug(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def invocation(
    *,
    instance_id: str,
    stage_id: str,
    stage_run_id: str,
    operation: str,
) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=stage_run_id,
        workflow_id=WORKFLOW_ID,
        instance_id=instance_id,
        stage_run_id=stage_run_id,
        adapter_family=AdapterFamily.HOST,
        adapter_id="host.openclaw.blackboard_decision_loop",
        operation=operation,
    )


def command_json(receipt: Any) -> dict[str, Any]:
    outputs = getattr(receipt, "runtime_provenance", {}).get("outputs", {})
    result = outputs.get("command_result")
    return dict(result.get("parsed_json") or {}) if isinstance(result, dict) else {}


def run_decision_ingest(args: argparse.Namespace) -> dict[str, Any]:
    adapter = OpenClawBlackboardDecisionLoopAdapter(
        args.openclaw_root,
        vault_root=args.vault_root,
        timeout_seconds=args.timeout_seconds,
    )
    instance_id = args.instance_id or run_slug("blackboard-decision-ingest")
    receipts = []
    mode = "dry_run" if args.dry_run else "run"
    ledger = open_ledger(args.ledger, instance_id=instance_id, mode=mode, lane="blackboard_decision_ingest")

    if args.dry_run:
        ingest_receipt = execute_stage(
            ledger,
            instance_id=instance_id,
            stage_id="ingest_decisions_dry_run",
            operation="ingest_decisions",
            call=lambda inv: adapter.ingest_decisions(inv, apply=False, refresh_blackboard=False, validate=False),
        )
        receipts.append(ingest_receipt)
        plan_receipt = execute_stage(
            ledger,
            instance_id=instance_id,
            stage_id="plan_review_runner",
            operation="plan_review_runner",
            call=lambda inv: adapter.plan_review_runner(inv),
        )
        receipts.append(plan_receipt)
        action = "blocked" if any(receipt.status != "succeeded" for receipt in receipts) else "planned"
    else:
        loop_receipt = execute_stage(
            ledger,
            instance_id=instance_id,
            stage_id="run_decision_loop",
            operation="run_decision_loop",
            call=lambda inv: adapter.run_decision_loop(
                inv,
                allow_agent_dispatch=args.allow_agent_dispatch,
                telegram_target=args.telegram_target,
                telegram_account=args.telegram_account,
                review_runner_dispatch=args.review_runner_dispatch,
            ),
        )
        receipts.append(loop_receipt)
        action = "blocked" if loop_receipt.status != "succeeded" else decision_loop_action(command_json(loop_receipt))
    workflow_status = WorkflowStatus.DONE if all(receipt.status == "succeeded" for receipt in receipts) else WorkflowStatus.BLOCKED
    finish_ledger(ledger, instance_id=instance_id, status=workflow_status, action=action)

    return summary_payload(
        instance_id=instance_id,
        mode=mode,
        lane="blackboard_decision_ingest",
        action=action,
        receipts=receipts,
        ledger_path=args.ledger,
        compatibility_cargo=(
            "scripts/legacy/run_blackboard_decision_ingester.openclaw_direct_legacy.sh, "
            "workspace-main/scripts/surfaces/ingest_agent_reviews.py, "
            "workspace-main/scripts/programs/agent_review_runner.py"
        ),
    )


def run_publisher(args: argparse.Namespace) -> dict[str, Any]:
    adapter = OpenClawBlackboardDecisionLoopAdapter(
        args.openclaw_root,
        vault_root=args.vault_root,
        timeout_seconds=args.timeout_seconds,
    )
    instance_id = args.instance_id or run_slug("blackboard-publisher")
    ledger = open_ledger(args.ledger, instance_id=instance_id, mode="dry_run" if args.dry_run else "run", lane="blackboard_publisher")
    if args.dry_run:
        receipt = execute_stage(
            ledger,
            instance_id=instance_id,
            stage_id="publish_attention_dry_run",
            operation="publish_attention",
            call=lambda inv: make_adapter_receipt(
                inv,
                status="succeeded",
                summary="Dry-run planned OpenClaw Blackboard attention handoff publishing without writing the surface.",
                created_at=now_iso(),
                outputs={
                    "command_result": {
                        "parsed_json": {"ok": True, "published": False, "dry_run": True},
                        "command": ["publish_or_research_attention.py", "--if-present", "--validate"],
                    }
                },
                checks_run=("dry_run_publish_attention_plan",),
                policy_snapshot={"dry_run": True, "writes_operator_surface": False},
            ),
        )
    else:
        receipt = execute_stage(
            ledger,
            instance_id=instance_id,
            stage_id="publish_attention",
            operation="publish_attention",
            call=lambda inv: adapter.publish_attention(inv, if_present=True, validate=True, force=args.force),
        )
    parsed = command_json(receipt)
    if receipt.status != "succeeded":
        action = "blocked"
    elif parsed.get("published"):
        action = "published_review_note"
    else:
        action = "noop"
    workflow_status = WorkflowStatus.DONE if receipt.status == "succeeded" else WorkflowStatus.BLOCKED
    finish_ledger(ledger, instance_id=instance_id, status=workflow_status, action=action)
    return summary_payload(
        instance_id=instance_id,
        mode="dry_run" if args.dry_run else "run",
        lane="blackboard_publisher",
        action=action,
        receipts=(receipt,),
        ledger_path=args.ledger,
        compatibility_cargo="workspace-main/scripts/publish_or_research_attention.py",
        surface_ref=(
            parsed.get("review_note") or parsed.get("review_note_path") or parsed.get("review_note_rel")
            if action == "published_review_note"
            else None
        ),
    )


def decision_loop_action(parsed: dict[str, Any]) -> str:
    if not parsed:
        return "completed"
    if parsed.get("published") is True:
        return "published_review_note"
    if parsed.get("action"):
        return str(parsed["action"])
    if parsed.get("direct_loop") is True:
        return "completed"
    return "completed"


def summary_payload(
    *,
    instance_id: str,
    mode: str,
    lane: str,
    action: str,
    receipts: Sequence[Any],
    ledger_path: Path | None,
    compatibility_cargo: str,
    surface_ref: str | None = None,
) -> dict[str, Any]:
    receipt_data = [to_plain_data(receipt) for receipt in receipts]
    ok = all(receipt.get("status") == "succeeded" for receipt in receipt_data)
    terminal = action in {
        "noop",
        "planned",
        "completed",
        "completed_jarvis_review_runner",
        "handled_ivy_writing_ops_review_handoff",
        "prepared_ivy_writing_ops_publish_packet",
        "published_review_note",
    } or not ok
    status = "done" if ok else "blocked"
    return {
        "schema": SCHEMA,
        "ok": ok,
        "mode": mode,
        "lane": lane,
        "workflow_id": WORKFLOW_ID,
        "workflow_version": WORKFLOW_VERSION,
        "workflow_status": status,
        "terminal": terminal,
        "action": action,
        "instance_id": instance_id,
        "surface_ref": surface_ref,
        "ledger_path": str(ledger_path) if ledger_path is not None else None,
        "receipts": receipt_data,
        "receipt_ids": [receipt.get("receipt_id") for receipt in receipt_data],
        "stage_runs": [
            {
                "stage_run_id": receipt.get("stage_run_id"),
                "stage_id": logical_stage_id(str(receipt.get("stage_run_id") or receipt.get("stage_id") or "")),
                "status": receipt.get("status"),
                "receipt_id": receipt.get("receipt_id"),
            }
            for receipt in receipt_data
        ],
        "workflow_definition": {
            "type": "awk_owned_blackboard_bus_wrapper",
            "stages": [
                logical_stage_id(str(receipt.get("stage_run_id") or receipt.get("stage_id") or ""))
                for receipt in receipt_data
            ],
            "terminal_states": ["done", "blocked"],
        },
        "prompt_refs": {},
        "no_prompt_reason": (
            "The Blackboard bus is deterministic surface-control cargo. It consumes "
            "checked review decisions and publishes existing attention handoffs; no "
            "model prompt is used by this wrapper."
        ),
        "policy_boundary": (
            "May refresh Blackboard, ingest checked Obsidian review decisions, route "
            "already-approved safe handoffs, publish review notes, and record receipts. "
            "Launchd wrappers may send Telegram receipts for failures or newly published "
            "operator cards, but Telegram is not domain workflow cargo here. "
            "Must not publish publicly, trade, mutate auth/secrets, deploy, or perform "
            "destructive cleanup."
        ),
        "compatibility_cargo": compatibility_cargo,
        "written_at": now_iso(),
    }


def logical_stage_id(stage_run_id: str) -> str:
    if ":" not in stage_run_id:
        return stage_run_id
    return stage_run_id.rsplit(":", 1)[-1]


def open_ledger(ledger_path: Path | None, *, instance_id: str, mode: str, lane: str) -> WorkflowLedger | None:
    if ledger_path is None:
        return None
    ledger = WorkflowLedger(ledger_path)
    ledger.initialize()
    ledger.insert_workflow_instance(
        WorkflowInstance(
            instance_id=instance_id,
            workflow_def_id=WORKFLOW_ID,
            workflow_version=WORKFLOW_VERSION,
            status=WorkflowStatus.RUNNING,
            current_stage_id=None,
            input_hash=digest_data({"mode": mode, "lane": lane}),
            idempotency_key=instance_id,
        ),
        input_snapshot={"mode": mode, "lane": lane},
        workflow_definition_json=json.dumps(
            {
                "schema": SCHEMA,
                "workflow_id": WORKFLOW_ID,
                "workflow_version": WORKFLOW_VERSION,
                "lane": lane,
                "mode": mode,
            },
            sort_keys=True,
        ),
        workflow_definition_hash=digest_data({"workflow_id": WORKFLOW_ID, "version": WORKFLOW_VERSION, "lane": lane}),
        workflow_source_uri="workflows/openclaw_blackboard_bus.yaml",
    )
    ledger.append_event(
        instance_id=instance_id,
        stage_run_id=None,
        event_type="workflow_started",
        actor=RUNNER_OWNER,
        payload={"lane": lane, "mode": mode},
    )
    return ledger


def execute_stage(
    ledger: WorkflowLedger | None,
    *,
    instance_id: str,
    stage_id: str,
    operation: str,
    call: Any,
) -> Any:
    stage_run_id = f"{instance_id}:{stage_id}"
    inv = invocation(
        instance_id=instance_id,
        stage_id=stage_id,
        stage_run_id=stage_run_id,
        operation=operation,
    )
    if ledger is None:
        return call(inv)
    ledger.update_workflow_instance(
        instance_id=instance_id,
        status=WorkflowStatus.RUNNING,
        current_stage_id=stage_id,
        actor=RUNNER_OWNER,
        payload={"stage_id": stage_id, "operation": operation},
    )
    ledger.insert_stage_run(
        StageRun(
            stage_run_id=stage_run_id,
            instance_id=instance_id,
            stage_id=stage_id,
            status=StageRunStatus.QUEUED,
            adapter_id=inv.adapter_id,
            actor_ref=RUNNER_OWNER,
        ),
        input_hash=digest_data({"operation": operation}),
        idempotency_key=f"{instance_id}:{stage_id}",
    )
    claimed = ledger.claim_next_queued_run(owner_id=RUNNER_OWNER, instance_id=instance_id, lease_seconds=2700)
    if claimed is None:
        raise RuntimeError(f"failed to claim queued stage {stage_id}")
    assert claimed.lease_token is not None
    ledger.mark_stage_run_started(
        stage_run_id=stage_run_id,
        lease_token=claimed.lease_token,
        actor=RUNNER_OWNER,
        idempotency_key=f"{instance_id}:{stage_id}",
        adapter_family=inv.adapter_family.value,
        adapter_id=inv.adapter_id,
        operation=operation,
        request_hash=digest_data(to_plain_data(inv)),
    )
    ledger.record_adapter_invocation_started(
        inv,
        request_hash=digest_data(to_plain_data(inv)),
        actor=RUNNER_OWNER,
        side_effect_scope={"openclaw_blackboard_bus": True},
    )
    receipt = call(inv)
    receipt_hash = digest_data(to_plain_data(receipt))
    ledger.complete_adapter_invocation(
        invocation_id=inv.invocation_id,
        status=receipt.status,
        actor=RUNNER_OWNER,
        response_hash=receipt_hash,
        error_class=None if receipt.status == "succeeded" else "blocked",
        error_summary=None if receipt.status == "succeeded" else receipt.summary,
    )
    ledger.record_receipt(receipt)
    if receipt.status == "succeeded":
        ledger.complete_stage_run(
            stage_run_id=stage_run_id,
            lease_token=claimed.lease_token,
            receipt_id=receipt.receipt_id,
            output_hash=receipt_hash,
            actor=RUNNER_OWNER,
        )
    else:
        ledger.fail_stage_run(
            stage_run_id=stage_run_id,
            lease_token=claimed.lease_token,
            failure_class=FailureClass.DOMAIN_BLOCKED,
            failure_summary=receipt.summary,
            status=StageRunStatus.BLOCKED,
            actor=RUNNER_OWNER,
        )
    return receipt


def finish_ledger(
    ledger: WorkflowLedger | None,
    *,
    instance_id: str,
    status: WorkflowStatus,
    action: str,
) -> None:
    if ledger is None:
        return
    ledger.update_workflow_instance(
        instance_id=instance_id,
        status=status,
        current_stage_id=None,
        actor=RUNNER_OWNER,
        event_type="workflow_terminal",
        payload={"action": action, "status": status.value},
    )
    ledger.close()


def write_summary(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AWK-owned OpenClaw Blackboard bus modes.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--openclaw-root", required=True, type=Path)
        sub.add_argument("--vault-root", type=Path)
        sub.add_argument("--ledger", type=Path)
        sub.add_argument("--summary-json", type=Path)
        sub.add_argument("--instance-id")
        sub.add_argument("--timeout-seconds", type=int, default=2700)
        sub.add_argument("--dry-run", action="store_true")

    ingest = subparsers.add_parser("decision-ingest")
    add_common(ingest)
    ingest.add_argument("--allow-agent-dispatch", action="store_true")
    ingest.add_argument("--telegram-target")
    ingest.add_argument("--telegram-account")
    ingest.add_argument("--review-runner-dispatch", default="agent", choices=("agent", "cron"))

    publisher = subparsers.add_parser("publisher")
    add_common(publisher)
    publisher.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    if args.mode == "decision-ingest":
        payload = run_decision_ingest(args)
    else:
        payload = run_publisher(args)
    write_summary(args.summary_json, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
