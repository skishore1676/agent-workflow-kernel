#!/usr/bin/env python3
"""OpenClaw fixture-only shadow runner for AWK adoption reports."""

from __future__ import annotations

import argparse
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

    adoption_status = _adoption_status(
        lane=lane,
        lane_kind=lane_kind,
        parity_status=str(parity_report["status"]),
        read_only_status=read_only_status,
        adapter_probe=adapter_probe,
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
        "lane_adapter": adapter_probe,
        "mapping_summary": mapping_summary,
        "next_recommended_adoption_step": next_step,
        "parity_report": parity_report,
        "read_only_adapter_result": adapter_result,
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
) -> str:
    if read_only_status == "blocked":
        return "blocked_external_action"
    if adapter_probe["status"] == "adapter_missing":
        return "adapter_missing"
    if lane_kind not in SUPPORTED_GENERIC_LANES and lane == lane_kind:
        return "unsupported_lane"
    if parity_status == "equivalent" and read_only_status == "succeeded":
        return "shadow_ready"
    return "shadow_review_required"


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
