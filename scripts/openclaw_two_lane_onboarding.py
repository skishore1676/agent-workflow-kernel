#!/usr/bin/env python3
"""Build a local two-lane OpenClaw onboarding proof packet."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
KERNEL_PATH = ROOT / "packages" / "kernel"
OPENCLAW_ADAPTER_PATH = ROOT / "packages" / "adapters" / "openclaw"
for package_path in (str(KERNEL_PATH), str(OPENCLAW_ADAPTER_PATH), str(ROOT / "scripts")):
    if package_path not in sys.path:
        sys.path.insert(0, package_path)

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterInvocation,
    LocalMarkdownHumanReviewSurfaceAdapter,
    digest_data,
    to_plain_data,
)
from openclaw_shadow_run import build_shadow_report  # noqa: E402


PACKET_SCHEMA = "workflow.kernel.openclaw-two-lane-onboarding-packet.v1"
DETERMINISTIC_CREATED_AT = "2000-01-01T00:00:00Z"
LANE_CONFIGS = {
    "ivy": {
        "label": "Ivy/Jonah editorial",
        "workflow_id": "ivy_jonah_editorial",
        "review_surface": "local_markdown_ivy_jonah_review",
    },
    "weekly": {
        "label": "Jarvis weekly update",
        "workflow_id": "jarvis_weekly_update_shadow",
        "review_surface": "local_markdown_weekly_update_review",
    },
}
FORBIDDEN_LIVE_EFFECTS = (
    "live_openclaw_call",
    "oldmac_mutation",
    "operator_surface_write",
    "telegram_send",
    "obsidian_or_northstar_write",
    "blackboard_or_obsidian_write",
    "public_publish",
    "trade_or_deploy",
    "auth_or_secret_access",
    "cron_or_deploy_change",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        summary = build_onboarding_packet(
            ivy_fixture=args.ivy_fixture,
            weekly_fixture=args.weekly_fixture,
            output_dir=args.output_dir,
        )
    except Exception as exc:
        print(_canonical_json({"ok": False, "error": str(exc)}), file=sys.stderr, end="")
        return 1

    print(
        _canonical_json(
            {
                "ok": True,
                "summary_path": summary["artifacts"]["summary_json"],
                "readme_path": summary["artifacts"]["readme"],
                "overall_readiness": summary["overall_readiness"],
            }
        ),
        end="",
    )
    return 0


def build_onboarding_packet(
    *,
    ivy_fixture: str | Path,
    weekly_fixture: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Build a deterministic local onboarding packet for the two proof lanes."""

    output_root = Path(output_dir).resolve()
    _prepare_output_root(output_root)

    lanes = {
        "ivy": _build_lane_packet("ivy", Path(ivy_fixture), output_root),
        "weekly": _build_lane_packet("weekly", Path(weekly_fixture), output_root),
    }
    overall = _overall_readiness(lanes)
    summary: dict[str, Any] = {
        "schema": PACKET_SCHEMA,
        "packet_id": _packet_id(lanes),
        "overall_readiness": overall,
        "safety": {
            "local_output_only": True,
            "mutation_permission_granted": False,
            "external_sends_performed": False,
            "forbidden_live_effects": list(FORBIDDEN_LIVE_EFFECTS),
        },
        "lanes": lanes,
        "artifacts": {
            "output_dir": str(output_root),
            "summary_json": str(output_root / "summary.json"),
            "readme": str(output_root / "README.md"),
        },
    }
    _write_json(output_root / "summary.json", summary)
    (output_root / "README.md").write_text(_render_readme(summary), encoding="utf-8")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a local Ivy/Jonah plus Jarvis weekly onboarding proof packet."
    )
    parser.add_argument("--ivy-fixture", required=True, type=Path, help="Ivy/Jonah OpenClaw fixture JSON")
    parser.add_argument("--weekly-fixture", required=True, type=Path, help="Jarvis weekly update fixture JSON")
    parser.add_argument("--output-dir", required=True, type=Path, help="Local output directory for the packet")
    return parser


def _prepare_output_root(output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    for dirname in ("lanes", "review_notes"):
        child = output_root / dirname
        if child.exists():
            shutil.rmtree(child)
    for filename in ("summary.json", "README.md"):
        child = output_root / filename
        if child.exists():
            child.unlink()


def _build_lane_packet(lane_id: str, fixture_path: Path, output_root: Path) -> dict[str, Any]:
    config = LANE_CONFIGS[lane_id]
    lane_dir = output_root / "lanes" / lane_id
    lane_dir.mkdir(parents=True, exist_ok=True)

    fixture = _load_json(fixture_path)
    shadow_report = build_shadow_report(fixture)
    lane_adoption = _mapping(shadow_report.get("lane_adoption"))
    adoption_report = _mapping(lane_adoption.get("report"))
    receipts = _list(lane_adoption.get("receipts")) or _list(shadow_report.get("receipts_generated"))

    _write_json(lane_dir / "shadow_report.json", shadow_report)
    _write_json(lane_dir / "adoption_report.json", adoption_report)
    _write_json(lane_dir / "receipts.json", receipts)

    gate_specs = _human_gate_specs(lane_id, shadow_report, adoption_report)
    review_notes = _publish_review_notes(
        lane_id=lane_id,
        config=config,
        fixture_id=_fixture_id(shadow_report, fixture),
        gate_specs=gate_specs,
        output_root=output_root,
    )
    _write_json(lane_dir / "review_notes.json", review_notes)

    readiness = _lane_readiness(lane_id, shadow_report, adoption_report, gate_specs)
    lane_summary = {
        "lane_id": lane_id,
        "label": config["label"],
        "workflow_id": config["workflow_id"],
        "fixture_path": str(fixture_path),
        "fixture_id": _fixture_id(shadow_report, fixture),
        "shadow_status": _mapping(shadow_report.get("adoption")).get("status"),
        "lane_adoption_status": lane_adoption.get("status"),
        "readiness": readiness,
        "human_gates": gate_specs,
        "review_notes": review_notes,
        "blocked_external_actions": _list(shadow_report.get("blocked_external_actions")),
        "readiness_blockers": _list(shadow_report.get("readiness_blockers")),
        "mutation_permission_granted": False,
        "external_sends_performed": False,
        "artifacts": {
            "shadow_report": str(lane_dir / "shadow_report.json"),
            "adoption_report": str(lane_dir / "adoption_report.json"),
            "receipts": str(lane_dir / "receipts.json"),
            "review_notes": str(lane_dir / "review_notes.json"),
        },
    }
    if lane_id == "ivy":
        lane_summary["public_publish_blocked"] = bool(adoption_report.get("public_publish_blocked"))
    if lane_id == "weekly":
        lane_summary["observed_read_clear"] = _weekly_read_clear_observed(adoption_report)
        lane_summary["read_clear_is_mutation_permission"] = False
        lane_summary["blackboard_or_obsidian_write_allowed"] = False
    return lane_summary


def _publish_review_notes(
    *,
    lane_id: str,
    config: Mapping[str, Any],
    fixture_id: str,
    gate_specs: Sequence[Mapping[str, Any]],
    output_root: Path,
) -> list[dict[str, Any]]:
    if not gate_specs:
        return []

    adapter = LocalMarkdownHumanReviewSurfaceAdapter(
        output_root / "review_notes" / lane_id,
        created_at=DETERMINISTIC_CREATED_AT,
        canonical_surface=str(config["review_surface"]),
    )
    notes: list[dict[str, Any]] = []
    for gate in gate_specs:
        stage_id = str(gate["stage_id"])
        workflow_id = str(gate.get("workflow_id") or config["workflow_id"])
        action_fingerprint = _action_fingerprint(lane_id, fixture_id, gate)
        invocation = AdapterInvocation(
            invocation_id=f"review:{lane_id}:{stage_id}",
            workflow_id=workflow_id,
            instance_id=fixture_id,
            stage_run_id=stage_id,
            adapter_family=AdapterFamily.SURFACE,
            adapter_id=adapter.adapter_id,
            operation="publish",
            input_ref=f"fixture:{fixture_id}",
            context_packet_ref=f"context:{fixture_id}:{stage_id}:local-review",
            idempotency_key=f"{lane_id}:{fixture_id}:{stage_id}",
        )
        publish = adapter.publish(
            invocation,
            {
                "title": f"{config['label']} - {stage_id}",
                "human_ask": _human_ask(lane_id, gate),
                "human_ref": "Suman(test)",
                "stage_id": stage_id,
                "allowed_decisions": gate["allowed_decisions"],
                "exact_action": gate["exact_action"],
                "action_fingerprint": action_fingerprint,
                "evidence_refs": gate["evidence_refs"],
                "test_only": True,
            },
        )
        surface_ref = dict(publish.outputs.get("surface_ref", {}))
        if "note_path" in publish.outputs:
            surface_ref["note_path"] = publish.outputs["note_path"]
        readback = adapter.readback(surface_ref)
        validation = adapter.validate(surface_ref)
        note_path = str(publish.outputs.get("note_path", ""))
        notes.append(
            {
                "lane_id": lane_id,
                "stage_id": stage_id,
                "status": publish.status,
                "note_path": note_path,
                "relative_note_path": _relative_to(output_root, note_path),
                "surface_ref": publish.outputs.get("surface_ref"),
                "publish_receipt_ref": publish.receipt_ref,
                "readback_receipt_id": readback.receipt_id,
                "validation_receipt_id": validation.receipt_id,
                "action_fingerprint": action_fingerprint,
                "allowed_decisions": list(gate["allowed_decisions"]),
                "exact_action": gate["exact_action"],
                "requires_explicit_approval": True,
                "test_only": True,
                "non_live": True,
            }
        )
    return notes


def _human_gate_specs(
    lane_id: str,
    shadow_report: Mapping[str, Any],
    adoption_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if lane_id == "ivy":
        return _ivy_human_gate_specs(shadow_report, adoption_report)
    if lane_id == "weekly":
        return _weekly_human_gate_specs(shadow_report, adoption_report)
    return []


def _ivy_human_gate_specs(
    shadow_report: Mapping[str, Any],
    adoption_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    fixture_id = str(adoption_report.get("fixture_id") or _fixture_id(shadow_report, {}))
    workflow_id = str(adoption_report.get("workflow_id") or "ivy_jonah_editorial")
    for observation in _list(adoption_report.get("stage_observations")):
        if not isinstance(observation, Mapping):
            continue
        requires_gate = bool(observation.get("requires_human_gate")) or str(observation.get("status")) == "needs_human"
        if not requires_gate:
            continue
        stage_id = str(observation.get("stage_id") or "human_gate")
        allowed_decisions = _ivy_allowed_decisions(stage_id)
        evidence_refs = _evidence_refs_for_stage(
            stage_id=stage_id,
            receipt_ids=_list(adoption_report.get("receipt_ids")),
            observations=_list(adoption_report.get("stage_observations")),
        )
        gates.append(
            {
                "lane_id": "ivy",
                "workflow_id": workflow_id,
                "fixture_id": fixture_id,
                "stage_id": stage_id,
                "status": observation.get("status"),
                "outcome": observation.get("outcome"),
                "summary": observation.get("summary"),
                "requires_explicit_approval": True,
                "public_publish_blocked": bool(observation.get("public_publish_blocked")),
                "allowed_decisions": allowed_decisions,
                "exact_action": _ivy_exact_action(stage_id),
                "evidence_refs": evidence_refs,
            }
        )
    return gates


def _weekly_human_gate_specs(
    shadow_report: Mapping[str, Any],
    adoption_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    fixture_id = str(adoption_report.get("fixture_id") or _fixture_id(shadow_report, {}))
    workflow_id = str(adoption_report.get("workflow_id") or "jarvis_weekly_update_shadow")
    for observation in _list(adoption_report.get("observations")):
        if not isinstance(observation, Mapping):
            continue
        metadata = _mapping(observation.get("metadata"))
        is_gate = observation.get("stage_type") == "human_gate" or metadata.get("requires_explicit_approval") is True
        if not is_gate:
            continue
        stage_id = str(observation.get("stage_id") or "suman_review_gate")
        allowed_decisions = _string_list(
            metadata.get("allowed_decisions"),
            fallback=("read_clear", "follow_up_requested", "defer", "blocked"),
        )
        gates.append(
            {
                "lane_id": "weekly",
                "workflow_id": workflow_id,
                "fixture_id": fixture_id,
                "stage_id": stage_id,
                "status": observation.get("status"),
                "outcome": observation.get("outcome"),
                "summary": observation.get("summary"),
                "requires_explicit_approval": True,
                "binds_to": metadata.get("binds_to", "receipts.readback_blackboard_card"),
                "allowed_decisions": allowed_decisions,
                "exact_action": (
                    "Record a local review decision for this Jarvis weekly fixture only; "
                    "read_clear is evidence, not permission to mutate Blackboard, Obsidian, Telegram, or OpenClaw."
                ),
                "evidence_refs": [str(observation.get("receipt_id") or f"fixture:{fixture_id}:{stage_id}")],
            }
        )
    return gates


def _lane_readiness(
    lane_id: str,
    shadow_report: Mapping[str, Any],
    adoption_report: Mapping[str, Any],
    gate_specs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    blockers = _list(shadow_report.get("readiness_blockers"))
    blocker_codes = {str(item.get("code")) for item in blockers if isinstance(item, Mapping)}
    hard_blockers = sorted(code for code in blocker_codes if code != "expected_host_receipt_missing")
    if lane_id == "ivy":
        public_publish_blocked = bool(adoption_report.get("public_publish_blocked"))
        ready_for_shadow = bool(adoption_report.get("ready_for_shadow"))
        if not public_publish_blocked:
            classification = "blocked_public_publish_boundary_broken"
        elif hard_blockers:
            classification = "shadow_review_required"
        elif gate_specs and ready_for_shadow:
            classification = "shadow_ready_human_gate_required"
        elif ready_for_shadow:
            classification = "shadow_ready_no_live_effects"
        else:
            classification = "shadow_review_required"
        return {
            "classification": classification,
            "ready_for_human_review": bool(gate_specs),
            "ready_for_live_onboarding": False,
            "host_receipt_missing": "expected_host_receipt_missing" in blocker_codes,
            "public_publish_blocked": public_publish_blocked,
            "hard_blockers": hard_blockers,
        }

    if lane_id == "weekly":
        read_clear = _weekly_read_clear_observed(adoption_report)
        if gate_specs:
            classification = "waiting_on_human_read_clear"
        elif read_clear:
            classification = "read_clear_shadow_complete_no_mutation"
        elif adoption_report.get("status") == "done":
            classification = "shadow_complete_no_mutation"
        else:
            classification = "shadow_review_required"
        return {
            "classification": classification,
            "ready_for_human_review": bool(gate_specs),
            "ready_for_live_onboarding": False,
            "observed_read_clear": read_clear,
            "read_clear_is_mutation_permission": False,
            "hard_blockers": hard_blockers,
        }

    return {
        "classification": "unsupported_lane",
        "ready_for_human_review": False,
        "ready_for_live_onboarding": False,
        "hard_blockers": hard_blockers,
    }


def _overall_readiness(lanes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    classifications = {
        lane_id: str(_mapping(lane.get("readiness")).get("classification"))
        for lane_id, lane in lanes.items()
    }
    if any(_list(lane.get("human_gates")) for lane in lanes.values()):
        classification = "human_review_required"
    elif any("blocked" in value for value in classifications.values()):
        classification = "blocked"
    else:
        classification = "local_shadow_packet_ready"
    return {
        "classification": classification,
        "lane_classifications": classifications,
        "ready_for_live_onboarding": False,
        "local_review_notes_created": sum(len(_list(lane.get("review_notes"))) for lane in lanes.values()),
        "mutation_permission_granted": False,
    }


def _render_readme(summary: Mapping[str, Any]) -> str:
    lanes = _mapping(summary.get("lanes"))
    lines = [
        "# Two-Lane OpenClaw Onboarding Packet",
        "",
        f"Schema: `{summary.get('schema')}`",
        f"Packet ID: `{summary.get('packet_id')}`",
        f"Overall readiness: `{_mapping(summary.get('overall_readiness')).get('classification')}`",
        "",
        "No live writes, sends, runtime calls, auth changes, cron changes, trading actions, or public publishing were performed.",
        "",
        "## Lane Verdicts",
        "",
    ]
    for lane_id in sorted(lanes):
        lane = _mapping(lanes[lane_id])
        readiness = _mapping(lane.get("readiness"))
        lines.extend(
            [
                f"### {lane.get('label', lane_id)}",
                "",
                f"- Fixture: `{lane.get('fixture_id')}`",
                f"- Workflow: `{lane.get('workflow_id')}`",
                f"- Readiness: `{readiness.get('classification')}`",
                f"- Ready for live onboarding: `{readiness.get('ready_for_live_onboarding')}`",
                f"- Human gates: `{len(_list(lane.get('human_gates')))}`",
                f"- Review notes: `{len(_list(lane.get('review_notes')))}`",
                f"- Mutation permission granted: `{lane.get('mutation_permission_granted')}`",
            ]
        )
        if lane_id == "ivy":
            lines.append(f"- Public publish blocked: `{lane.get('public_publish_blocked')}`")
        if lane_id == "weekly":
            lines.append(f"- Read clear is mutation permission: `{lane.get('read_clear_is_mutation_permission')}`")
        lines.extend(
            [
                f"- Shadow report: `{_mapping(lane.get('artifacts')).get('shadow_report')}`",
                f"- Adoption report: `{_mapping(lane.get('artifacts')).get('adoption_report')}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Local Review Notes",
            "",
        ]
    )
    for lane_id in sorted(lanes):
        lane = _mapping(lanes[lane_id])
        notes = _list(lane.get("review_notes"))
        if not notes:
            lines.append(f"- {lane.get('label', lane_id)}: none required by this fixture.")
            continue
        for note in notes:
            if isinstance(note, Mapping):
                lines.append(
                    f"- {lane.get('label', lane_id)} `{note.get('stage_id')}`: `{note.get('relative_note_path')}`"
                )
    lines.append("")
    return "\n".join(lines)


def _packet_id(lanes: Mapping[str, Mapping[str, Any]]) -> str:
    seed = {
        "schema": PACKET_SCHEMA,
        "lanes": {
            lane_id: {
                "fixture_id": lane.get("fixture_id"),
                "shadow_status": lane.get("shadow_status"),
                "lane_adoption_status": lane.get("lane_adoption_status"),
                "readiness": _mapping(lane.get("readiness")).get("classification"),
            }
            for lane_id, lane in sorted(lanes.items())
        },
    }
    return digest_data(seed)


def _action_fingerprint(lane_id: str, fixture_id: str, gate: Mapping[str, Any]) -> str:
    return digest_data(
        {
            "schema": PACKET_SCHEMA,
            "lane_id": lane_id,
            "fixture_id": fixture_id,
            "stage_id": gate.get("stage_id"),
            "exact_action": gate.get("exact_action"),
            "allowed_decisions": gate.get("allowed_decisions"),
            "evidence_refs": gate.get("evidence_refs"),
            "non_live": True,
        }
    )


def _human_ask(lane_id: str, gate: Mapping[str, Any]) -> str:
    if lane_id == "ivy":
        return (
            "Choose exactly one local review decision for this Ivy/Jonah fixture gate. "
            "This does not authorize public publishing or any external send."
        )
    return (
        "Choose exactly one local review decision for this weekly update fixture gate. "
        "A read_clear choice is review evidence only and does not authorize mutation."
    )


def _ivy_allowed_decisions(stage_id: str) -> list[str]:
    if stage_id == "accept_source_approval":
        return ["selected", "blocked"]
    if stage_id == "p5_final_approval":
        return ["approve_packet", "revise", "park", "reject"]
    return ["approved", "blocked"]


def _ivy_exact_action(stage_id: str) -> str:
    if stage_id == "p5_final_approval":
        return (
            "Review the Ivy/Jonah P5 packet in local AWK shadow only; keep public publish blocked "
            "until a separate explicit live approval exists."
        )
    return (
        "Acknowledge this Ivy/Jonah source approval fixture in local AWK shadow only; "
        "do not mutate OpenClaw or publish externally."
    )


def _evidence_refs_for_stage(
    *,
    stage_id: str,
    receipt_ids: Sequence[Any],
    observations: Sequence[Any],
) -> list[str]:
    stage_ids = [
        str(item.get("stage_id"))
        for item in observations
        if isinstance(item, Mapping) and item.get("stage_id") is not None
    ]
    if stage_id in stage_ids:
        index = stage_ids.index(stage_id)
        if index < len(receipt_ids):
            return [str(receipt_ids[index])]
    return [f"stage:{stage_id}"]


def _weekly_read_clear_observed(adoption_report: Mapping[str, Any]) -> bool:
    for observation in _list(adoption_report.get("observations")):
        if not isinstance(observation, Mapping):
            continue
        if observation.get("stage_id") == "readback_blackboard_card" and observation.get("outcome") == "read_clear":
            return True
    return False


def _fixture_id(shadow_report: Mapping[str, Any], fixture: Mapping[str, Any]) -> str:
    identity = _mapping(shadow_report.get("fixture_identity"))
    return str(identity.get("fixture_id") or fixture.get("fixture_id") or "openclaw-fixture")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"fixture must be a JSON object: {path}")
    return data


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_plain_data(data), sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _canonical_json(data: Mapping[str, Any]) -> str:
    return json.dumps(to_plain_data(data), sort_keys=True, separators=(",", ":")) + "\n"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string_list(value: Any, *, fallback: Sequence[str]) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    return [str(item) for item in fallback]


def _relative_to(root: Path, path: str) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return path


if __name__ == "__main__":
    raise SystemExit(main())
