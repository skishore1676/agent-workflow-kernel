#!/usr/bin/env python3
"""Apply the automated Suman reviewer to a local OpenClaw AWK packet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
KERNEL_PATH = ROOT / "packages" / "kernel"
if str(KERNEL_PATH) not in sys.path:
    sys.path.insert(0, str(KERNEL_PATH))

from agent_workflow_kernel import (  # noqa: E402
    AutomatedSumanReviewer,
    LocalMarkdownHumanReviewSurfaceAdapter,
    to_plain_data,
)


AUTO_REVIEW_SCHEMA = "workflow.kernel.openclaw-auto-review-packet.v1"
DETERMINISTIC_CREATED_AT = "2000-01-01T00:00:00Z"
LANES = ("ivy", "weekly")
FORBIDDEN_LIVE_EFFECTS = (
    "openclaw_runtime_mutation",
    "operator_surface_write",
    "obsidian_or_northstar_write",
    "telegram_send",
    "public_publish",
    "auth_or_secret_access",
    "deploy",
    "trading_or_money_action",
    "destructive_action",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        summary = auto_review_packet(
            packet_dir=args.packet_dir,
            output_dir=args.output_dir,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "summary_path": summary["artifacts"]["summary_json"],
                "readme_path": summary["artifacts"]["readme"],
                "reviewed_notes": summary["reviewed_notes"],
                "approved_decisions": summary["approved_decisions"],
                "blocked_decisions": summary["blocked_decisions"],
                "mutation_permission_granted": False,
            },
            sort_keys=True,
        ),
        end="",
    )
    return 0


def auto_review_packet(*, packet_dir: str | Path, output_dir: str | Path) -> dict[str, Any]:
    packet_root = Path(packet_dir).resolve()
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    receipt_dir = output_root / "reviewer_receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)

    reviewer = AutomatedSumanReviewer(
        created_at=DETERMINISTIC_CREATED_AT,
        receipt_dir=receipt_dir,
    )
    lane_results: dict[str, Any] = {}
    decisions: list[dict[str, Any]] = []
    for lane in LANES:
        lane_notes = _load_lane_notes(packet_root, lane)
        lane_dir = packet_root / "review_notes" / lane
        adapter = LocalMarkdownHumanReviewSurfaceAdapter(
            lane_dir,
            created_at=DETERMINISTIC_CREATED_AT,
            canonical_surface=f"local_markdown_{lane}_auto_review",
        )
        lane_decisions: list[dict[str, Any]] = []
        for note in lane_notes:
            note_result = _review_note(
                reviewer=reviewer,
                adapter=adapter,
                note=note,
                packet_root=packet_root,
                lane=lane,
            )
            lane_decisions.append(note_result)
            decisions.append(note_result)
        lane_results[lane] = {
            "review_notes_index": str(packet_root / "lanes" / lane / "review_notes.json"),
            "decisions": lane_decisions,
            "decision_count": len(lane_decisions),
        }

    summary = {
        "schema": AUTO_REVIEW_SCHEMA,
        "packet_dir": str(packet_root),
        "status": _overall_status(decisions),
        "reviewed_notes": len(decisions),
        "approved_decisions": [
            item for item in decisions if item.get("review_decision") in {"selected", "approve_packet", "read_clear"}
        ],
        "blocked_decisions": [
            item for item in decisions if item.get("review_status") != "succeeded"
        ],
        "lanes": lane_results,
        "safety": {
            "test_only": True,
            "non_live": True,
            "local_output_only": True,
            "mutation_permission_granted": False,
            "operator_surface_writes_performed": False,
            "telegram_sends_performed": False,
            "public_publish_performed": False,
            "forbidden_live_effects": list(FORBIDDEN_LIVE_EFFECTS),
        },
        "artifacts": {
            "output_dir": str(output_root),
            "summary_json": str(output_root / "summary.json"),
            "readme": str(output_root / "README.md"),
            "reviewer_receipts": str(receipt_dir),
        },
    }
    _write_json(output_root / "summary.json", summary)
    (output_root / "README.md").write_text(_render_readme(summary), encoding="utf-8")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automate local/test-only review decisions for an OpenClaw AWK onboarding packet."
    )
    parser.add_argument("--packet-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def _load_lane_notes(packet_root: Path, lane: str) -> list[Mapping[str, Any]]:
    path = packet_root / "lanes" / lane / "review_notes.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a list")
    return [item for item in data if isinstance(item, Mapping)]


def _review_note(
    *,
    reviewer: AutomatedSumanReviewer,
    adapter: LocalMarkdownHumanReviewSurfaceAdapter,
    note: Mapping[str, Any],
    packet_root: Path,
    lane: str,
) -> dict[str, Any]:
    note_path = Path(str(note["note_path"])).resolve()
    context = {
        "test_only": bool(note.get("test_only", True)),
        "non_live": bool(note.get("non_live", True)),
        "public_publish_blocked": True,
        "required_artifacts": _required_artifacts(packet_root, lane),
        "adoption_blockers": (),
    }
    review = reviewer.review_human_gate_surface(
        surface_ref={"note_path": str(note_path)},
        context=context,
    )
    ingest_receipts = adapter.ingest_decisions(
        {
            "query_id": f"auto-review:{lane}:{note.get('stage_id')}",
            "note_path": str(note_path),
            "allowed_decisions": tuple(str(item) for item in note.get("allowed_decisions", ())),
            "exact_action": str(note.get("exact_action") or ""),
            "expected_action_fingerprint": str(note.get("action_fingerprint") or ""),
            "evidence_refs": tuple(_evidence_refs(note)),
            "human_ref": getattr(review, "human_ref", "Suman(test automated reviewer)"),
            "gate_id": str(note.get("stage_id") or ""),
        }
    )
    decision_receipts = [to_plain_data(receipt) for receipt in ingest_receipts]
    decision = _decision_from_receipts(decision_receipts)
    return {
        "lane_id": lane,
        "stage_id": str(note.get("stage_id") or ""),
        "note_path": str(note_path),
        "review_status": getattr(review, "status", "blocked"),
        "review_decision": decision,
        "reviewer_human_ref": getattr(review, "human_ref", None),
        "review_receipt_path": getattr(review, "receipt_path", None),
        "ingest_receipts": decision_receipts,
        "mutation_permission_granted": False,
        "operator_surface_write_performed": False,
    }


def _required_artifacts(packet_root: Path, lane: str) -> tuple[str, ...]:
    lane_dir = packet_root / "lanes" / lane
    return tuple(
        str(path)
        for path in (
            lane_dir / "shadow_report.json",
            lane_dir / "adoption_report.json",
            lane_dir / "lane_report.json",
        )
    )


def _evidence_refs(note: Mapping[str, Any]) -> list[str]:
    readback = note.get("readback")
    refs = []
    if isinstance(readback, Mapping) and readback.get("receipt_id"):
        refs.append(str(readback["receipt_id"]))
    if note.get("readback_receipt_id"):
        refs.append(str(note["readback_receipt_id"]))
    return sorted(set(refs))


def _decision_from_receipts(receipts: Sequence[Mapping[str, Any]]) -> str | None:
    for receipt in receipts:
        outputs = receipt.get("runtime_provenance", {}).get("outputs")
        if isinstance(outputs, Mapping) and outputs.get("decision"):
            return str(outputs["decision"])
    return None


def _overall_status(decisions: Sequence[Mapping[str, Any]]) -> str:
    if not decisions:
        return "no_review_notes"
    if any(item.get("review_status") != "succeeded" for item in decisions):
        return "blocked"
    return "reviewed"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _render_readme(summary: Mapping[str, Any]) -> str:
    lines = [
        "# OpenClaw AWK Automated Suman Review Packet",
        "",
        f"- Status: `{summary['status']}`",
        f"- Packet: `{summary['packet_dir']}`",
        f"- Reviewed notes: `{summary['reviewed_notes']}`",
        f"- Mutation permission granted: `{summary['safety']['mutation_permission_granted']}`",
        f"- Operator surface writes performed: `{summary['safety']['operator_surface_writes_performed']}`",
        "",
        "## Decisions",
        "",
    ]
    for lane, lane_summary in summary["lanes"].items():
        lines.append(f"### {lane}")
        for decision in lane_summary["decisions"]:
            lines.append(
                "- `{stage}`: `{decision}` via `{human}`".format(
                    stage=decision["stage_id"],
                    decision=decision["review_decision"],
                    human=decision["reviewer_human_ref"],
                )
            )
        lines.append("")
    lines.extend(
        [
            "## Safety",
            "",
            "This packet is local/test-only. It does not authorize live OpenClaw mutation,",
            "Obsidian/Northstar writes, Telegram sends, public publish, auth/deploy changes,",
            "trading actions, or destructive actions.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
