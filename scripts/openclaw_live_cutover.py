#!/usr/bin/env python3
"""Prepare operator-visible OpenClaw AWK live cutover review artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
KERNEL_PATH = ROOT / "packages" / "kernel"
for package_path in (str(KERNEL_PATH), str(ROOT / "scripts")):
    if package_path not in sys.path:
        sys.path.insert(0, package_path)

from agent_workflow_kernel import (  # noqa: E402
    AUTOMATED_SUMAN_REVIEWER_HUMAN_REF,
    AdapterFamily,
    AdapterInvocation,
    LiveObsidianMarkdownSurfaceAdapter,
    SandboxObsidianMarkdownSurfaceAdapter,
    SandboxTelegramOutboxSurfaceAdapter,
    digest_data,
    to_plain_data,
)
from agent_workflow_kernel.local_adapters import OpenClawTelegramSurfaceAdapter  # noqa: E402
from openclaw_auto_review_packet import auto_review_packet  # noqa: E402
from openclaw_two_lane_onboarding import build_onboarding_packet  # noqa: E402


CUTOVER_SCHEMA = "workflow.kernel.openclaw-live-cutover-receipt.v1"
DETERMINISTIC_CREATED_AT = "2000-01-01T00:00:00Z"
LANE_LABELS = {
    "ivy": "Ivy/Jonah editorial",
    "weekly": "Jarvis weekly update",
}
FORBIDDEN_ACTIONS = (
    "public_publish",
    "trade_or_money_action",
    "auth_or_secret_access",
    "deploy_or_cron_change",
    "oldmac_mutation",
    "openclaw_runtime_mutation",
    "destructive_action",
)
GENERATED_NAMES = (
    "input_packet",
    "auto_review",
    "obsidian-sandbox",
    "telegram-outbox",
    "cutover_receipt.json",
    "cutover_receipt.md",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    telegram_target = args.telegram_target
    if args.telegram_target_env:
        telegram_target = os.environ.get(args.telegram_target_env)
    try:
        receipt = build_live_cutover(
            packet_dir=args.packet_dir,
            ivy_fixture=args.ivy_fixture,
            weekly_fixture=args.weekly_fixture,
            vault_root=args.vault_root,
            obsidian_prefix=args.obsidian_prefix,
            telegram_target=telegram_target,
            telegram_account=args.telegram_account,
            allow_live_obsidian=args.allow_live_obsidian,
            allow_live_telegram=args.allow_live_telegram,
            output_dir=args.output_dir,
            telegram_send_cmd=args.telegram_send_cmd,
            telegram_delivery=args.telegram_delivery,
        )
    except Exception as exc:
        print(_canonical_json({"ok": False, "error": str(exc)}), file=sys.stderr, end="")
        return 1

    print(
        _canonical_json(
            {
                "ok": receipt["status"] == "ready",
                "status": receipt["status"],
                "receipt_json": receipt["artifacts"]["receipt_json"],
                "receipt_md": receipt["artifacts"]["receipt_md"],
                "telegram_status": receipt["telegram"]["send_result"]["status"],
                "obsidian_notes": [note.get("note_path") for note in receipt["obsidian"].get("notes", [])],
            }
        ),
        end="",
    )
    return 0 if receipt["status"] == "ready" else 1


def build_live_cutover(
    *,
    packet_dir: str | Path | None = None,
    ivy_fixture: str | Path | None = None,
    weekly_fixture: str | Path | None = None,
    vault_root: str | Path | None = None,
    obsidian_prefix: str = "OpenClaw/Cutover",
    telegram_target: str | None = None,
    telegram_account: str | None = None,
    allow_live_obsidian: bool = False,
    allow_live_telegram: bool = False,
    output_dir: str | Path,
    telegram_send_cmd: str | None = None,
    telegram_delivery: str = "subprocess",
) -> dict[str, Any]:
    """Build cutover review artifacts and a receipt without implicit live effects."""

    _validate_inputs(
        packet_dir=packet_dir,
        ivy_fixture=ivy_fixture,
        weekly_fixture=weekly_fixture,
        vault_root=vault_root,
        obsidian_prefix=obsidian_prefix,
        telegram_target=telegram_target,
        telegram_account=telegram_account,
        allow_live_obsidian=allow_live_obsidian,
        allow_live_telegram=allow_live_telegram,
    )
    output_root = Path(output_dir).resolve()
    _prepare_output_root(output_root)

    packet_root = _materialize_packet(
        packet_dir=Path(packet_dir).resolve() if packet_dir is not None else None,
        ivy_fixture=Path(ivy_fixture).resolve() if ivy_fixture is not None else None,
        weekly_fixture=Path(weekly_fixture).resolve() if weekly_fixture is not None else None,
        output_root=output_root,
    )
    packet_summary = _load_json(packet_root / "summary.json")
    auto_summary = auto_review_packet(
        packet_dir=packet_root,
        output_dir=output_root / "auto_review",
    )
    decisions = _collect_decisions(auto_summary)
    blocked_actions = _blocked_actions(
        allow_live_obsidian=allow_live_obsidian,
        allow_live_telegram=allow_live_telegram,
    )

    obsidian = _publish_obsidian_notes(
        packet_summary=packet_summary,
        auto_summary=auto_summary,
        decisions=decisions,
        output_root=output_root,
        vault_root=Path(vault_root).resolve() if vault_root is not None else None,
        obsidian_prefix=obsidian_prefix,
        allow_live_obsidian=allow_live_obsidian,
        blocked_actions=blocked_actions,
    )
    obsidian_trust_blockers = _obsidian_trust_blockers(obsidian)
    pointer = _telegram_pointer(
        obsidian=obsidian,
        blocked_actions=blocked_actions,
        allow_live_obsidian=allow_live_obsidian,
        allow_live_telegram=allow_live_telegram,
        obsidian_trust_blockers=obsidian_trust_blockers,
    )
    telegram = _publish_telegram_pointer(
        pointer=pointer,
        output_root=output_root,
        telegram_target=telegram_target,
        telegram_account=telegram_account,
        allow_live_telegram=allow_live_telegram,
        telegram_send_cmd=telegram_send_cmd,
        telegram_delivery=telegram_delivery,
        upstream_obsidian_trusted=not obsidian_trust_blockers,
        upstream_obsidian_blockers=obsidian_trust_blockers,
    )

    status = _overall_status(obsidian=obsidian, telegram=telegram, decisions=decisions)
    receipt: dict[str, Any] = {
        "schema": CUTOVER_SCHEMA,
        "status": status,
        "created_at": DETERMINISTIC_CREATED_AT,
        "input": {
            "source_kind": "packet_dir" if packet_dir is not None else "fixtures",
            "packet_dir": str(packet_root),
            "ivy_fixture": str(ivy_fixture) if ivy_fixture is not None else None,
            "weekly_fixture": str(weekly_fixture) if weekly_fixture is not None else None,
        },
        "review": {
            "reviewer_human_ref": AUTOMATED_SUMAN_REVIEWER_HUMAN_REF,
            "test_only": True,
            "decisions": decisions,
            "auto_review_summary": auto_summary["artifacts"]["summary_json"],
        },
        "obsidian": obsidian,
        "telegram": telegram,
        "safety": {
            "default_no_live_writes": True,
            "allow_live_obsidian": allow_live_obsidian,
            "allow_live_telegram": allow_live_telegram,
            "mutation_permission_granted": False,
            "public_publish_performed": False,
            "trading_or_money_action_performed": False,
            "auth_or_secret_access_performed": False,
            "oldmac_mutation_performed": False,
            "blocked_actions": blocked_actions,
        },
        "artifacts": {
            "output_dir": str(output_root),
            "receipt_json": str(output_root / "cutover_receipt.json"),
            "receipt_md": str(output_root / "cutover_receipt.md"),
            "input_packet": str(packet_root),
            "auto_review": auto_summary["artifacts"]["output_dir"],
        },
    }
    _write_json(output_root / "cutover_receipt.json", receipt)
    (output_root / "cutover_receipt.md").write_text(_render_receipt_md(receipt), encoding="utf-8")
    return receipt


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build AWK OpenClaw live cutover review artifacts with explicit live-effect gates."
    )
    parser.add_argument("--packet-dir", type=Path, help="Wave 13 two-lane packet directory")
    parser.add_argument("--ivy-fixture", type=Path, help="Ivy/Jonah live-readonly fixture JSON")
    parser.add_argument("--weekly-fixture", type=Path, help="Weekly update live-readonly fixture JSON")
    parser.add_argument("--vault-root", type=Path, help="Obsidian/Northstar vault root for explicitly allowed writes")
    parser.add_argument("--obsidian-prefix", default="OpenClaw/Cutover", help="Relative note prefix")
    parser.add_argument("--review-prefix", dest="obsidian_prefix", help="Alias for --obsidian-prefix")
    parser.add_argument("--telegram-target", help="Telegram target/chat for explicitly allowed sends")
    parser.add_argument("--telegram-target-env", help="Environment variable containing Telegram target")
    parser.add_argument("--telegram-account", help="Telegram account/profile for explicitly allowed sends")
    parser.add_argument("--allow-live-obsidian", action="store_true")
    parser.add_argument("--allow-obsidian-write", dest="allow_live_obsidian", action="store_true")
    parser.add_argument("--allow-live-telegram", action="store_true")
    parser.add_argument("--allow-telegram-send", dest="allow_live_telegram", action="store_true")
    parser.add_argument("--telegram-delivery", default="subprocess", choices=("subprocess", "openclaw-message-send"))
    parser.add_argument("--telegram-send-cmd", help="Telegram send command; defaults to AWK_TELEGRAM_SEND_CMD or telegram-send")
    parser.add_argument("--output-dir", "--artifact-dir", required=True, type=Path)
    parser.add_argument("--openclaw-root", type=Path, help="Accepted for OpenClaw bridge compatibility; recorded by bridge.")
    parser.add_argument("--no-public-publish", action="store_true")
    parser.add_argument("--no-trading", action="store_true")
    parser.add_argument("--no-auth", action="store_true")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--no-destructive", action="store_true")
    return parser


def _validate_inputs(
    *,
    packet_dir: str | Path | None,
    ivy_fixture: str | Path | None,
    weekly_fixture: str | Path | None,
    vault_root: str | Path | None,
    obsidian_prefix: str,
    telegram_target: str | None,
    telegram_account: str | None,
    allow_live_obsidian: bool,
    allow_live_telegram: bool,
) -> None:
    has_packet = packet_dir is not None
    has_fixtures = ivy_fixture is not None or weekly_fixture is not None
    if has_packet and has_fixtures:
        raise ValueError("provide --packet-dir or --ivy-fixture/--weekly-fixture, not both")
    if not has_packet and not (ivy_fixture is not None and weekly_fixture is not None):
        raise ValueError("provide --packet-dir or both --ivy-fixture and --weekly-fixture")
    if _unsafe_relative_path(obsidian_prefix):
        raise ValueError("--obsidian-prefix must be a relative path without traversal")
    if allow_live_obsidian and vault_root is None:
        raise ValueError("--vault-root is required with --allow-live-obsidian")
    if allow_live_telegram and (not telegram_target or not telegram_account):
        raise ValueError("--telegram-target and --telegram-account are required with --allow-live-telegram")


def _prepare_output_root(output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    for name in GENERATED_NAMES:
        path = output_root / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def _materialize_packet(
    *,
    packet_dir: Path | None,
    ivy_fixture: Path | None,
    weekly_fixture: Path | None,
    output_root: Path,
) -> Path:
    packet_root = output_root / "input_packet"
    if packet_dir is not None:
        if not (packet_dir / "summary.json").exists():
            raise ValueError(f"packet directory is missing summary.json: {packet_dir}")
        shutil.copytree(packet_dir, packet_root)
        _rewrite_copied_packet_note_paths(source_root=packet_dir, copied_root=packet_root)
        return packet_root
    if ivy_fixture is None or weekly_fixture is None:
        raise ValueError("both fixtures are required")
    build_onboarding_packet(
        ivy_fixture=ivy_fixture,
        weekly_fixture=weekly_fixture,
        output_dir=packet_root,
    )
    return packet_root


def _rewrite_copied_packet_note_paths(*, source_root: Path, copied_root: Path) -> None:
    """Keep copied Wave 13 packets self-contained before local/test review."""

    lanes_root = copied_root / "lanes"
    if not lanes_root.exists():
        return
    for review_index in lanes_root.glob("*/review_notes.json"):
        notes = json.loads(review_index.read_text(encoding="utf-8"))
        if not isinstance(notes, list):
            continue
        rewritten: list[Any] = []
        for note in notes:
            if not isinstance(note, Mapping):
                rewritten.append(note)
                continue
            updated = dict(note)
            original_note_path = str(updated.get("note_path") or "")
            relative_note_path = str(updated.get("relative_note_path") or "")
            copied_note_path = _copied_path(
                original_path=original_note_path,
                relative_path=relative_note_path,
                source_root=source_root,
                copied_root=copied_root,
            )
            if copied_note_path is not None:
                updated["note_path"] = str(copied_note_path)
                readback = _mapping(updated.get("readback"))
                if readback:
                    readback["note_path"] = str(copied_note_path)
                    updated["readback"] = readback
            rewritten.append(updated)
        review_index.write_text(json.dumps(rewritten, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _copied_path(
    *,
    original_path: str,
    relative_path: str,
    source_root: Path,
    copied_root: Path,
) -> Path | None:
    if relative_path:
        candidate = copied_root / relative_path
        if candidate.exists():
            return candidate.resolve()
    if original_path:
        path = Path(original_path)
        try:
            return (copied_root / path.resolve().relative_to(source_root.resolve())).resolve()
        except ValueError:
            return None
    return None


def _collect_decisions(auto_summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    lanes = _mapping(auto_summary.get("lanes"))
    for lane_id in sorted(lanes):
        lane = _mapping(lanes[lane_id])
        for decision in _list(lane.get("decisions")):
            if not isinstance(decision, Mapping):
                continue
            decisions.append(
                {
                    "lane_id": lane_id,
                    "stage_id": str(decision.get("stage_id") or ""),
                    "decision": decision.get("review_decision"),
                    "review_status": decision.get("review_status"),
                    "reviewer_human_ref": decision.get("reviewer_human_ref"),
                    "note_path": decision.get("note_path"),
                    "review_receipt_path": decision.get("review_receipt_path"),
                    "mutation_permission_granted": False,
                }
            )
    return decisions


def _publish_obsidian_notes(
    *,
    packet_summary: Mapping[str, Any],
    auto_summary: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    output_root: Path,
    vault_root: Path | None,
    obsidian_prefix: str,
    allow_live_obsidian: bool,
    blocked_actions: Sequence[str],
) -> dict[str, Any]:
    if allow_live_obsidian:
        if vault_root is None:
            raise ValueError("vault_root is required for live Obsidian writes")
        adapter_root = vault_root
        adapter = LiveObsidianMarkdownSurfaceAdapter(
            adapter_root,
            allowed_relative_prefix=obsidian_prefix,
            allow_live_write=True,
            created_at=DETERMINISTIC_CREATED_AT,
            canonical_surface="openclaw_cutover_obsidian",
        )
    else:
        adapter_root = output_root / "obsidian-sandbox"
        adapter = SandboxObsidianMarkdownSurfaceAdapter(
            adapter_root,
            created_at=DETERMINISTIC_CREATED_AT,
            canonical_surface="openclaw_cutover_obsidian",
        )
    notes: list[dict[str, Any]] = []
    lanes = _mapping(packet_summary.get("lanes"))
    for lane_id in sorted(lanes):
        lane = _mapping(lanes[lane_id])
        stage_id = f"{lane_id}_cutover_review"
        note_rel = Path(obsidian_prefix) / lane_id / "cutover-review.md"
        lane_decisions = [item for item in decisions if item.get("lane_id") == lane_id]
        action_fingerprint = digest_data(
            {
                "schema": CUTOVER_SCHEMA,
                "lane_id": lane_id,
                "fixture_id": lane.get("fixture_id"),
                "readiness": _mapping(lane.get("readiness")).get("classification"),
                "decisions": [_fingerprint_decision(item) for item in lane_decisions],
                "blocked_actions": list(blocked_actions),
                "mutation_permission_granted": False,
            }
        )
        invocation = AdapterInvocation(
            invocation_id=f"cutover:obsidian:{lane_id}",
            workflow_id=str(lane.get("workflow_id") or f"openclaw_{lane_id}_cutover"),
            instance_id=str(lane.get("fixture_id") or "openclaw-cutover"),
            stage_run_id=stage_id,
            adapter_family=AdapterFamily.SURFACE,
            adapter_id=adapter.adapter_id,
            operation="publish",
            input_ref=f"packet:{packet_summary.get('packet_id')}",
            context_packet_ref=f"cutover:{lane_id}:operator-visible-note",
            idempotency_key=f"cutover:{lane_id}:{action_fingerprint}",
        )
        publish = adapter.publish(
            invocation,
            {
                "title": f"OpenClaw Cutover Review - {LANE_LABELS.get(lane_id, lane_id)}",
                "note_path": str(note_rel),
                "stage_id": stage_id,
                "gate_id": f"cutover:{lane_id}",
                "human_ref": "operator-visible-cutover-review",
                "human_ask": _obsidian_human_ask(lane_id, lane, lane_decisions),
                "allowed_decisions": ("acknowledged", "needs_follow_up", "blocked"),
                "requested_action": "Review this cutover artifact as evidence only.",
                "exact_action": "Surface the OpenClaw AWK review state for operator readback only within this configured note path.",
                "action_fingerprint": action_fingerprint,
                "evidence_refs": _lane_evidence_refs(lane, auto_summary, lane_decisions),
                "test_only": not allow_live_obsidian,
                "non_live": not allow_live_obsidian,
                "live_operator_surface_allowed": allow_live_obsidian,
                "public_publish_blocked": True,
            },
        )
        surface_ref = dict(publish.outputs.get("surface_ref", {}))
        if publish.outputs.get("note_path"):
            surface_ref["note_path"] = publish.outputs["note_path"]
        readback = adapter.readback(surface_ref) if surface_ref else None
        readback_outputs = _mapping(readback.runtime_provenance.get("outputs")) if readback else {}
        publish_error = _mapping(publish.outputs.get("error"))
        readback_hash = readback_outputs.get("content_hash")
        trusted = (
            publish.status == "succeeded"
            and getattr(readback, "status", None) == "succeeded"
            and bool(readback_hash)
            and readback_hash == publish.outputs.get("content_hash")
        )
        notes.append(
            {
                "lane_id": lane_id,
                "status": publish.status,
                "note_path": publish.outputs.get("note_path"),
                "relative_note_path": str(note_rel),
                "content_hash": publish.outputs.get("content_hash"),
                "readback_hash": readback_hash,
                "readback_confirmed": bool(readback_outputs.get("readback_confirmed", False)),
                "readback_hash_matches": bool(readback_outputs.get("hash_matches", False)),
                "readback_receipt_id": getattr(readback, "receipt_id", None),
                "readback_status": getattr(readback, "status", None),
                "action_fingerprint": action_fingerprint,
                "publish_receipt_ref": publish.receipt_ref,
                "adapter_id": adapter.adapter_id,
                "decision_count": len(lane_decisions),
                "idempotency_replayed": bool(publish.outputs.get("idempotency_replayed", False)),
                "trusted": trusted,
                "error_class": publish_error.get("error_class"),
                "error_message": publish_error.get("message"),
            }
        )
    return {
        "enabled": allow_live_obsidian,
        "mode": "configured_vault_root" if allow_live_obsidian else "sandbox_output_root",
        "adapter_id": adapter.adapter_id,
        "adapter_root": str(adapter_root),
        "obsidian_prefix": obsidian_prefix,
        "notes": notes,
    }


def _publish_telegram_pointer(
    *,
    pointer: str,
    output_root: Path,
    telegram_target: str | None,
    telegram_account: str | None,
    allow_live_telegram: bool,
    telegram_send_cmd: str | None,
    telegram_delivery: str,
    upstream_obsidian_trusted: bool,
    upstream_obsidian_blockers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    adapter = SandboxTelegramOutboxSurfaceAdapter(
        output_root / "telegram-outbox",
        created_at=DETERMINISTIC_CREATED_AT,
        canonical_surface="openclaw_cutover_telegram_outbox",
    )
    action_fingerprint = digest_data(
        {
            "schema": CUTOVER_SCHEMA,
            "telegram_target": telegram_target,
            "telegram_account": telegram_account,
            "pointer": pointer,
            "allow_live_telegram": allow_live_telegram,
            "upstream_obsidian_trusted": upstream_obsidian_trusted,
            "upstream_obsidian_blockers": list(upstream_obsidian_blockers),
        }
    )
    invocation = AdapterInvocation(
        invocation_id="cutover:telegram:pointer",
        workflow_id="openclaw_cutover",
        instance_id="openclaw-cutover",
        stage_run_id="telegram_pointer",
        adapter_family=AdapterFamily.SURFACE,
        adapter_id=adapter.adapter_id,
        operation="publish",
        input_ref="cutover:obsidian-notes",
        context_packet_ref="cutover:telegram-pointer",
        idempotency_key=f"cutover:telegram:{action_fingerprint}",
    )
    publish = adapter.publish(
        invocation,
        {
            "title": "OpenClaw AWK cutover pointer",
            "body": pointer,
            "stage_id": "telegram_pointer",
            "gate_id": "cutover:telegram-pointer",
            "allowed_decisions": ("acknowledged", "needs_follow_up", "blocked"),
            "requested_action": "Read this pointer only.",
            "exact_action": "Send a concise Telegram pointer to the configured target only if explicitly enabled.",
            "action_fingerprint": action_fingerprint,
            "evidence_refs": (),
            "test_only": True,
            "non_live": True,
        },
    )
    surface_ref = dict(publish.outputs.get("surface_ref", {}))
    readback = adapter.readback(surface_ref) if surface_ref else None
    live_readback_hash = None
    if allow_live_telegram:
        if upstream_obsidian_trusted:
            live = _send_live_telegram_pointer(
                pointer=pointer,
                telegram_target=telegram_target or "",
                telegram_account=telegram_account or "",
                output_root=output_root,
                telegram_send_cmd=telegram_send_cmd,
                telegram_delivery=telegram_delivery,
            )
            send_result = live["send_result"]
            live_readback_hash = live.get("readback_hash")
        else:
            send_result = {
                "status": "blocked",
                "reason": "obsidian_receipts_not_trusted",
                "performed": False,
                "upstream_blockers": list(upstream_obsidian_blockers),
            }
    else:
        send_result = {
            "status": "not_sent",
            "reason": "allow_live_telegram was not set",
            "performed": False,
        }
    return {
        "enabled": allow_live_telegram,
        "adapter_id": adapter.adapter_id,
        "outbox_message_path": publish.outputs.get("message_path"),
        "outbox_content_hash": publish.outputs.get("content_hash"),
        "readback_hash": _hash_file(Path(str(publish.outputs.get("message_path"))))
        if publish.outputs.get("message_path")
        else None,
        "live_readback_hash": live_readback_hash,
        "readback_receipt_id": getattr(readback, "receipt_id", None),
        "target": telegram_target,
        "account": telegram_account,
        "upstream_obsidian_trusted": upstream_obsidian_trusted,
        "upstream_obsidian_blockers": list(upstream_obsidian_blockers),
        "pointer": pointer,
        "send_result": send_result,
    }


def _send_telegram_pointer(
    *,
    pointer: str,
    telegram_target: str,
    telegram_account: str,
    telegram_send_cmd: str | None,
) -> dict[str, Any]:
    base_cmd = telegram_send_cmd or os.environ.get("AWK_TELEGRAM_SEND_CMD") or "telegram-send"
    cmd = shlex.split(base_cmd) + [
        "--account",
        telegram_account,
        "--target",
        telegram_target,
        "--message",
        pointer,
    ]
    try:
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return {
            "status": "blocked",
            "performed": False,
            "command": _redacted_command(cmd),
            "error": str(exc),
        }
    return {
        "status": "sent" if completed.returncode == 0 else "blocked",
        "performed": completed.returncode == 0,
        "command": _redacted_command(cmd),
        "returncode": completed.returncode,
        "stdout_ref": _short_text(completed.stdout),
        "stderr_ref": _short_text(completed.stderr),
    }


def _send_live_telegram_pointer(
    *,
    pointer: str,
    telegram_target: str,
    telegram_account: str,
    output_root: Path,
    telegram_send_cmd: str | None,
    telegram_delivery: str,
) -> dict[str, Any]:
    command = ("openclaw",)
    if telegram_send_cmd:
        command = tuple(shlex.split(telegram_send_cmd))
    elif telegram_delivery != "openclaw-message-send":
        command = tuple(shlex.split(os.environ.get("AWK_TELEGRAM_SEND_CMD") or "openclaw"))
    adapter = OpenClawTelegramSurfaceAdapter(
        account=telegram_account,
        target=telegram_target,
        allow_live_send=True,
        created_at=DETERMINISTIC_CREATED_AT,
        canonical_surface="openclaw_cutover_telegram",
        receipt_dir=output_root / "telegram-live-receipts",
        command=command,
    )
    action_fingerprint = digest_data(
        {
            "schema": CUTOVER_SCHEMA,
            "telegram_target": telegram_target,
            "telegram_account": telegram_account,
            "pointer": pointer,
            "delivery": telegram_delivery,
        }
    )
    invocation = AdapterInvocation(
        invocation_id="cutover:telegram:live-pointer",
        workflow_id="openclaw_cutover",
        instance_id="openclaw-cutover",
        stage_run_id="telegram_live_pointer",
        adapter_family=AdapterFamily.SURFACE,
        adapter_id=adapter.adapter_id,
        operation="publish",
        input_ref="cutover:obsidian-notes",
        context_packet_ref="cutover:telegram-live-pointer",
        idempotency_key=f"cutover:telegram-live:{action_fingerprint}",
    )
    publish = adapter.publish(
        invocation,
        {
            "title": "OpenClaw AWK cutover pointer",
            "message": pointer,
            "stage_id": "telegram_live_pointer",
            "gate_id": "cutover:telegram-live-pointer",
            "allowed_decisions": ("acknowledged", "needs_follow_up", "blocked"),
            "requested_action": "Read this operator-surface pointer.",
            "exact_action": "Send this concise operator-surface pointer to the configured Telegram target.",
            "action_fingerprint": action_fingerprint,
            "evidence_refs": (),
            "live_operator_surface_allowed": True,
            "public_publish_blocked": True,
        },
    )
    surface_ref = dict(publish.outputs.get("surface_ref", {}))
    readback = adapter.readback(surface_ref) if surface_ref else None
    readback_outputs = _mapping(readback.runtime_provenance.get("outputs")) if readback else {}
    return {
        "send_result": {
            "status": "sent" if publish.status == "succeeded" else "blocked",
            "performed": publish.status == "succeeded",
            "publish_receipt_ref": publish.receipt_ref,
            "message_id": publish.outputs.get("message_id"),
            "command": publish.outputs.get("command"),
            "error": _mapping(publish.outputs.get("error")).get("message"),
        },
        "readback_hash": digest_data(readback_outputs) if readback_outputs else None,
    }


def _telegram_pointer(
    *,
    obsidian: Mapping[str, Any],
    blocked_actions: Sequence[str],
    allow_live_obsidian: bool,
    allow_live_telegram: bool,
    obsidian_trust_blockers: Sequence[Mapping[str, Any]],
) -> str:
    note_lines = [
        "- {lane}: {path} (readback={readback}; trusted={trusted}; status={status})".format(
            lane=note.get("lane_id"),
            path=note.get("note_path"),
            readback=note.get("readback_hash"),
            trusted=note.get("trusted"),
            status=note.get("status"),
        )
        for note in _list(obsidian.get("notes"))
        if isinstance(note, Mapping)
    ]
    headline = (
        "OpenClaw AWK cutover review artifacts ready."
        if not obsidian_trust_blockers
        else "OpenClaw AWK cutover review artifacts are blocked; inspect receipt before trusting live surfaces."
    )
    blocker_lines = [
        "- blocked {lane}: {reason}".format(
            lane=blocker.get("lane_id"),
            reason=blocker.get("reason"),
        )
        for blocker in obsidian_trust_blockers
    ]
    safety = (
        f"safety: obsidian_enabled={allow_live_obsidian}; "
        f"telegram_enabled={allow_live_telegram}; mutation_permission_granted=False; "
        "protected-action-gates=closed; see receipt for details"
    )
    return "\n".join([headline, *note_lines, *blocker_lines, safety])


def _obsidian_trust_blockers(obsidian: Mapping[str, Any]) -> list[dict[str, Any]]:
    notes = [note for note in _list(obsidian.get("notes")) if isinstance(note, Mapping)]
    if not notes:
        return [{"lane_id": None, "reason": "no_obsidian_notes"}]

    blockers: list[dict[str, Any]] = []
    for note in notes:
        lane_id = note.get("lane_id")
        if note.get("status") != "succeeded":
            blockers.append(
                {
                    "lane_id": lane_id,
                    "reason": "publish_failed",
                    "status": note.get("status"),
                    "error_class": note.get("error_class"),
                    "error_message": note.get("error_message"),
                }
            )
            continue
        if note.get("readback_status") != "succeeded":
            blockers.append(
                {
                    "lane_id": lane_id,
                    "reason": "readback_failed",
                    "readback_status": note.get("readback_status"),
                }
            )
            continue
        if not note.get("readback_hash") or note.get("readback_hash") != note.get("content_hash"):
            blockers.append(
                {
                    "lane_id": lane_id,
                    "reason": "readback_hash_mismatch",
                    "content_hash": note.get("content_hash"),
                    "readback_hash": note.get("readback_hash"),
                }
            )
    return blockers


def _obsidian_human_ask(
    lane_id: str,
    lane: Mapping[str, Any],
    lane_decisions: Sequence[Mapping[str, Any]],
) -> str:
    decisions = ", ".join(
        f"{item.get('stage_id')}={item.get('decision')} via {item.get('reviewer_human_ref')}"
        for item in lane_decisions
    ) or "no local/test human gates were present in this packet"
    readiness = _mapping(lane.get("readiness")).get("classification")
    return (
        f"Readback artifact for {LANE_LABELS.get(lane_id, lane_id)}. "
        f"Readiness is {readiness}. Local/test reviewer decisions: {decisions}. "
        "This note is evidence only and grants no mutation permission."
    )


def _lane_evidence_refs(
    lane: Mapping[str, Any],
    auto_summary: Mapping[str, Any],
    lane_decisions: Sequence[Mapping[str, Any]],
) -> list[str]:
    refs = [
        str(_mapping(lane.get("artifacts")).get("lane_report") or ""),
        str(auto_summary.get("artifacts", {}).get("summary_json") or ""),
    ]
    refs.extend(str(item.get("review_receipt_path") or "") for item in lane_decisions)
    return [ref for ref in refs if ref]


def _fingerprint_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "lane_id": decision.get("lane_id"),
        "stage_id": decision.get("stage_id"),
        "decision": decision.get("decision"),
        "review_status": decision.get("review_status"),
        "reviewer_human_ref": decision.get("reviewer_human_ref"),
        "mutation_permission_granted": False,
    }


def _blocked_actions(*, allow_live_obsidian: bool, allow_live_telegram: bool) -> list[str]:
    actions = list(FORBIDDEN_ACTIONS)
    if not allow_live_obsidian:
        actions.append("live_obsidian_or_northstar_write")
    if not allow_live_telegram:
        actions.append("telegram_send")
    return actions


def _overall_status(
    *,
    obsidian: Mapping[str, Any],
    telegram: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
) -> str:
    notes = _list(obsidian.get("notes"))
    if not notes or any(_mapping(note).get("status") != "succeeded" for note in notes):
        return "blocked"
    if any(_mapping(note).get("readback_status") != "succeeded" for note in notes):
        return "blocked"
    if any(_mapping(decision).get("review_status") != "succeeded" for decision in decisions):
        return "blocked"
    send_result = _mapping(_mapping(telegram).get("send_result"))
    if send_result.get("status") == "blocked":
        return "blocked"
    return "ready"


def _render_receipt_md(receipt: Mapping[str, Any]) -> str:
    lines = [
        "# OpenClaw AWK Live Cutover Receipt",
        "",
        f"- Status: `{receipt.get('status')}`",
        f"- Packet: `{_mapping(receipt.get('input')).get('packet_dir')}`",
        f"- Reviewer: `{_mapping(receipt.get('review')).get('reviewer_human_ref')}`",
        f"- Live Obsidian enabled: `{_mapping(receipt.get('safety')).get('allow_live_obsidian')}`",
        f"- Live Telegram enabled: `{_mapping(receipt.get('safety')).get('allow_live_telegram')}`",
        f"- Mutation permission granted: `{_mapping(receipt.get('safety')).get('mutation_permission_granted')}`",
        "",
        "## Obsidian Notes",
        "",
    ]
    for note in _list(_mapping(receipt.get("obsidian")).get("notes")):
        if not isinstance(note, Mapping):
            continue
        lines.append(
            "- `{lane}`: `{path}` readback `{hash}` status `{status}`".format(
                lane=note.get("lane_id"),
                path=note.get("note_path"),
                hash=note.get("readback_hash"),
                status=note.get("readback_status"),
            )
        )
    lines.extend(["", "## Decisions", ""])
    for decision in _list(_mapping(receipt.get("review")).get("decisions")):
        if not isinstance(decision, Mapping):
            continue
        lines.append(
            "- `{lane}` `{stage}`: `{decision}` via `{human}`".format(
                lane=decision.get("lane_id"),
                stage=decision.get("stage_id"),
                decision=decision.get("decision"),
                human=decision.get("reviewer_human_ref"),
            )
        )
    telegram = _mapping(receipt.get("telegram"))
    send_result = _mapping(telegram.get("send_result"))
    lines.extend(
        [
            "",
            "## Telegram",
            "",
            f"- Outbox: `{telegram.get('outbox_message_path')}`",
            f"- Send status: `{send_result.get('status')}`",
            f"- Send ref: `{send_result.get('stdout_ref') or send_result.get('reason') or send_result.get('error')}`",
            "",
            "## Blocked Actions",
            "",
        ]
    )
    for action in _list(_mapping(receipt.get("safety")).get("blocked_actions")):
        lines.append(f"- `{action}`")
    lines.append("")
    return "\n".join(lines)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_plain_data(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _canonical_json(data: Mapping[str, Any]) -> str:
    return json.dumps(to_plain_data(data), sort_keys=True, separators=(",", ":")) + "\n"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _unsafe_relative_path(value: str) -> bool:
    if not value.strip():
        return True
    path = Path(value)
    return path.is_absolute() or any(part in {"..", ".", ""} for part in path.parts)


def _short_text(value: str, limit: int = 400) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _redacted_command(cmd: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    sensitive_flags = {"--token", "--api-key", "--password", "--secret"}
    for part in cmd:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        redacted.append(part)
        if part in sensitive_flags:
            redact_next = True
    return redacted


def _hash_file(path: Path) -> str | None:
    if not path.exists():
        return None
    import hashlib

    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


if __name__ == "__main__":
    raise SystemExit(main())
