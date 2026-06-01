#!/usr/bin/env python3
"""Import OpenClaw Blackboard acknowledgement state into an AWK owned ledger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
KERNEL_PATH = ROOT / "packages" / "kernel"
OPENCLAW_ADAPTER_PATH = ROOT / "packages" / "adapters" / "openclaw"
for package_path in (str(KERNEL_PATH), str(OPENCLAW_ADAPTER_PATH)):
    if package_path not in sys.path:
        sys.path.insert(0, package_path)

from agent_workflow_kernel_openclaw import run_owned_completion_bridge  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prove AWK-owned terminal completion for migrated OpenClaw Blackboard work IDs."
    )
    parser.add_argument("--openclaw-root", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--cutover-receipt", type=Path)
    parser.add_argument("--artifact-id", action="append", default=[])
    parser.add_argument("--summary-json", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = run_owned_completion_bridge(
            ledger_path=args.ledger,
            openclaw_root=args.openclaw_root,
            cutover_receipt_path=args.cutover_receipt,
            artifact_ids=tuple(args.artifact_id),
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
