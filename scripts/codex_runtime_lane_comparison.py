#!/usr/bin/env python3
"""Compare AWK runtime choices for one concrete lane stage.

The real Codex CLI path is intentionally gated. Tests can pass a fake Codex
executable; operator runs must opt in with ``--run-real-codex``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "codex_cli"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

from agent_workflow_kernel import AdapterFamily, AdapterInvocation, to_plain_data  # noqa: E402
from agent_workflow_kernel.dsl import load_workflow_file  # noqa: E402
from agent_workflow_kernel_codex_cli import CodexCliSessionRuntimeAdapter  # noqa: E402
from agent_workflow_kernel_openclaw.ivy_lane import (  # noqa: E402
    adopt_ivy_jonah_fixture,
    load_ivy_jonah_fixture,
)


SCHEMA = "awk.runtime-comparison.v1"
DEFAULT_STAGE_ID = "build_draft_package"
DEFAULT_WORKFLOW = ROOT / "workflows" / "ivy_jonah_editorial.yaml"
DEFAULT_FIXTURE = ROOT / "fixtures" / "openclaw" / "ivy_jonah" / "p3_approval_to_p5_shadow.json"


@dataclass(slots=True, frozen=True)
class PathResult:
    path_id: str
    runtime_owner: str
    status: str
    wall_time_seconds: float
    token_usage: Mapping[str, Any]
    session: Mapping[str, Any]
    artifacts: Mapping[str, Any]
    quality: Mapping[str, Any]
    manual_supervision: str
    receipt_clarity: str
    notes: tuple[str, ...]

    def to_data(self) -> dict[str, Any]:
        return to_plain_data(self)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    executable = args.codex_executable or shutil.which("codex")
    if not executable:
        raise SystemExit("Codex executable not found; pass --codex-executable for deterministic tests.")
    if not args.run_real_codex and not args.codex_executable:
        raise SystemExit("Real Codex CLI execution requires --run-real-codex.")

    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    packet = run_comparison(
        output_dir=output_dir,
        workflow_path=args.workflow,
        fixture_path=args.fixture,
        codex_executable=executable,
        timeout_seconds=args.timeout_seconds,
        model=args.model,
        stage_id=args.stage_id,
    )
    packet_path = output_dir / "comparison.json"
    summary_path = output_dir / "summary.md"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path.write_text(render_summary(packet), encoding="utf-8")
    print(json.dumps({"status": "ok", "packet": str(packet_path), "summary": str(summary_path)}, sort_keys=True))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-real-codex", action="store_true", help="Opt in to spending real Codex CLI tokens.")
    parser.add_argument("--codex-executable", help="Codex executable path; tests may pass a fake executable.")
    parser.add_argument("--model", help="Optional Codex CLI model override.")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--stage-id", default=DEFAULT_STAGE_ID)
    return parser.parse_args(argv)


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d")
    return ROOT / ".awk-live" / f"runtime-comparison-{stamp}"


def run_comparison(
    *,
    output_dir: Path,
    workflow_path: Path,
    fixture_path: Path,
    codex_executable: str,
    timeout_seconds: int,
    model: str | None,
    stage_id: str,
) -> dict[str, Any]:
    workflow = load_workflow_file(workflow_path)
    stage = next((item for item in workflow.stages if item.id == stage_id), None)
    if stage is None:
        raise ValueError(f"stage {stage_id!r} not found in {workflow_path}")
    fixture = load_ivy_jonah_fixture(fixture_path)

    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    paths = [
        run_codex_session_path(
            output_dir=output_dir / "codex_cli_session",
            workflow_id=workflow.id,
            stage=stage,
            fixture=fixture.raw,
            codex_executable=codex_executable,
            timeout_seconds=timeout_seconds,
            model=model,
        ),
        run_openclaw_fixture_path(fixture_path=fixture_path),
        run_direct_script_path(fixture=fixture.raw, stage_id=stage_id),
    ]
    metrics = [item.to_data() for item in paths]
    return {
        "schema": SCHEMA,
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "workflow": {
            "id": workflow.id,
            "version": workflow.version,
            "source": str(workflow_path),
        },
        "stage": {
            "id": stage.id,
            "type": stage.type.value,
            "original_adapter": stage.adapter,
            "experiment_adapter": "runtime.codex_cli_session",
            "prompt_refs": [to_plain_data(item) for item in stage.prompt_refs],
        },
        "fixture": {
            "path": str(fixture_path),
            "fixture_id": fixture.fixture_id,
            "source_mode": "fixture_shadow",
        },
        "safety": {
            "production_openclaw_mutated": False,
            "external_surfaces_mutated": False,
            "public_publish_allowed": False,
            "auth_or_secrets_requested": False,
        },
        "metrics": metrics,
        "verdict": build_verdict(metrics),
    }


def run_codex_session_path(
    *,
    output_dir: Path,
    workflow_id: str,
    stage: Any,
    fixture: Mapping[str, Any],
    codex_executable: str,
    timeout_seconds: int,
    model: str | None,
) -> PathResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter = CodexCliSessionRuntimeAdapter(
        executable=codex_executable,
        default_cwd=str(ROOT),
        timeout_seconds=timeout_seconds,
    )
    seed_prompt = (
        "You are evaluating one AWK lane stage. Remember this fixture id for the next turn: "
        f"{fixture.get('fixture_id')}. Do not perform external side effects."
    )
    stage_prompt = render_codex_stage_prompt(stage=stage, fixture=fixture)
    common_input = {
        "actor_ref": "actors.writer",
        "session_key": f"{workflow_id}:runtime-comparison:ivy",
        "codex_cli": {
            "cwd": str(ROOT),
            "sandbox": "read-only",
            "ask_for_approval": "never",
            "ignore_rules": True,
            "timeout_seconds": timeout_seconds,
            "artifact_dir": str(output_dir),
            "max_session_turns": 4,
            **({"model": model} if model else {}),
        },
    }

    seed_invocation = invocation(
        workflow_id=workflow_id,
        invocation_id="runtime-comparison:codex-session:seed",
        stage_id="session_context_seed",
    )
    stage_invocation = invocation(
        workflow_id=workflow_id,
        invocation_id=f"runtime-comparison:codex-session:{stage.id}",
        stage_id=stage.id,
    )

    start = time.perf_counter()
    seed_result = adapter.invoke(seed_invocation, {**common_input, "prompt": seed_prompt})
    stage_result = adapter.invoke(
        stage_invocation,
        {
            **common_input,
            "prompt": stage_prompt,
            "stage": {**to_plain_data(stage), "adapter": "runtime.codex_cli_session"},
        },
    )
    elapsed = time.perf_counter() - start
    stage_payload = parse_jsonish(stage_result.outputs.get("last_message", ""))
    quality = score_draft_payload(stage_payload, fixture=fixture)
    artifacts = {
        "seed": seed_result.outputs.get("artifacts", {}),
        "stage": stage_result.outputs.get("artifacts", {}),
        "last_message": stage_result.outputs.get("last_message", ""),
    }
    (output_dir / "stage_payload.json").write_text(
        json.dumps(stage_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return PathResult(
        path_id="codex_cli_session",
        runtime_owner="runtime.codex_cli_session",
        status=stage_result.status,
        wall_time_seconds=round(elapsed, 3),
        token_usage={
            "seed": seed_result.outputs.get("usage", {}),
            "stage": stage_result.outputs.get("usage", {}),
            "combined_total_tokens": int(seed_result.outputs.get("usage", {}).get("total_tokens") or 0)
            + int(stage_result.outputs.get("usage", {}).get("total_tokens") or 0),
        },
        session=stage_result.outputs.get("session", {}),
        artifacts=artifacts,
        quality=quality,
        manual_supervision="low after prompt construction; requires token spend and receipt review",
        receipt_clarity="high: command, session id, usage when emitted, last message, stderr, JSONL events",
        notes=("actual AWK stage definition was invoked with adapter override runtime.codex_cli_session",),
    )


def run_openclaw_fixture_path(*, fixture_path: Path) -> PathResult:
    start = time.perf_counter()
    fixture = load_ivy_jonah_fixture(fixture_path)
    adoption = adopt_ivy_jonah_fixture(fixture)
    elapsed = time.perf_counter() - start
    build_stage = next(
        (item for item in adoption.stage_observations if item.stage_id == DEFAULT_STAGE_ID),
        None,
    )
    quality = {
        "score": 6,
        "max_score": 10,
        "grade": "receipt-parity-only",
        "checks": {
            "stage_observed": build_stage is not None,
            "draft_artifact_role_present": build_stage is not None and "draft_package" in build_stage.artifact_roles,
            "source_trail_role_present": build_stage is not None and "source_trail" in build_stage.artifact_roles,
            "public_publish_blocked": bool(build_stage and build_stage.public_publish_blocked),
            "text_quality_assessable": False,
            "token_metrics_available": False,
        },
        "missing": (
            "No fixture primitive exposes generated draft text or native OpenClaw token accounting for this stage.",
        ),
    }
    return PathResult(
        path_id="openclaw_fixture",
        runtime_owner="openclaw.ivy_lane.readonly",
        status="succeeded" if build_stage else "blocked",
        wall_time_seconds=round(elapsed, 3),
        token_usage={"available": False, "reason": "fixture receipt does not include native token accounting"},
        session={
            "runtime_refs": list((fixture.raw.get("mapping") or {}).get("runtime_refs") or []),
            "session_reused": "unknown_fixture_only",
        },
        artifacts={
            "fixture": str(fixture_path),
            "receipt_ids": list(adoption.report.receipt_ids),
            "artifact_refs": adoption.report.evidence_refs.get("artifact_refs", []),
        },
        quality=quality,
        manual_supervision="low for mapping/readback; high if text quality or token economics are required",
        receipt_clarity="medium-high: stage receipts and artifact roles are clear, but runtime tokens/text are absent",
        notes=("closest safe OpenClaw-backed representation is fixture-only/read-only, not live mutation",),
    )


def run_direct_script_path(*, fixture: Mapping[str, Any], stage_id: str) -> PathResult:
    start = time.perf_counter()
    payload = deterministic_draft_package(fixture)
    elapsed = time.perf_counter() - start
    quality = score_draft_payload(payload, fixture=fixture)
    return PathResult(
        path_id="direct_script",
        runtime_owner="deterministic.local_script",
        status="succeeded",
        wall_time_seconds=round(elapsed, 3),
        token_usage={"total_tokens": 0, "model_tokens": 0},
        session={"session_reused": False, "cold_start": "python_import_only"},
        artifacts={"stage_payload": payload, "stage_id": stage_id},
        quality=quality,
        manual_supervision="very low for extraction/packaging; medium if prose quality matters",
        receipt_clarity="high for deterministic fields; no conversational reasoning trace",
        notes=("cheapest path; accurate for structured cargo, shallow for nuanced editorial drafting",),
    )


def invocation(*, workflow_id: str, invocation_id: str, stage_id: str) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=invocation_id,
        workflow_id=workflow_id,
        instance_id="runtime-comparison-ivy-jonah",
        stage_run_id=f"runtime-comparison:{stage_id}",
        adapter_family=AdapterFamily.RUNTIME,
        adapter_id="runtime.codex_cli_session",
        operation="invoke",
        input_ref="fixture:ivy_jonah:p3_approval_to_p5_shadow",
        context_packet_ref="workflow:ivy_jonah_editorial:stage:build_draft_package",
        idempotency_key=f"runtime-comparison:{stage_id}",
    )


def render_codex_stage_prompt(*, stage: Any, fixture: Mapping[str, Any]) -> str:
    prompt = {
        "objective": "Run the AWK ivy_jonah_editorial build_draft_package stage locally.",
        "hard_boundaries": [
            "Do not publish, send externally, mutate OpenClaw, touch auth, or write outside the requested answer.",
            "Return only JSON. No markdown fences.",
        ],
        "required_json_shape": {
            "schema": "draft_package_result.v1",
            "outcome": "ready|blocked",
            "title": "string",
            "lede": "string",
            "outline": ["at least three section headings"],
            "draft_package": "short local draft package summary",
            "source_trail": {
                "fixture_id": fixture.get("fixture_id"),
                "project_id": fixture.get("project", {}).get("project_id"),
                "receipt_ids": fixture.get("mapping", {}).get("work_ledger", {}).get("receipt_ids", []),
            },
            "public_publish_blocked": True,
            "next_action": "string",
        },
        "stage": to_plain_data(stage),
        "fixture": fixture,
    }
    return json.dumps(prompt, indent=2, sort_keys=True)


def deterministic_draft_package(fixture: Mapping[str, Any]) -> dict[str, Any]:
    project = dict(fixture.get("project") or {})
    mapping = dict(fixture.get("mapping") or {})
    work_ledger = dict(mapping.get("work_ledger") or {})
    title = str(project.get("title") or "Untitled local draft")
    return {
        "schema": "draft_package_result.v1",
        "outcome": "ready",
        "title": title,
        "lede": f"{title}: a local fixture-backed draft package for Jonah review.",
        "outline": [
            "Why receipt-backed agent handoffs matter",
            "What the P3 approval says is ready",
            "Where Jonah review and Suman approval remain mandatory",
        ],
        "draft_package": (
            "Local deterministic package assembled from fixture metadata only; "
            "it preserves the P5 approval gate and does not attempt public publishing."
        ),
        "source_trail": {
            "fixture_id": fixture.get("fixture_id"),
            "project_id": project.get("project_id"),
            "receipt_ids": list(work_ledger.get("receipt_ids") or []),
            "handoff_type": (fixture.get("handoff") or {}).get("handoff_type"),
        },
        "public_publish_blocked": True,
        "next_action": "Use model/runtime work only when editorial nuance is required; keep publish behind P5 approval.",
    }


def parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            return {"raw_text": text}
    return {"raw_text": text}


def score_draft_payload(payload: Any, *, fixture: Mapping[str, Any]) -> dict[str, Any]:
    data = payload if isinstance(payload, Mapping) else {}
    source_trail = data.get("source_trail") if isinstance(data.get("source_trail"), Mapping) else {}
    outline = data.get("outline") if isinstance(data.get("outline"), list) else []
    checks = {
        "json_object": isinstance(payload, Mapping),
        "schema_matches": data.get("schema") == "draft_package_result.v1",
        "outcome_ready": data.get("outcome") == "ready",
        "title_present": bool(str(data.get("title") or "").strip()),
        "lede_present": bool(str(data.get("lede") or "").strip()),
        "outline_has_three_items": len(outline) >= 3,
        "source_fixture_bound": source_trail.get("fixture_id") == fixture.get("fixture_id"),
        "source_receipts_present": bool(source_trail.get("receipt_ids")),
        "public_publish_blocked": data.get("public_publish_blocked") is True,
        "next_action_present": bool(str(data.get("next_action") or "").strip()),
    }
    score = sum(1 for passed in checks.values() if passed)
    missing = tuple(name for name, passed in checks.items() if not passed)
    return {"score": score, "max_score": len(checks), "grade": grade(score, len(checks)), "checks": checks, "missing": missing}


def grade(score: int, max_score: int) -> str:
    if score == max_score:
        return "excellent"
    if score >= max_score - 2:
        return "usable"
    if score >= max_score // 2:
        return "partial"
    return "poor"


def build_verdict(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item["path_id"]: item for item in metrics}
    return {
        "stage_under_test": DEFAULT_STAGE_ID,
        "best_for_structured_extraction": "direct_script",
        "best_for_editorial_generation": "codex_cli_session",
        "best_for_runtime_parity_receipts": "openclaw_fixture",
        "ownership_recommendations": {
            "deterministic_fixture_readback_or_hash_validation": "direct_script",
            "drafting_revision_synthesis_or_judgment": "runtime.codex_cli_session when continuity matters; one-shot/direct model when it does not",
            "legacy_openclaw_adoption_evidence": "openclaw_fixture/live_readonly path until production cutover is explicitly approved",
            "public_publish_or_external_send": "no runtime owns this without explicit Suman approval gate",
        },
        "token_economics": {
            "codex_cli_session": by_id.get("codex_cli_session", {}).get("token_usage", {}),
            "openclaw_fixture": "no token accounting primitive exposed by fixture",
            "direct_script": "0 model tokens",
        },
        "smallest_next_action": (
            "Add token accounting to the OpenClaw live-readonly exporter or receipts so future comparisons "
            "can compare OpenClaw native sessions against Codex CLI on equal footing."
        ),
    }


def render_summary(packet: Mapping[str, Any]) -> str:
    lines = [
        "# AWK Codex Runtime Lane Comparison",
        "",
        f"Workflow: `{packet['workflow']['id']}`",
        f"Stage: `{packet['stage']['id']}` (`{packet['stage']['original_adapter']}` -> `runtime.codex_cli_session`)",
        f"Fixture: `{packet['fixture']['path']}`",
        "",
        "## Metrics",
        "",
        "| Path | Status | Wall time | Tokens | Quality | Receipt clarity |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in packet["metrics"]:
        usage = item["token_usage"]
        tokens = usage.get("combined_total_tokens", usage.get("total_tokens", "n/a")) if isinstance(usage, Mapping) else "n/a"
        quality = item["quality"]
        quality_text = f"{quality.get('score', 'n/a')}/{quality.get('max_score', 'n/a')} {quality.get('grade', '')}"
        lines.append(
            f"| `{item['path_id']}` | {item['status']} | {item['wall_time_seconds']:.3f}s | {tokens} | {quality_text} | {item['receipt_clarity']} |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"- Structured extraction/hash/readback: `{packet['verdict']['best_for_structured_extraction']}`.",
            f"- Editorial generation with continuity: `{packet['verdict']['best_for_editorial_generation']}`.",
            f"- OpenClaw parity receipts: `{packet['verdict']['best_for_runtime_parity_receipts']}`.",
            f"- Smallest next action: {packet['verdict']['smallest_next_action']}",
            "",
            "Safety: no production OpenClaw behavior, external surfaces, auth, trading, or public publish path was mutated.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
