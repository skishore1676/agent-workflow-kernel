#!/usr/bin/env python3
"""Run a tiny live smoke for AWK's native Codex CLI runtime adapter."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "codex_cli"))

from agent_workflow_kernel import AdapterFamily, AdapterInvocation  # noqa: E402
from agent_workflow_kernel_codex_cli import (  # noqa: E402
    CodexCliExecRuntimeAdapter,
    CodexCliSessionRuntimeAdapter,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-real", action="store_true", help="Actually invoke native codex CLI.")
    parser.add_argument("--mode", choices=("exec", "session", "both"), default="session")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    if not args.run_real:
        parser.error("--run-real is required so token-consuming smoke runs are explicit")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    artifact_root = ROOT / ".awk-live" / "codex-cli-smoke" / run_id
    artifact_root.mkdir(parents=True, exist_ok=True)
    results = []

    if args.mode in {"exec", "both"}:
        adapter = CodexCliExecRuntimeAdapter(
            default_cwd=str(ROOT),
            default_sandbox="read-only",
            default_ask_for_approval="never",
            default_model=args.model,
            timeout_seconds=args.timeout_seconds,
        )
        result = adapter.invoke(
            _invocation(adapter.adapter_id, "exec-1"),
            {
                "prompt": (
                    'AWK Codex CLI smoke. Reply with JSON exactly: '
                    '{"status":"ok","mode":"exec"}. Do not call tools.'
                ),
                "codex_cli": {"artifact_dir": str(artifact_root / "exec")},
            },
        )
        results.append(_summary("exec", result))

    if args.mode in {"session", "both"}:
        nonce = str(uuid4())
        adapter = CodexCliSessionRuntimeAdapter(
            default_cwd=str(ROOT),
            default_sandbox="read-only",
            default_ask_for_approval="never",
            default_model=args.model,
            timeout_seconds=args.timeout_seconds,
        )
        first = adapter.invoke(
            _invocation(adapter.adapter_id, "session-1"),
            {
                "prompt": (
                    f"AWK bounded-session smoke. Remember nonce {nonce}. "
                    'Reply with JSON exactly: {"status":"ok","step":"remembered"}. '
                    "Do not call tools."
                ),
                "actor_ref": "codex_cli_smoke_worker",
                "codex_cli": {
                    "artifact_dir": str(artifact_root / "session"),
                    "max_session_turns": 5,
                },
            },
        )
        second = adapter.invoke(
            _invocation(adapter.adapter_id, "session-2"),
            {
                "prompt": (
                    "Using only this conversation's prior context, reply with "
                    f'JSON exactly: {{"status":"ok","step":"recalled","nonce":"{nonce}"}}. '
                    "Do not call tools."
                ),
                "actor_ref": "codex_cli_smoke_worker",
                "codex_cli": {
                    "artifact_dir": str(artifact_root / "session"),
                    "max_session_turns": 5,
                },
            },
        )
        results.append(_summary("session_first", first))
        results.append(_summary("session_second", second))

    packet = {
        "schema": "codex_cli_runtime_smoke.v1",
        "run_id": run_id,
        "artifact_root": str(artifact_root),
        "results": results,
    }
    print(json.dumps(packet, indent=2, sort_keys=True))
    if any(result["status"] != "succeeded" for result in results):
        return 1
    return 0


def _invocation(adapter_id: str, suffix: str) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"codex-cli-smoke-{suffix}",
        workflow_id="codex-cli-smoke",
        instance_id="codex-cli-smoke",
        stage_run_id=f"stage-{suffix}",
        adapter_family=AdapterFamily.RUNTIME,
        adapter_id=adapter_id,
        operation="invoke",
        idempotency_key=f"codex-cli-smoke:{suffix}",
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
    }


if __name__ == "__main__":
    raise SystemExit(main())
