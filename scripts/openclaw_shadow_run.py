#!/usr/bin/env python3
"""OpenClaw fixture-only shadow runner for AWK adoption reports."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
KERNEL_PATH = ROOT / "packages" / "kernel"
OPENCLAW_ADAPTER_PATH = ROOT / "packages" / "adapters" / "openclaw"
for package_path in (str(KERNEL_PATH), str(OPENCLAW_ADAPTER_PATH)):
    if package_path not in sys.path:
        sys.path.insert(0, package_path)

from agent_workflow_kernel import compare_receipts, to_plain_data  # noqa: E402
from agent_workflow_kernel_openclaw import (  # noqa: E402
    OpenClawMutationBlocked,
    OpenClawReadOnlyAdapter,
    mapping_from_fixture,
)


SHADOW_REPORT_SCHEMA = "workflow.kernel.openclaw-shadow-report.v1"
DEFAULT_BLOCKED_EXTERNAL_ACTIONS = (
    {
        "action": "live_openclaw_call",
        "reason": "shadow runner consumes supplied fixtures only",
    },
    {
        "action": "oldmac_mutation",
        "reason": "runtime mutation is outside this read-only proof",
    },
    {
        "action": "operator_surface_write",
        "reason": "Telegram, Obsidian, Northstar, and Blackboard writes require a human gate",
    },
    {
        "action": "trade_or_deploy",
        "reason": "financial and deployment effects are forbidden in shadow runs",
    },
)
LANE_ADAPTER_MODULES = {
    "ivy": "agent_workflow_kernel_openclaw.ivy_lane",
    "weekly": "agent_workflow_kernel_openclaw.weekly_update",
}
SUPPORTED_GENERIC_LANES = {
    "generic",
    "openclaw",
    "openclaw-fixture",
    "or-research",
    "quality-review",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        fixture = _load_json(args.fixture)
        report = build_shadow_report(fixture)
        rendered = _canonical_json(report)
        if args.report == "-":
            sys.stdout.write(rendered)
        else:
            report_path = Path(args.report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(rendered, encoding="utf-8")
        return 0
    except Exception as exc:
        print(_canonical_json({"ok": False, "error": str(exc)}), file=sys.stderr, end="")
        return 1


def build_shadow_report(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Run a supplied OpenClaw fixture through the AWK read-only proof path."""

    lane = _detect_lane(fixture)
    lane_kind = _lane_kind(lane, fixture)
    adapter_probe = _lane_adapter_probe(lane_kind)
    identity = _fixture_identity(fixture, lane)
    blocked_actions = _blocked_external_actions(fixture, lane_kind)
    next_step = _next_recommended_step(lane_kind, adapter_probe)
    lane_adoption = None
    lane_receipts: list[dict[str, Any]] = []

    try:
        inspection = OpenClawReadOnlyAdapter().inspect_fixture(fixture)
        receipt = to_plain_data(inspection.receipt)
        parity_report = compare_receipts(
            fixture.get("expected_host_receipt", receipt),
            receipt,
            ignored_fields=fixture.get("ignored_fields", {}),
            expected_label="expected_openclaw_receipt",
            actual_label="awk_shadow_receipt",
            metadata={
                "fixture_id": identity["fixture_id"],
                "lane": lane,
                "expected_host_receipt_supplied": "expected_host_receipt" in fixture,
            },
        ).to_data()
        mapping_summary = _mapping_summary(inspection.mapping.to_metadata())
        receipts = [receipt]
        adapter_result = to_plain_data(inspection.result)
        read_only_status = "succeeded"
    except OpenClawMutationBlocked as exc:
        parity_report = _empty_parity_report("blocked_external_action")
        mapping_summary = _fallback_mapping_summary(fixture)
        receipts = []
        adapter_result = {"status": "blocked", "error": str(exc)}
        read_only_status = "blocked"
        next_step = "Regenerate the fixture with a read-only OpenClaw operation before adoption."
    except ValueError as exc:
        if lane_kind in LANE_ADAPTER_MODULES:
            parity_report = _empty_parity_report("adapter_missing")
            mapping_summary = _fallback_mapping_summary(fixture)
            receipts = []
            adapter_result = {"status": "adapter_missing", "error": str(exc)}
            read_only_status = "adapter_missing"
        else:
            raise

    if adapter_probe["status"] == "available":
        lane_adoption = _run_lane_adapter(lane_kind, fixture)
        lane_receipts = list(lane_adoption.get("receipts", []))
        if lane_adoption["status"] == "adapter_input_invalid":
            next_step = "Regenerate this fixture in the lane-specific OpenClaw export shape, then rerun the shadow proof."

    readiness_blockers = _readiness_blockers(
        fixture=fixture,
        lane_kind=lane_kind,
        parity_report=parity_report,
        mapping_summary=mapping_summary,
        lane_adoption=lane_adoption,
    )
    adoption_status = _adoption_status(
        lane=lane,
        lane_kind=lane_kind,
        parity_status=str(parity_report["status"]),
        read_only_status=read_only_status,
        adapter_probe=adapter_probe,
        lane_adoption_status=str(lane_adoption["status"]) if lane_adoption else None,
        readiness_blockers=readiness_blockers,
    )
    return {
        "schema": SHADOW_REPORT_SCHEMA,
        "adoption": {
            "status": adoption_status,
            "parity_status": parity_report["status"],
            "read_only_status": read_only_status,
        },
        "blocked_external_actions": blocked_actions,
        "fixture_identity": identity,
        "lane": lane,
        "lane_adoption": lane_adoption,
        "lane_adapter": adapter_probe,
        "lane_receipts_generated": lane_receipts,
        "mapping_summary": mapping_summary,
        "next_recommended_adoption_step": next_step,
        "parity_report": parity_report,
        "read_only_adapter_result": adapter_result,
        "readiness_blockers": readiness_blockers,
        "receipts_generated": receipts,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AWK OpenClaw shadow fixture adoption proof.")
    parser.add_argument("--fixture", required=True, type=Path, help="OpenClaw-exported fixture JSON")
    parser.add_argument("--report", required=True, help="Report path, or '-' for stdout")
    return parser


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("OpenClaw shadow fixture must be a JSON object")
    return data


def _canonical_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"


def _detect_lane(fixture: Mapping[str, Any]) -> str:
    lane = fixture.get("lane")
    if isinstance(lane, str) and lane:
        return lane
    mapping = fixture.get("mapping")
    if isinstance(mapping, Mapping):
        lane_id = mapping.get("lane_id")
        if isinstance(lane_id, str) and lane_id:
            return lane_id
    return "generic"


def _lane_kind(lane: str, fixture: Mapping[str, Any]) -> str:
    schema = fixture.get("schema")
    if schema == "openclaw.ivy-jonah.fixture.v1":
        return "ivy"
    if schema == "openclaw.weekly-update-fixture.v1":
        return "weekly"
    normalized = lane.strip().lower().replace("_", "-")
    if "ivy" in fixture or normalized in {"ivy", "ivy-jonah", "ivy-jonah-editorial"}:
        return "ivy"
    if "weekly_update" in fixture or normalized in {"weekly", "weekly-update", "jarvis-weekly-update"}:
        return "weekly"
    return normalized


def _lane_adapter_probe(lane_kind: str) -> dict[str, Any]:
    module_name = LANE_ADAPTER_MODULES.get(lane_kind)
    if module_name is None:
        return {"status": "generic_readonly", "module": None}
    if importlib.util.find_spec(module_name) is None:
        return {
            "status": "adapter_missing",
            "module": module_name,
            "reason": "lane-specific Wave 4 adapter is not importable in this worktree",
        }
    return {"status": "available", "module": module_name}


def _run_lane_adapter(lane_kind: str, fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Run a lane-specific adapter when it exists in this worktree."""

    try:
        if lane_kind == "ivy":
            module = importlib.import_module("agent_workflow_kernel_openclaw")
            adoption = module.adopt_ivy_jonah_fixture(fixture)
            report = to_plain_data(adoption.report)
            receipts = [to_plain_data(receipt) for receipt in adoption.receipts]
            return {
                "status": "shadow_ready" if report.get("ready_for_shadow") else "shadow_review_required",
                "report": report,
                "receipt_count": len(receipts),
                "receipts": receipts,
            }
        if lane_kind == "weekly":
            module = importlib.import_module("agent_workflow_kernel_openclaw")
            report_obj = module.adoption_report_from_fixture(fixture)
            receipts = [to_plain_data(receipt) for receipt in module.receipts_from_weekly_update(fixture)]
            report = to_plain_data(report_obj)
            return {
                "status": str(report.get("status", "shadow_review_required")),
                "report": report,
                "receipt_count": len(receipts),
                "receipts": receipts,
            }
    except Exception as exc:
        return {
            "status": "adapter_input_invalid",
            "error": str(exc),
            "receipt_count": 0,
            "receipts": [],
        }
    return {
        "status": "not_applicable",
        "receipt_count": 0,
        "receipts": [],
    }


def _fixture_identity(fixture: Mapping[str, Any], lane: str) -> dict[str, Any]:
    return {
        "fixture_id": str(fixture.get("fixture_id", "openclaw-fixture")),
        "generated_at": fixture.get("generated_at", fixture.get("created_at")),
        "lane": lane,
        "schema": fixture.get("schema"),
        "source_root": fixture.get("source_root"),
    }


def _mapping_summary(mapping: Mapping[str, Any]) -> dict[str, Any]:
    work_ledger_ids = mapping.get("work_ledger_ids", {})
    surface_refs = mapping.get("surface_refs", [])
    runtime_refs = mapping.get("runtime_refs", [])
    return {
        "agent_id": mapping.get("agent_id"),
        "host_ref": mapping.get("host_ref"),
        "lane_id": mapping.get("lane_id"),
        "runtime_ref_count": len(runtime_refs) if isinstance(runtime_refs, list) else 0,
        "surface_ref_count": len(surface_refs) if isinstance(surface_refs, list) else 0,
        "work_ledger": {
            "handoff_id": work_ledger_ids.get("handoff_id") if isinstance(work_ledger_ids, Mapping) else None,
            "receipt_count": (
                len(work_ledger_ids.get("receipt_ids", [])) if isinstance(work_ledger_ids, Mapping) else 0
            ),
            "work_item_id": work_ledger_ids.get("work_item_id") if isinstance(work_ledger_ids, Mapping) else None,
        },
    }


def _fallback_mapping_summary(fixture: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return _mapping_summary(mapping_from_fixture(fixture).to_metadata())
    except Exception:
        mapping = fixture.get("mapping")
        if not isinstance(mapping, Mapping):
            mapping = {}
        return {
            "agent_id": mapping.get("agent_id"),
            "host_ref": mapping.get("host_ref"),
            "lane_id": mapping.get("lane_id", fixture.get("lane", "generic")),
            "runtime_ref_count": len(mapping.get("runtime_refs", [])) if isinstance(mapping.get("runtime_refs"), list) else 0,
            "surface_ref_count": len(mapping.get("surface_refs", [])) if isinstance(mapping.get("surface_refs"), list) else 0,
            "work_ledger": {
                "handoff_id": None,
                "receipt_count": 0,
                "work_item_id": None,
            },
        }


def _blocked_external_actions(fixture: Mapping[str, Any], lane_kind: str) -> list[dict[str, str]]:
    actions = list(DEFAULT_BLOCKED_EXTERNAL_ACTIONS)
    extra = fixture.get("blocked_external_actions", ())
    if isinstance(extra, list):
        actions.extend(_normalize_blocked_action(item) for item in extra)
    if lane_kind == "ivy":
        actions.append(
            {
                "action": "public_publish",
                "reason": "Ivy/Jonah publish packets require explicit human approval",
            }
        )
    if lane_kind == "weekly":
        actions.append(
            {
                "action": "blackboard_or_obsidian_write",
                "reason": "weekly shadow readback must not mutate operator notes",
            }
        )
    deduped = {(item["action"], item["reason"]): item for item in actions}
    return [deduped[key] for key in sorted(deduped)]


def _normalize_blocked_action(item: Any) -> dict[str, str]:
    if isinstance(item, str):
        return {"action": item, "reason": "blocked by supplied fixture"}
    if isinstance(item, Mapping):
        return {
            "action": str(item.get("action", "external_effect")),
            "reason": str(item.get("reason", "blocked by supplied fixture")),
        }
    return {"action": str(item), "reason": "blocked by supplied fixture"}


def _next_recommended_step(lane_kind: str, adapter_probe: Mapping[str, Any]) -> str:
    if adapter_probe["status"] == "adapter_missing":
        if lane_kind == "ivy":
            return "Merge or implement the Ivy/Jonah lane adapter, then rerun this fixture for stage-level adoption."
        if lane_kind == "weekly":
            return "Merge or implement the Jarvis weekly update adapter, then rerun this fixture for workflow-gate adoption."
    if adapter_probe["status"] == "available":
        if lane_kind == "ivy":
            return "Compare the Ivy/Jonah lane adoption report against OpenClaw exporter output before any takeover."
        if lane_kind == "weekly":
            return "Compare the weekly update adoption report against OpenClaw exporter output before any takeover."
    if lane_kind not in SUPPORTED_GENERIC_LANES:
        return "Create a lane-specific adapter or classify this lane before any takeover decision."
    return "Use this deterministic report as the read-only adoption receipt, then compare against a lane-specific shadow run."


def _adoption_status(
    *,
    lane: str,
    lane_kind: str,
    parity_status: str,
    read_only_status: str,
    adapter_probe: Mapping[str, Any],
    lane_adoption_status: str | None,
    readiness_blockers: Sequence[Mapping[str, Any]],
) -> str:
    if read_only_status == "blocked":
        return "blocked_external_action"
    if adapter_probe["status"] == "adapter_missing":
        return "adapter_missing"
    if lane_adoption_status == "adapter_input_invalid":
        return "lane_adapter_input_invalid"
    if lane_adoption_status == "waiting_on_human":
        return "waiting_on_human"
    if adapter_probe["status"] == "generic_readonly" and lane_kind not in SUPPORTED_GENERIC_LANES and lane == lane_kind:
        return "unsupported_lane"
    if readiness_blockers:
        blocker_codes = {str(item.get("code", "")) for item in readiness_blockers}
        if blocker_codes == {"expected_host_receipt_missing"}:
            return "host_receipt_missing"
        return "shadow_review_required"
    if lane_adoption_status in {
        "blocked",
        "done",
        "final_approval_required",
        "shadow_ready",
        "shadow_review_required",
        "waiting_on_human",
    }:
        return lane_adoption_status
    if parity_status == "equivalent" and read_only_status == "succeeded":
        return "shadow_ready"
    return "shadow_review_required"


def _readiness_blockers(
    *,
    fixture: Mapping[str, Any],
    lane_kind: str,
    parity_report: Mapping[str, Any],
    mapping_summary: Mapping[str, Any],
    lane_adoption: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    if "expected_host_receipt" not in fixture and parity_report.get("status") == "equivalent":
        blockers.append(
            {
                "code": "expected_host_receipt_missing",
                "message": "Fixture did not include expected_host_receipt; parity is shape-only self-comparison.",
            }
        )
    supplied = fixture.get("adoption_blockers")
    if isinstance(supplied, list):
        for item in supplied:
            if isinstance(item, Mapping):
                blockers.append(
                    {
                        "code": str(item.get("code") or item.get("id") or "fixture_blocker"),
                        "message": str(item.get("message") or item.get("reason") or item),
                    }
                )
            else:
                blockers.append({"code": "fixture_blocker", "message": str(item)})
    if lane_kind == "ivy":
        blockers.extend(_ivy_readiness_blockers(fixture, mapping_summary, lane_adoption))
    return _dedupe_blockers(blockers)


def _ivy_readiness_blockers(
    fixture: Mapping[str, Any],
    mapping_summary: Mapping[str, Any],
    lane_adoption: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    work_ledger = mapping_summary.get("work_ledger")
    if not isinstance(work_ledger, Mapping) or not work_ledger.get("work_item_id") or not work_ledger.get("handoff_id"):
        blockers.append(
            {
                "code": "ivy_work_ledger_missing",
                "message": "Ivy fixture lacks Work Ledger work_item_id and handoff_id evidence.",
            }
        )
    roles = _artifact_roles(fixture)
    missing_roles = _missing_ivy_artifact_roles(roles)
    if missing_roles:
        blockers.append(
            {
                "code": "ivy_required_artifacts_missing",
                "message": "Ivy fixture lacks required artifact roles: " + ", ".join(missing_roles),
            }
        )
    ivy = fixture.get("ivy")
    if isinstance(ivy, Mapping):
        transcript_refs = ivy.get("transcript_refs", fixture.get("transcript_refs"))
        stages = ivy.get("stages")
    else:
        transcript_refs = fixture.get("transcript_refs")
        stages = None
    if not isinstance(transcript_refs, list) or not transcript_refs:
        blockers.append(
            {
                "code": "ivy_transcript_refs_missing",
                "message": "Ivy fixture lacks transcript/editor-review proof references.",
            }
        )
    if isinstance(stages, list):
        missing = [
            str(stage.get("stage", "unknown"))
            for stage in stages
            if isinstance(stage, Mapping) and str(stage.get("status", "")).lower() == "missing"
        ]
        if missing:
            blockers.append(
                {
                    "code": "ivy_stage_artifact_missing",
                    "message": "Ivy exported missing stage artifacts for: " + ", ".join(missing),
                }
            )
    if lane_adoption and lane_adoption.get("status") == "shadow_ready":
        report = lane_adoption.get("report")
        open_questions = report.get("open_questions") if isinstance(report, Mapping) else None
        if isinstance(open_questions, list):
            for question in open_questions:
                text = str(question).lower()
                if "no transcript_ref" in text or "no review_surface" in text:
                    blockers.append({"code": "ivy_open_question_blocks_readiness", "message": str(question)})
    return blockers


def _artifact_roles(fixture: Mapping[str, Any]) -> set[str]:
    roles: set[str] = set()
    artifacts = fixture.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if isinstance(artifact, Mapping) and artifact.get("role"):
                roles.add(str(artifact["role"]))
    return roles


def _missing_ivy_artifact_roles(roles: set[str]) -> list[str]:
    required_groups = {
        "draft_package": {"draft_package", "p4_draft_package"},
        "editor_verdict": {"editor_verdict", "p4_editor_review"},
        "p5_review_surface": {"p5_review_surface", "p5_final_review"},
    }
    return [name for name, aliases in required_groups.items() if roles.isdisjoint(aliases)]


def _dedupe_blockers(blockers: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    deduped: dict[tuple[str, str], dict[str, str]] = {}
    for blocker in blockers:
        code = str(blocker.get("code", "readiness_blocker"))
        message = str(blocker.get("message", "Readiness blocker present."))
        deduped[(code, message)] = {"code": code, "message": message}
    return [deduped[key] for key in sorted(deduped)]


def _empty_parity_report(status: str) -> dict[str, Any]:
    return {
        "schema": "workflow.kernel.parity-report.v1",
        "report_id": f"parity:{status}",
        "status": status,
        "expected_label": "expected_openclaw_receipt",
        "actual_label": "awk_shadow_receipt",
        "summary": {
            "equivalent": 0,
            "different": 0,
            "missing": 0,
            "extra": 0,
            "ignored": 0,
        },
        "fields": {
            "equivalent": [],
            "different": [],
            "missing": [],
            "extra": [],
            "ignored": [],
        },
        "metadata": {},
    }


if __name__ == "__main__":
    raise SystemExit(main())
