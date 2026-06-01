#!/usr/bin/env python3
"""Render versioned OpenClaw prompt profiles for agent injection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
KERNEL_PATH = ROOT / "packages" / "kernel"


def _ensure_source_checkout_imports() -> None:
    package_path = str(KERNEL_PATH)
    if package_path not in sys.path:
        sys.path.insert(0, package_path)


try:
    from agent_workflow_kernel import PromptRef, PromptRegistry, render_context_packet  # noqa: E402
except ModuleNotFoundError:
    _ensure_source_checkout_imports()
    from agent_workflow_kernel import PromptRef, PromptRegistry, render_context_packet  # noqa: E402


PROFILE_REFS = {
    "jarvis_weekly_improvement_cargo": (
        PromptRef(id="identity.jarvis_weekly_shadow_worker", kind="identity", version="1.0.0"),
        PromptRef(id="policy.openclaw.read_only_shadow", kind="policy", version="1.0.0", render_mode="yaml"),
        PromptRef(id="lane.jarvis_weekly_update_shadow", kind="lane", version="1.0.0"),
        PromptRef(id="stage.jarvis_weekly.improvement_cargo", kind="stage", version="1.0.0"),
    ),
    "openclaw_cutover_review_weekly": (
        PromptRef(id="policy.openclaw.review_only_human_gate", kind="policy", version="1.0.0", render_mode="yaml"),
        PromptRef(id="lane.jarvis_weekly_update_shadow", kind="lane", version="1.0.0"),
        PromptRef(id="stage.openclaw.cutover_review_artifact", kind="stage", version="1.0.0"),
    ),
    "openclaw_cutover_review_ivy": (
        PromptRef(id="policy.openclaw.review_only_human_gate", kind="policy", version="1.0.0", render_mode="yaml"),
        PromptRef(id="lane.ivy_jonah_editorial", kind="lane", version="1.0.0"),
        PromptRef(id="stage.openclaw.cutover_review_artifact", kind="stage", version="1.0.0"),
    ),
}


def render_profile(
    profile: str,
    *,
    inputs: Mapping[str, Any] | None = None,
    registry_root: str | Path = ROOT / "prompts",
) -> dict[str, Any]:
    if profile not in PROFILE_REFS:
        raise ValueError(f"unknown prompt profile: {profile}")
    registry = PromptRegistry.load(registry_root)
    bundle = registry.resolve(PROFILE_REFS[profile])
    rendered = render_context_packet(
        prompt_bundle=bundle,
        workflow_id="openclaw_prompt_profile",
        workflow_version="0.1.0",
        instance_id=str((inputs or {}).get("instance_id") or profile),
        stage_id=profile,
        stage_run_id=profile,
        stage_type="agent_work" if "improvement_cargo" in profile else "human_gate",
        inputs=inputs or {},
        constraints={
            "public_publish_blocked": True,
            "telegram_send_blocked": True,
            "trading_or_money_action_blocked": True,
            "auth_or_secret_access_blocked": True,
            "destructive_action_blocked": True,
        },
        permissions={
            "shell": False,
            "network": False,
            "public_publish": False,
            "telegram_send": False,
            "runtime_mutation": False,
        },
    )
    return {
        "schema": "openclaw.prompt_profile_render.v1",
        "profile": profile,
        "prompt_bundle_digest": rendered.prompt_bundle.prompt_bundle_digest,
        "context_packet_ref": rendered.packet.context_id,
        "packet_digest": rendered.packet_digest,
        "rendered_input_digest": rendered.rendered_input_digest,
        "refs": rendered.prompt_bundle.provenance_refs(),
        "rendered_input": rendered.rendered_input,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=sorted(PROFILE_REFS))
    parser.add_argument("--inputs-json", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    inputs: dict[str, Any] = {}
    if args.inputs_json is not None:
        loaded = json.loads(args.inputs_json.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise SystemExit("--inputs-json must contain a JSON object")
        inputs = loaded
    payload = render_profile(args.profile, inputs=inputs)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

