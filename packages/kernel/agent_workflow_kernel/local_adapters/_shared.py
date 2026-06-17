"""Shared constants and render/parse helpers for the local adapters.

Extracted from the former local_adapters.py module so each adapter group
lives in its own file. Internal to the package.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..adapters import (
    ADAPTER_STATUS_BLOCKED,
    ADAPTER_STATUS_CANCELLED,
    ADAPTER_STATUS_SUCCEEDED,
    CapabilitySet,
    HostDescriptor,
    LaneDescriptor,
    RuntimeRef,
    SurfaceCapabilityContract,
    SurfaceRef,
    ensure_invocation_family,
    make_adapter_receipt,
    result_from_receipt,
    unsupported_operation_result,
)
from ..contracts import (
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    ArtifactRef,
    Receipt,
    StageRun,
    to_plain_data,
)


DETERMINISTIC_CREATED_AT = "2000-01-01T00:00:00Z"
LOCAL_HUMAN_REVIEW_CARD_SCHEMA = "local_human_review_card.v1"
LOCAL_HUMAN_REVIEW_DECISION_SCHEMA = "local_human_review_decision.v1"
DRY_RUN_SURFACE_PACKET_SCHEMA = "dry_run_surface_packet.v1"
DRY_RUN_SURFACE_DECISION_SCHEMA = "dry_run_surface_decision.v1"
OBSIDIAN_SANDBOX_NOTE_SCHEMA = "obsidian_sandbox_note.v1"
TELEGRAM_SANDBOX_MESSAGE_SCHEMA = "telegram_sandbox_message.v1"
SANDBOX_SURFACE_DECISION_SCHEMA = "sandbox_surface_decision.v1"
LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA = "live_obsidian_markdown_note.v1"
LIVE_OPERATOR_SURFACE_DECISION_SCHEMA = "live_operator_surface_decision.v1"

_CHECKBOX_RE = re.compile(
    r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+`?(?P<label>[^`\n]+?)`?\s*$"
)
_FINGERPRINT_RE = re.compile(r"Action fingerprint:\s*`(?P<value>[^`]+)`")
_FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---", re.DOTALL)

def _plain_mapping(value: Mapping[str, Any] | RuntimeRef | SurfaceRef) -> dict[str, Any]:
    if isinstance(value, (RuntimeRef, SurfaceRef)):
        return to_plain_data(asdict(value))
    return dict(value)


def _synthetic_invocation(
    *,
    adapter_family: AdapterFamily,
    adapter_id: str,
    operation: str,
    instance_id: str = "local-instance",
    stage_run_id: str = "local-stage-run",
    idempotency_key: str | None = None,
) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"{adapter_id}:{operation}:{idempotency_key or instance_id}",
        workflow_id="local-workflow",
        instance_id=instance_id,
        stage_run_id=stage_run_id,
        adapter_family=adapter_family,
        adapter_id=adapter_id,
        operation=operation,
        idempotency_key=idempotency_key,
    )



def _artifact_review_from_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    title = str(packet.get("artifact_title") or packet.get("artifact_label") or "").strip()
    intro = str(packet.get("artifact_intro") or "").strip()
    link = str(packet.get("artifact_link") or packet.get("artifact_path") or "").strip()
    markdown = str(packet.get("artifact_markdown") or packet.get("artifact_body") or "").strip()
    display_mode = str(packet.get("artifact_display_mode") or "").strip().lower()
    if not any((title, intro, link, markdown)):
        return {}
    if not title:
        title = "Artifact To Review"
    if display_mode not in {"", "inline", "details", "link_only"}:
        display_mode = ""
    return {
        "title": title,
        "intro": intro,
        "link": link,
        "markdown": markdown,
        "embedded": bool(markdown),
        "display_mode": display_mode,
    }


def _artifact_review_metadata(artifact_review: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": str(artifact_review.get("title") or ""),
        "link": str(artifact_review.get("link") or ""),
        "embedded": bool(artifact_review.get("embedded")),
        "display_mode": str(artifact_review.get("display_mode") or ""),
    }


def _operator_brief_from_packet(packet: Mapping[str, Any]) -> dict[str, str]:
    fields = {
        "executive_summary": str(packet.get("executive_summary") or packet.get("summary") or "").strip(),
        "why_this_matters": str(packet.get("why_this_matters") or packet.get("why") or "").strip(),
        "recommended_action": str(packet.get("recommended_action") or packet.get("recommendation") or "").strip(),
        "risk_summary": str(packet.get("risk_summary") or packet.get("risk") or "").strip(),
    }
    return {key: value for key, value in fields.items() if value}


def _operator_brief_metadata(operator_brief: Mapping[str, str]) -> dict[str, bool]:
    return {key: bool(operator_brief.get(key)) for key in sorted(operator_brief)}


def _render_operator_brief_section(
    *,
    operator_brief: Mapping[str, str],
    human_ask: str,
    artifact_review: Mapping[str, Any],
) -> list[str]:
    summary = str(operator_brief.get("executive_summary") or "").strip()
    if not summary:
        summary = str(
            artifact_review.get("intro")
            or human_ask
            or "Review the linked artifact and choose one decision."
        ).strip()
    lines = ["## Operator Brief", "", summary, ""]
    if operator_brief.get("why_this_matters"):
        lines.extend(["## Why This Matters", "", str(operator_brief["why_this_matters"]), ""])
    if operator_brief.get("recommended_action"):
        lines.extend(["## Recommended Action", "", str(operator_brief["recommended_action"]), ""])
    if operator_brief.get("risk_summary"):
        lines.extend(["## Risk / Open Question", "", str(operator_brief["risk_summary"]), ""])
    return lines


def _render_artifact_review_section(
    artifact_review: Mapping[str, Any],
    *,
    default_display_mode: str = "inline",
) -> list[str]:
    if not artifact_review:
        return []
    title = str(artifact_review.get("title") or "Artifact To Review")
    intro = str(artifact_review.get("intro") or "").strip()
    link = str(artifact_review.get("link") or "").strip()
    markdown = str(artifact_review.get("markdown") or "").strip()
    display_mode = str(artifact_review.get("display_mode") or default_display_mode).strip().lower()
    if display_mode not in {"inline", "details", "link_only"}:
        display_mode = default_display_mode
    lines = ["## Artifact To Review", "", f"### {title}", ""]
    if intro:
        lines.extend([intro, ""])
    if link:
        lines.extend([f"- Review source: [{title}](<{link}>)", ""])
    if markdown and display_mode == "inline":
        lines.extend([markdown, ""])
    elif markdown and display_mode == "details":
        lines.extend(
            [
                "<details>",
                f"<summary>Full {title}</summary>",
                "",
                markdown,
                "",
                "</details>",
                "",
            ]
        )
    return lines


def _prompt_provenance_from_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    value = packet.get("prompt_provenance")
    if not isinstance(value, Mapping):
        return {}
    refs = value.get("refs")
    return {
        "prompt_bundle_digest": str(value.get("prompt_bundle_digest") or ""),
        "context_packet_ref": str(value.get("context_packet_ref") or value.get("context_packet_id") or ""),
        "rendered_input_digest": str(value.get("rendered_input_digest") or ""),
        "refs": [dict(ref) for ref in refs if isinstance(ref, Mapping)] if isinstance(refs, list) else [],
    }


def _prompt_provenance_metadata(prompt_provenance: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "prompt_bundle_digest": str(prompt_provenance.get("prompt_bundle_digest") or ""),
        "context_packet_ref": str(prompt_provenance.get("context_packet_ref") or ""),
        "rendered_input_digest": str(prompt_provenance.get("rendered_input_digest") or ""),
        "ref_count": len(prompt_provenance.get("refs") or ()),
    }


def _choice_options_from_packet(packet: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_options = packet.get("choice_options")
    if not raw_options and isinstance(packet.get("choice_manifest"), Mapping):
        raw_options = packet["choice_manifest"].get("options")
    if not raw_options:
        return ()
    if isinstance(raw_options, Mapping):
        raw_options = [
            {"id": key, **(value if isinstance(value, Mapping) else {"label": value})}
            for key, value in raw_options.items()
        ]
    if isinstance(raw_options, str):
        raw_options = (raw_options,)
    options: list[dict[str, Any]] = []
    for index, raw_option in enumerate(raw_options, start=1):
        if isinstance(raw_option, Mapping):
            option = {str(key): raw_option[key] for key in sorted(raw_option, key=str)}
            option_id = (
                option.get("id")
                or option.get("decision")
                or option.get("value")
                or option.get("label")
                or f"option_{index}"
            )
            option["id"] = str(option_id)
            option.setdefault("label", str(option_id))
            options.append(option)
        else:
            option_id = str(raw_option)
            options.append({"id": option_id, "label": option_id})
    return tuple(options)


def _selected_choice_option(
    decision: str | None,
    choice_options: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    if decision is None:
        return {}
    for option in choice_options:
        if str(option.get("id") or "") == decision:
            return dict(option)
    return {}


def _render_choice_options_section(
    choice_options: tuple[dict[str, Any], ...],
    choice_manifest_hash: str,
) -> list[str]:
    if not choice_options:
        return []
    lines = ["### Choice Options"]
    if choice_manifest_hash:
        lines.append(f"- Manifest hash: `{choice_manifest_hash}`")
    for option in choice_options:
        option_id = str(option.get("id") or "")
        label = str(option.get("label") or option_id)
        summary = str(option.get("summary") or option.get("description") or "").strip()
        line = f"- `{option_id}`: {label}"
        if summary:
            line = f"{line} - {summary}"
        lines.append(line)
    lines.append("")
    return lines


def _render_review_card(
    *,
    invocation: AdapterInvocation,
    stage_id: str,
    title: str,
    human_ask: str,
    human_ref: str,
    canonical_surface: str,
    gate_id: str,
    allowed_decisions: tuple[str, ...],
    requested_action: str,
    exact_action: str,
    action_fingerprint: str,
    evidence_refs: tuple[str, ...],
    artifact_review: Mapping[str, Any],
    prompt_provenance: Mapping[str, Any],
    operator_brief: Mapping[str, str],
    choice_options: tuple[dict[str, Any], ...],
    choice_manifest_hash: str,
    test_only: bool,
    non_live: bool,
    created_at: str,
) -> str:
    metadata = {
        "schema": LOCAL_HUMAN_REVIEW_CARD_SCHEMA,
        "canonical_surface": canonical_surface,
        "workflow_id": invocation.workflow_id,
        "instance_id": invocation.instance_id,
        "stage_id": stage_id,
        "stage_run_id": invocation.stage_run_id,
        "invocation_id": invocation.invocation_id,
        "gate_id": gate_id,
        "allowed_decisions": list(allowed_decisions),
        "requested_action": requested_action,
        "exact_action": exact_action,
        "evidence_refs": list(evidence_refs),
        "choice_options": list(choice_options),
        "choice_manifest_hash": choice_manifest_hash or None,
        "test_only": test_only,
        "non_live": non_live,
        "created_at": created_at,
    }
    if artifact_review:
        metadata["artifact_review"] = _artifact_review_metadata(artifact_review)
    if prompt_provenance:
        metadata["prompt_provenance"] = _prompt_provenance_metadata(prompt_provenance)
    if operator_brief:
        metadata["operator_brief"] = _operator_brief_metadata(operator_brief)
    frontmatter = "\n".join(
        f"{key}: {json.dumps(value, sort_keys=True)}" for key, value in metadata.items()
    )
    evidence_lines = "\n".join(f"- `{ref}`" for ref in evidence_refs) or "- `none`"
    ask = human_ask or "Choose exactly one allowed decision below."
    brief_lines = _render_operator_brief_section(
        operator_brief=operator_brief,
        human_ask=ask,
        artifact_review=artifact_review,
    )
    artifact_lines = _render_artifact_review_section(artifact_review, default_display_mode="details")
    choice_lines = _render_choice_options_section(choice_options, choice_manifest_hash)
    decision_lines = "\n".join(f"- [ ] `{decision}`" for decision in allowed_decisions)
    label = "TEST ONLY - NON-LIVE LOCAL REVIEW PACKET" if test_only else "LOCAL REVIEW PACKET - NON-LIVE"
    return "\n".join(
        [
            "---",
            frontmatter,
            "---",
            "",
            f"# {title}",
            "",
            f"**{label}**",
            "",
            *brief_lines,
            "### Decision Scope",
            f"- Requested action: `{requested_action}`",
            f"- Exact action: `{exact_action}`",
            "- This is a local/test review packet; it grants no live mutation permission.",
            "- Comments are context only and do not authorize any live, external, destructive, auth, money, deploy, publish, Telegram, OpenClaw, oldmac, or trading action.",
            "",
            *artifact_lines,
            *choice_lines,
            "<details>",
            "<summary>Evidence and provenance for audit/ingest</summary>",
            "",
            "### Review Context",
            f"- Workflow ID: `{invocation.workflow_id}`",
            f"- Instance ID: `{invocation.instance_id}`",
            f"- Stage ID: `{stage_id}`",
            f"- Stage Run ID: `{invocation.stage_run_id}`",
            f"- Gate ID: `{gate_id or 'not-provided'}`",
            f"- Invocation ID: `{invocation.invocation_id}`",
            f"- Canonical surface: `{canonical_surface}`",
            f"- Human ref: `{human_ref}`",
            f"- Action fingerprint: `{action_fingerprint}`",
            f"- Prompt bundle: `{prompt_provenance.get('prompt_bundle_digest') or 'not-provided'}`",
            "",
            "### Evidence Refs",
            evidence_lines,
            "",
            "</details>",
            "",
            "## Decision",
            "Check exactly one allowed decision. Comments are context only and do not authorize any live, external, destructive, auth, money, deploy, publish, Telegram, OpenClaw, oldmac, or trading action.",
            "",
            decision_lines,
            "",
        ]
    )


def _render_live_review_card(
    *,
    invocation: AdapterInvocation,
    stage_id: str,
    title: str,
    human_ask: str,
    human_ref: str,
    canonical_surface: str,
    gate_id: str,
    allowed_decisions: tuple[str, ...],
    requested_action: str,
    exact_action: str,
    action_fingerprint: str,
    evidence_refs: tuple[str, ...],
    artifact_review: Mapping[str, Any],
    prompt_provenance: Mapping[str, Any],
    operator_brief: Mapping[str, str],
    choice_options: tuple[dict[str, Any], ...],
    choice_manifest_hash: str,
    created_at: str,
) -> str:
    metadata = {
        "schema": LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA,
        "canonical_surface": canonical_surface,
        "workflow_id": invocation.workflow_id,
        "instance_id": invocation.instance_id,
        "stage_id": stage_id,
        "stage_run_id": invocation.stage_run_id,
        "invocation_id": invocation.invocation_id,
        "gate_id": gate_id,
        "allowed_decisions": list(allowed_decisions),
        "requested_action": requested_action,
        "exact_action": exact_action,
        "evidence_refs": list(evidence_refs),
        "choice_options": list(choice_options),
        "choice_manifest_hash": choice_manifest_hash or None,
        "live_operator_surface_allowed": True,
        "public_publish_blocked": True,
        "created_at": created_at,
    }
    if artifact_review:
        metadata["artifact_review"] = _artifact_review_metadata(artifact_review)
    if prompt_provenance:
        metadata["prompt_provenance"] = _prompt_provenance_metadata(prompt_provenance)
    if operator_brief:
        metadata["operator_brief"] = _operator_brief_metadata(operator_brief)
    frontmatter = "\n".join(
        f"{key}: {json.dumps(value, sort_keys=True)}" for key, value in metadata.items()
    )
    evidence_lines = "\n".join(f"- `{ref}`" for ref in evidence_refs) or "- `none`"
    ask = human_ask or "Choose exactly one allowed decision below."
    brief_lines = _render_operator_brief_section(
        operator_brief=operator_brief,
        human_ask=ask,
        artifact_review=artifact_review,
    )
    artifact_lines = _render_artifact_review_section(artifact_review, default_display_mode="details")
    choice_lines = _render_choice_options_section(choice_options, choice_manifest_hash)
    decision_lines = "\n".join(f"- [ ] `{decision}`" for decision in allowed_decisions)
    return "\n".join(
        [
            "---",
            frontmatter,
            "---",
            "",
            f"# {title}",
            "",
            "**LIVE OPERATOR-SURFACE WRITE AUTHORIZED - PUBLIC PUBLISH BLOCKED**",
            "",
            *brief_lines,
            "### Decision Scope",
            f"- Requested action: `{requested_action}`",
            f"- Exact action: `{exact_action}`",
            "- Public publish remains blocked unless a separate explicit approval says otherwise.",
            "- Comments are context only and do not authorize public publish, deploy, trading, money movement, auth, secrets, destructive changes, or unscoped live mutation.",
            "",
            *artifact_lines,
            *choice_lines,
            "<details>",
            "<summary>Evidence and provenance for audit/ingest</summary>",
            "",
            "### Review Context",
            f"- Workflow ID: `{invocation.workflow_id}`",
            f"- Instance ID: `{invocation.instance_id}`",
            f"- Stage ID: `{stage_id}`",
            f"- Stage Run ID: `{invocation.stage_run_id}`",
            f"- Gate ID: `{gate_id or 'not-provided'}`",
            f"- Invocation ID: `{invocation.invocation_id}`",
            f"- Canonical surface: `{canonical_surface}`",
            f"- Human ref: `{human_ref}`",
            f"- Action fingerprint: `{action_fingerprint}`",
            f"- Prompt bundle: `{prompt_provenance.get('prompt_bundle_digest') or 'not-provided'}`",
            f"- Public publish blocked: `true`",
            "",
            "### Evidence Refs",
            evidence_lines,
            "",
            "</details>",
            "",
            "## Decision",
            "Check exactly one allowed decision. Comments are context only and do not authorize public publish, deploy, trading, money movement, auth, secrets, destructive changes, or unscoped live mutation.",
            "",
            decision_lines,
            "",
        ]
    )


def _render_telegram_operator_message(packet: Mapping[str, Any]) -> str:
    title = str(packet.get("title") or "OpenClaw operator review")
    ask = str(packet.get("human_ask") or packet.get("ask") or packet.get("body") or "")
    exact_action = str(packet.get("exact_action") or packet.get("requested_action") or "")
    fingerprint = str(packet.get("action_fingerprint") or "")
    decisions = ", ".join(_string_tuple(packet.get("allowed_decisions", ()))) or "none"
    parts = [title]
    if ask:
        parts.append(ask)
    if exact_action:
        parts.append(f"Exact action: {exact_action}")
    if fingerprint:
        parts.append(f"Action fingerprint: {fingerprint}")
    parts.append(f"Allowed decisions: {decisions}")
    parts.append("Live operator-surface send authorized; public publish remains blocked.")
    return "\n".join(parts)


def _non_live_safety_error(
    payload: Mapping[str, Any],
    *,
    require_test_only: bool,
) -> dict[str, Any] | None:
    if bool(payload.get("live_mutation_requested", False)) or bool(
        payload.get("mutation_permission_granted", False)
    ):
        return _live_mutation_error("Surface adapter refused a request that asked for live mutation.")
    if not bool(payload.get("non_live", True)):
        return _live_mutation_error("Surface adapter refused a packet that was not marked non_live.")
    if require_test_only and not bool(payload.get("test_only", False)):
        return {
            "error_class": "test_only_required",
            "message": "Dry-run surface adapter requires test_only true.",
        }
    return None


_UNSAFE_LIVE_SURFACE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("public_publish", r"\b(public\s+publish|publish\s+publicly|substack|medium|social\s+post|public\s+website|public\s+repo\s+release)\b"),
    ("trading_or_money", r"\b(live\s+trade|trade|trading|broker|order\s+placement|order\s+cancellation|position\s+size|money\s+movement|spend|purchase|transfer|billing|invoice)\b"),
    ("auth_or_secret", r"\b(auth|oauth|credential|credentials|secret|token|api\s*key|login|session)\b"),
    ("deploy_or_production", r"\b(deploy|deployment|production\s+mutation|migration|service\s+restart|runtime\s+mutation|oldmac\s+mutation)\b"),
    ("destructive", r"\b(delete|deletion|destroy|destructive|archive\s+without\s+recovery|irreversible|overwrite|prune|cleanup\s+job)\b"),
    ("unscoped_live_mutation", r"\b(unscoped\s+live|any\s+live\s+mutation|arbitrary\s+mutation|mutate\s+anything)\b"),
)


def _live_operator_surface_safety_error(
    payload: Mapping[str, Any],
    *,
    effect_name: str,
) -> dict[str, Any] | None:
    if not bool(payload.get("live_operator_surface_allowed", False)):
        return _live_mutation_error(f"{effect_name} requires live_operator_surface_allowed=True.")
    if bool(payload.get("public_publish_allowed", False)) or payload.get("public_publish_blocked", True) is False:
        return _live_mutation_error(f"{effect_name} refused a packet that did not keep public publish blocked.")
    if bool(payload.get("mutation_permission_granted", False)):
        return _live_mutation_error(f"{effect_name} refused ambiguous mutation permission.")
    unsafe = _unsafe_live_surface_hits(payload)
    if unsafe:
        return {
            "error_class": "unsafe_live_surface_scope_refused",
            "message": f"{effect_name} refused unsafe or out-of-scope live action language.",
            "retryable": False,
            "unsafe_hits": unsafe,
        }
    return None


def _unsafe_live_surface_hits(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    scan_keys = {
        "title",
        "human_ask",
        "ask",
        "body",
        "message",
        "requested_action",
        "exact_action",
        "exact_action_approved",
        "allowed_scope",
        "requested_effects",
        "risk_classes",
        "forbidden_actions",
        "side_effects",
    }
    hits: list[dict[str, str]] = []
    for key in scan_keys:
        if key not in payload:
            continue
        text = _stringify_for_scan(payload[key]).lower()
        if not text:
            continue
        for category, pattern in _UNSAFE_LIVE_SURFACE_PATTERNS:
            if re.search(pattern, text):
                hits.append({"field": key, "category": category})
    return hits


def _stringify_for_scan(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return " ".join(f"{key} {_stringify_for_scan(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringify_for_scan(item) for item in value)
    return str(value)


def _live_mutation_error(message: str) -> dict[str, Any]:
    return {
        "error_class": "live_mutation_refused",
        "message": message,
        "retryable": False,
    }


def _sandbox_configuration_error(
    *,
    mutation_mode: str,
    allow_live_mutation: bool,
    live_effect_name: str,
) -> dict[str, Any] | None:
    if allow_live_mutation:
        return _live_mutation_error(
            f"{live_effect_name} is not allowed by the sandbox surface adapter."
        )
    if mutation_mode not in {"sandbox", "test", "local"}:
        return {
            "error_class": "unknown_mutation_mode",
            "message": (
                "Sandbox surface adapter refused an unknown or live mutation mode: "
                f"{mutation_mode!r}."
            ),
            "retryable": False,
        }
    return None


def _redact_sensitive_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    sensitive_markers = ("token", "secret", "password", "api_key", "apikey", "credential")
    for key, value in payload.items():
        key_text = str(key).lower()
        if any(marker in key_text for marker in sensitive_markers):
            redacted[str(key)] = "<redacted>"
        elif isinstance(value, Mapping):
            redacted[str(key)] = _redact_sensitive_mapping(value)
        else:
            redacted[str(key)] = value
    return redacted


def _redact_sensitive_text(text: str) -> str:
    if not text:
        return ""
    redacted = text
    for pattern in (
        r"(?i)(token|secret|password|api[_-]?key|credential)=\S+",
        r"(?i)(token|secret|password|api[_-]?key|credential):\s*\S+",
    ):
        redacted = re.sub(pattern, r"\1=<redacted>", redacted)
    return redacted


def _redacted_telegram_command(command: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            redacted.append("<redacted-message>")
            skip_next = False
            continue
        redacted.append(part)
        if part == "--message":
            skip_next = True
    return redacted


def _loads_json_mapping(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {"value": payload}


def _decision_outputs(
    *,
    schema: str,
    canonical_surface: str,
    surface_query: Mapping[str, Any],
    decision: str | None,
    source_ref: str,
    transcript_or_message_ref: str,
    checked_decisions: tuple[str, ...] = (),
    allowed_decisions: tuple[str, ...] = (),
    note_action_fingerprint: str | None = None,
    decision_payload: Mapping[str, Any] | None = None,
    test_only: bool = True,
    non_live: bool = True,
    live_operator_surface_allowed: bool = False,
    public_publish_blocked: bool = True,
    live_mutation_performed: bool = False,
    network_call_performed: bool = False,
) -> dict[str, Any]:
    payload = dict(decision_payload or {})
    exact_action = str(
        payload.get("exact_action")
        or surface_query.get("exact_action")
        or surface_query.get("exact_action_approved")
        or surface_query.get("requested_action")
        or ""
    )
    action_fingerprint = str(
        payload.get("action_fingerprint")
        or surface_query.get("expected_action_fingerprint")
        or surface_query.get("action_fingerprint")
        or ""
    )
    gate_id = str(payload.get("gate_id") or surface_query.get("gate_id") or "")
    human_ref = str(payload.get("human_ref") or surface_query.get("human_ref") or "Suman(test)")
    evidence_refs = _string_tuple(
        payload.get("evidence_refs", surface_query.get("evidence_refs", ()))
    )
    resolved_allowed = allowed_decisions or _string_tuple(
        payload.get("allowed_decisions", surface_query.get("allowed_decisions", ()))
    )
    choice_options = _choice_options_from_packet(surface_query) or _choice_options_from_packet(payload)
    selected_option = _selected_choice_option(decision, choice_options)
    choice_manifest_hash = str(
        payload.get("choice_manifest_hash") or surface_query.get("choice_manifest_hash") or ""
    )
    return {
        "schema": schema,
        "canonical_surface": canonical_surface,
        "gate_id": gate_id,
        "human_ref": human_ref,
        "decision": decision,
        "requested_action": str(surface_query.get("requested_action") or exact_action),
        "exact_action_approved": exact_action,
        "action_fingerprint": action_fingerprint,
        "note_action_fingerprint": note_action_fingerprint,
        "evidence_refs": list(evidence_refs),
        "source_ref": source_ref,
        "source_note_path": source_ref,
        "transcript_or_message_ref": transcript_or_message_ref,
        "checked_decisions": list(checked_decisions),
        "allowed_decisions": list(resolved_allowed),
        "selected_option": selected_option,
        "choice_options": list(choice_options),
        "choice_manifest_hash": choice_manifest_hash or None,
        "decision_payload": _redact_sensitive_mapping(payload),
        "test_only": test_only,
        "non_live": non_live,
        "live_operator_surface_allowed": live_operator_surface_allowed,
        "public_publish_blocked": public_publish_blocked,
        "live_mutation_performed": live_mutation_performed,
        "network_call_performed": network_call_performed,
    }


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return slug or "item"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_action_fingerprint(text: str) -> str:
    match = _FINGERPRINT_RE.search(text)
    return match.group("value").strip() if match else ""


def _extract_allowed_decisions(text: str) -> tuple[str, ...]:
    metadata = _extract_frontmatter(text)
    if "allowed_decisions" in metadata:
        return _string_tuple(metadata["allowed_decisions"])

    decisions: list[str] = []
    in_decision_section = False
    for line in text.splitlines():
        if line.strip() == "## Decision":
            in_decision_section = True
            continue
        if in_decision_section and line.startswith("## "):
            break
        if not in_decision_section:
            continue
        match = _CHECKBOX_RE.match(line)
        if match:
            decisions.append(match.group("label").strip())
    return tuple(decisions)


def _extract_checked_decisions(text: str) -> tuple[str, ...]:
    checked: list[str] = []
    in_decision_section = False
    for line in text.splitlines():
        if line.strip() == "## Decision":
            in_decision_section = True
            continue
        if in_decision_section and line.startswith("## "):
            break
        if not in_decision_section:
            continue
        match = _CHECKBOX_RE.match(line)
        if match and match.group("mark").lower() == "x":
            checked.append(match.group("label").strip())
    return tuple(checked)


def _extract_frontmatter(text: str) -> dict[str, Any]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    metadata: dict[str, Any] = {}
    for line in match.group("body").splitlines():
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        try:
            metadata[key.strip()] = json.loads(raw_value.strip())
        except json.JSONDecodeError:
            metadata[key.strip()] = raw_value.strip()
    return metadata



__all__ = [
    "DETERMINISTIC_CREATED_AT",
    "LOCAL_HUMAN_REVIEW_CARD_SCHEMA",
    "LOCAL_HUMAN_REVIEW_DECISION_SCHEMA",
    "DRY_RUN_SURFACE_PACKET_SCHEMA",
    "DRY_RUN_SURFACE_DECISION_SCHEMA",
    "OBSIDIAN_SANDBOX_NOTE_SCHEMA",
    "TELEGRAM_SANDBOX_MESSAGE_SCHEMA",
    "SANDBOX_SURFACE_DECISION_SCHEMA",
    "LIVE_OBSIDIAN_MARKDOWN_NOTE_SCHEMA",
    "LIVE_OPERATOR_SURFACE_DECISION_SCHEMA",
    "_CHECKBOX_RE",
    "_FINGERPRINT_RE",
    "_FRONTMATTER_RE",
    "_plain_mapping",
    "_synthetic_invocation",
    "_artifact_review_from_packet",
    "_artifact_review_metadata",
    "_operator_brief_from_packet",
    "_operator_brief_metadata",
    "_render_operator_brief_section",
    "_render_artifact_review_section",
    "_prompt_provenance_from_packet",
    "_prompt_provenance_metadata",
    "_choice_options_from_packet",
    "_selected_choice_option",
    "_render_choice_options_section",
    "_render_review_card",
    "_render_live_review_card",
    "_render_telegram_operator_message",
    "_non_live_safety_error",
    "_live_operator_surface_safety_error",
    "_unsafe_live_surface_hits",
    "_stringify_for_scan",
    "_live_mutation_error",
    "_sandbox_configuration_error",
    "_redact_sensitive_mapping",
    "_redact_sensitive_text",
    "_redacted_telegram_command",
    "_loads_json_mapping",
    "_decision_outputs",
    "_string_tuple",
    "_slug",
    "_sha256_text",
    "_extract_action_fingerprint",
    "_extract_allowed_decisions",
    "_extract_checked_decisions",
    "_extract_frontmatter",
]
