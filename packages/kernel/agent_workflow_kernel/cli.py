"""Command line interface for the portable workflow kernel."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .dsl import load_workflow_file, workflow_to_canonical_json
from .local_runner import run_local_workflow


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return _validate(args.workflow)
        if args.command == "compile":
            return _compile(args.workflow)
        if args.command == "run-local":
            return _run_local(
                args.workflow,
                ledger=args.ledger,
                max_steps=args.max_steps,
            )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    parser.print_help(sys.stderr)
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agent_workflow_kernel.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate a workflow YAML file")
    validate_parser.add_argument("workflow", type=Path)

    compile_parser = subparsers.add_parser("compile", help="print canonical workflow JSON")
    compile_parser.add_argument("workflow", type=Path)

    run_parser = subparsers.add_parser("run-local", help="run a workflow with local fakes")
    run_parser.add_argument("workflow", type=Path)
    run_parser.add_argument(
        "--ledger",
        type=Path,
        help="SQLite ledger path to create or reuse",
    )
    run_parser.add_argument("--max-steps", type=int, default=50)
    return parser


def _validate(workflow_path: Path) -> int:
    workflow = load_workflow_file(workflow_path)
    print(
        json.dumps(
            {
                "ok": True,
                "workflow_id": workflow.id,
                "workflow_version": workflow.version,
                "stages": len(workflow.stages),
                "transitions": len(workflow.transitions),
            },
            sort_keys=True,
        )
    )
    return 0


def _compile(workflow_path: Path) -> int:
    workflow = load_workflow_file(workflow_path)
    print(workflow_to_canonical_json(workflow))
    return 0


def _run_local(workflow_path: Path, *, ledger: Path | None, max_steps: int) -> int:
    summary = run_local_workflow(workflow_path, ledger_path=ledger, max_steps=max_steps)
    print(json.dumps(summary.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
