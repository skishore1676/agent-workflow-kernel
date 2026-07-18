#!/usr/bin/env python3
"""Build and smoke-test AWK as an isolated wheel artifact.

This is intentionally independent of the checkout import path.  It catches
the common false green where source tests import an adapter package that
setuptools never put in the wheel.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PACKAGE_ROOTS = {
    "agent_workflow_kernel",
    "agent_workflow_kernel_a2a",
    "agent_workflow_kernel_artifact_validation",
    "agent_workflow_kernel_codex_cli",
    "agent_workflow_kernel_codex_sdk",
    "agent_workflow_kernel_ivy",
    "agent_workflow_kernel_openclaw",
    "agent_workflow_kernel_x_digest",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, help="optional wheel output directory")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="awk-wheel-") as temporary:
        scratch = Path(temporary)
        wheelhouse = args.wheelhouse or scratch / "wheelhouse"
        wheelhouse.mkdir(parents=True, exist_ok=True)
        _run([sys.executable, "-m", "pip", "wheel", "--no-deps", ".", "-w", str(wheelhouse)])
        wheels = sorted(wheelhouse.glob("agent_workflow_kernel-0.4.0-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected exactly one 0.4.0 kernel wheel, found: {wheels}")
        wheel = wheels[0]
        _assert_allowlist(wheel)
        environment = scratch / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        # Deliberately resolve declared dependencies here.  Source-tree tests
        # can hide an undeclared dependency via the developer environment;
        # this isolated install must satisfy the wheel metadata on its own.
        _run([str(python), "-m", "pip", "install", str(wheel)])
        isolated_env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        _run(
            [
                str(python),
                "-c",
                (
                    "import importlib.metadata as m; import agent_workflow_kernel as a; "
                    "import agent_workflow_kernel_x_digest as x; "
                    "assert a.__version__ == '0.4.0'; "
                    "assert m.version('agent-workflow-kernel') == '0.4.0'; "
                    "assert x.XDigestDraftRuntimeAdapter.adapter_id == 'runtime.agent'"
                ),
            ],
            env=isolated_env,
        )
        _run([str(python), "-m", "pip", "check"], env=isolated_env)
        print(f"wheel verification passed: {wheel}")
    return 0


def _assert_allowlist(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        roots = {
            name.split("/", 1)[0]
            for name in archive.namelist()
            if name.endswith(".py") and "/" in name
        }
    missing = EXPECTED_PACKAGE_ROOTS - roots
    unexpected = {root for root in roots if root.startswith("agent_workflow_kernel")}
    unexpected -= EXPECTED_PACKAGE_ROOTS
    if missing or unexpected:
        raise RuntimeError(
            f"wheel package allowlist mismatch; missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
