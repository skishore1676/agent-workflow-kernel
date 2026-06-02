#!/usr/bin/env python3
"""Run a local live smoke for AWK's Codex SDK session runtime adapter."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "codex_sdk"))

from agent_workflow_kernel import AdapterFamily, AdapterInvocation  # noqa: E402
from agent_workflow_kernel_codex_sdk import CodexSdkSessionRuntimeAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-real", action="store_true", help="Actually invoke live Codex SDK.")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--model", default=None)
    parser.add_argument("--sandbox", default="read-only")
    args = parser.parse_args()

    if not args.run_real:
        parser.error("--run-real is required so token-consuming smoke runs are explicit")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    artifact_root = ROOT / ".awk-live" / "codex-sdk-smoke" / run_id
    fixture_root = artifact_root / "fixture"
    artifact_root.mkdir(parents=True, exist_ok=True)
    fixture_root.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_root / "invoice_review.py"
    fixture_path.write_text(
        "\n".join(
            [
                "def summarize_invoices(invoices):",
                "    total = 0",
                "    overdue = []",
                "    for invoice in invoices:",
                "        total += invoice['amount']",
                "        if invoice.get('status') == 'overdue':",
                "            overdue.append(invoice['id'])",
                "    return {'total': total, 'overdue': overdue}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    adapter = CodexSdkSessionRuntimeAdapter(
        default_cwd=str(ROOT),
        default_sandbox=args.sandbox,
        default_approval_mode="deny_all",
        default_model=args.model,
        timeout_seconds=args.timeout_seconds,
    )
    common = {
        "actor_ref": "codex_sdk_smoke_worker",
        "codex_sdk": {
            "artifact_dir": str(artifact_root / "sdk"),
            "max_session_turns": 5,
            "approval_mode": "deny_all",
            "sandbox": args.sandbox,
        },
    }
    first = adapter.invoke(
        _invocation(adapter.adapter_id, "inspect-plan"),
        {
            **common,
            "prompt": (
                "AWK Codex SDK smoke, turn 1. Inspect this local fixture path: "
                f"{fixture_path}. Do not edit files. Return JSON with keys "
                "status, observed_behavior, patch_plan, and risk. The patch_plan "
                "should be a short list of concrete recommendations."
            ),
        },
    )
    second = adapter.invoke(
        _invocation(adapter.adapter_id, "structured-review"),
        {
            **common,
            "prompt": (
                "AWK Codex SDK smoke, turn 2. Continue the same thread. Based on "
                "the prior inspection, return JSON with keys status, thread_continuity, "
                "recommended_patch, tests_to_add, and verdict. Do not edit files."
            ),
        },
    )

    packet = {
        "schema": "codex_sdk_runtime_smoke.v1",
        "run_id": run_id,
        "artifact_root": str(artifact_root),
        "fixture_path": str(fixture_path),
        "results": [
            _summary("inspect_plan", first),
            _summary("structured_review", second),
        ],
    }
    summary_path = artifact_root / "summary.json"
    summary_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
    packet["summary_path"] = str(summary_path)
    print(json.dumps(packet, indent=2, sort_keys=True))
    if first.status != "succeeded" or second.status != "succeeded":
        return 1
    return 0


def _invocation(adapter_id: str, suffix: str) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"codex-sdk-smoke-{suffix}",
        workflow_id="codex-sdk-smoke",
        instance_id="codex-sdk-smoke",
        stage_run_id=f"stage-{suffix}",
        adapter_family=AdapterFamily.RUNTIME,
        adapter_id=adapter_id,
        operation="invoke",
        idempotency_key=f"codex-sdk-smoke:{suffix}",
    )


def _summary(label: str, result: object) -> dict[str, object]:
    outputs = getattr(result, "outputs")
    return {
        "label": label,
        "status": getattr(result, "status"),
        "mode": outputs.get("mode"),
        "session": outputs.get("session"),
        "usage": outputs.get("usage"),
        "artifacts": outputs.get("artifacts"),
        "structured_result": outputs.get("structured_result"),
        "error": outputs.get("error"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
