"""Deterministic test-only human-gate reviewers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from .storage import iso_timestamp


AUTOMATED_SUMAN_REVIEWER_ID = "suman_reviewer.automated_test_only"
AUTOMATED_SUMAN_REVIEWER_HUMAN_REF = "Suman(test automated reviewer)"
AUTOMATED_SUMAN_REVIEWER_SURFACE = "local_test_automated_suman_reviewer"
AUTOMATED_SUMAN_REVIEWER_SCHEMA = "automated_suman_reviewer_decision.v1"

_FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---", re.DOTALL)
_CHECKBOX_RE = re.compile(r"^(?P<prefix>\s*[-*]\s+)\[(?P<mark>[ xX])\](?P<suffix>\s+`?(?P<label>[^`\n]+?)`?\s*)$")
_APPROVAL_INTENTS = ("approved", "approve", "approval_granted", "read_clear", "clear", "approve_packet", "selected")
_REVISION_INTENTS = ("revise", "revision_requested", "revise_plan", "needs_revision", "follow_up_requested")
_PARK_INTENTS = ("park", "parked", "defer", "blocked", "reject", "rejected", "denied")
_UNSAFE_CONTEXT_KEYS = ("hard_gates", "forbidden_actions", "risk_classes", "requested_effects")
_UNSAFE_EFFECTS = frozenset(
    {
        "public_publish",
        "publish",
        "deploy",
        "production_effect",
        "live_trade",
        "trade",
        "auth",
        "auth_effect",
        "credential",
        "credentials",
        "secret",
        "money",
        "financial_effect",
        "external_send",
        "send_telegram",
        "telegram_send",
        "obsidian_write",
        "northstar_write",
        "oldmac_mutation",
        "destructive_change",
        "destructive_effect",
        "delete",
        "archive",
    }
)


@dataclass(frozen=True, slots=True)
class AutomatedSumanReviewResult:
    """Structured receipt for one automated test-only review decision."""

    status: Literal["succeeded", "blocked"]
    operation: Literal["automated_review"]
    decision: str | None
    human_ref: str
    reviewer_id: str
    note_path: str | None
    receipt_path: str | None
    summary: str
    outputs: Mapping[str, Any] = field(default_factory=dict)


class AutomatedSumanReviewer:
    """Deterministic local reviewer for fixture/shadow human gates.

    This helper is intentionally narrower than a real approval source. It can
    mark local Markdown review cards for tests and shadow packets, but it
    refuses to approve live effects, external sends, public publish, auth,
    deploy, trading, money, destructive actions, or packets without explicit
    test/non-live scope.
    """

    reviewer_id = AUTOMATED_SUMAN_REVIEWER_ID
    human_ref = AUTOMATED_SUMAN_REVIEWER_HUMAN_REF
    canonical_surface = AUTOMATED_SUMAN_REVIEWER_SURFACE

    def __init__(
        self,
        *,
        created_at: str | None = None,
        receipt_dir: str | Path | None = None,
        override_decisions: Mapping[str, str] | None = None,
    ) -> None:
        self.created_at = created_at
        self.receipt_dir = Path(receipt_dir).resolve() if receipt_dir is not None else None
        self.override_decisions = dict(override_decisions or {})

    def review_human_gate_surface(
        self,
        *,
        surface_ref: Mapping[str, Any],
        readback_result: Any | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> AutomatedSumanReviewResult:
        """Review a local Markdown human-gate surface and check one decision."""

        review_context = dict(context or {})
        note_path = _note_path_from_surface_ref(surface_ref)
        if note_path is None:
            return self._blocked(
                note_path=None,
                decision=None,
                summary="Automated Suman Reviewer blocked because the surface has no local note path.",
                reason="missing_note_path",
                context=review_context,
            )
        if not note_path.exists():
            return self._blocked(
                note_path=note_path,
                decision=None,
                summary="Automated Suman Reviewer blocked because the local review note is missing.",
                reason="missing_review_note",
                context=review_context,
            )

        text = note_path.read_text(encoding="utf-8")
        metadata = _extract_frontmatter(text)
        allowed = _allowed_decisions(text, metadata)
        desired_intent, reason, checks = self._desired_intent(
            note_path=note_path,
            metadata=metadata,
            context=review_context,
        )
        decision = _select_allowed_decision(allowed, desired_intent)
        if decision is None:
            return self._blocked(
                note_path=note_path,
                decision=None,
                summary=(
                    "Automated Suman Reviewer blocked because no allowed decision "
                    f"matches the safe intent {desired_intent!r}."
                ),
                reason="no_allowed_decision",
                context=review_context,
                metadata=metadata,
                checks=checks,
            )

        updated = _mark_decision(text, decision)
        if updated == text and f"`{decision}`" not in text:
            return self._blocked(
                note_path=note_path,
                decision=decision,
                summary="Automated Suman Reviewer blocked because the review note has no matching checkbox.",
                reason="decision_checkbox_missing",
                context=review_context,
                metadata=metadata,
                checks=checks,
            )
        note_path.write_text(updated, encoding="utf-8")
        outputs = self._outputs(
            note_path=note_path,
            decision=decision,
            status="succeeded",
            summary=reason,
            context=review_context,
            metadata=metadata,
            checks=checks,
        )
        receipt_path = self._write_receipt(note_path, outputs)
        return AutomatedSumanReviewResult(
            status="succeeded",
            operation="automated_review",
            decision=decision,
            human_ref=self.human_ref,
            reviewer_id=self.reviewer_id,
            note_path=str(note_path),
            receipt_path=str(receipt_path) if receipt_path is not None else None,
            summary=reason,
            outputs=outputs,
        )

    def _desired_intent(
        self,
        *,
        note_path: Path,
        metadata: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        checks: dict[str, Any] = {
            "test_only": bool(metadata.get("test_only")) or bool(context.get("test_only")),
            "non_live": bool(metadata.get("non_live")) or bool(context.get("non_live")),
            "required_artifacts_present": True,
            "adoption_blockers_absent": True,
            "unsafe_effect_absent": True,
        }
        override = self._override_for(note_path, metadata, context)
        scope_ok = checks["test_only"] and checks["non_live"]
        if not scope_ok:
            return (
                "park",
                "Automated Suman Reviewer parked the gate because explicit test-only/non-live scope is missing.",
                {**checks, "scope_ok": False},
            )

        unsafe_reasons = _unsafe_reasons(metadata, context)
        if unsafe_reasons:
            return (
                "park",
                "Automated Suman Reviewer refused approval for unsafe live-effect scope.",
                {**checks, "unsafe_effect_absent": False, "unsafe_reasons": unsafe_reasons},
            )

        missing_artifacts = _missing_required_artifacts(context.get("required_artifacts", ()))
        if missing_artifacts:
            return (
                "revise",
                "Automated Suman Reviewer requested revision because required artifacts are missing.",
                {**checks, "required_artifacts_present": False, "missing_artifacts": missing_artifacts},
            )

        blockers = _blockers(context)
        if blockers:
            return (
                "revise",
                "Automated Suman Reviewer requested revision because adoption blockers remain.",
                {**checks, "adoption_blockers_absent": False, "adoption_blockers": blockers},
            )

        if context.get("public_publish_blocked") is False:
            return (
                "park",
                "Automated Suman Reviewer parked the gate because public publish is not blocked.",
                {**checks, "public_publish_blocked": False},
            )

        if override:
            normalized = _intent_for_decision(override)
            if normalized == "approve":
                return "approve", "Automated Suman Reviewer applied a safe approval override.", checks
            if normalized == "revise":
                return "revise", "Automated Suman Reviewer applied a revision override.", checks
            return "park", "Automated Suman Reviewer applied a park/block override.", checks

        return "approve", "Automated Suman Reviewer approved the safe local shadow/test gate.", checks

    def _override_for(
        self,
        note_path: Path,
        metadata: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> str | None:
        direct = context.get("override_decision")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        for key in (
            str(note_path),
            note_path.name,
            str(metadata.get("stage_id") or ""),
            str(metadata.get("gate_id") or ""),
        ):
            if key and key in self.override_decisions:
                return str(self.override_decisions[key])
        return None

    def _blocked(
        self,
        *,
        note_path: Path | None,
        decision: str | None,
        summary: str,
        reason: str,
        context: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
        checks: Mapping[str, Any] | None = None,
    ) -> AutomatedSumanReviewResult:
        outputs = self._outputs(
            note_path=note_path,
            decision=decision,
            status="blocked",
            summary=summary,
            context=context,
            metadata=metadata or {},
            checks={**dict(checks or {}), "blocked_reason": reason},
        )
        receipt_path = self._write_receipt(note_path, outputs)
        return AutomatedSumanReviewResult(
            status="blocked",
            operation="automated_review",
            decision=decision,
            human_ref=self.human_ref,
            reviewer_id=self.reviewer_id,
            note_path=str(note_path) if note_path is not None else None,
            receipt_path=str(receipt_path) if receipt_path is not None else None,
            summary=summary,
            outputs=outputs,
        )

    def _outputs(
        self,
        *,
        note_path: Path | None,
        decision: str | None,
        status: str,
        summary: str,
        context: Mapping[str, Any],
        metadata: Mapping[str, Any],
        checks: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema": AUTOMATED_SUMAN_REVIEWER_SCHEMA,
            "reviewer_id": self.reviewer_id,
            "human_ref": self.human_ref,
            "canonical_surface": self.canonical_surface,
            "decision": decision,
            "status": status,
            "summary": summary,
            "source_note_path": str(note_path) if note_path is not None else None,
            "stage_id": metadata.get("stage_id"),
            "stage_run_id": metadata.get("stage_run_id"),
            "gate_id": metadata.get("gate_id"),
            "action_fingerprint": metadata.get("action_fingerprint"),
            "allowed_scope": "tests/shadow/local_review_packets_only",
            "test_only": True,
            "non_live": True,
            "live_effect_approved": False,
            "created_at": self.created_at,
            "checks": dict(checks),
            "context": _jsonable_context(context),
        }

    def _write_receipt(self, note_path: Path | None, outputs: Mapping[str, Any]) -> Path | None:
        receipt_dir = self.receipt_dir
        if receipt_dir is None and note_path is not None:
            receipt_dir = note_path.parents[1] / "reviewer_decisions"
        if receipt_dir is None:
            return None
        receipt_dir.mkdir(parents=True, exist_ok=True)
        seed = json.dumps(outputs, sort_keys=True, default=str)
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        stage = str(outputs.get("stage_id") or "human_gate")
        receipt_path = receipt_dir / f"{_slug(stage)}-{digest}.json"
        payload = dict(outputs)
        payload["receipt_path"] = str(receipt_path)
        if payload.get("created_at") is None:
            payload["created_at"] = iso_timestamp(None)
        receipt_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return receipt_path


def _note_path_from_surface_ref(surface_ref: Mapping[str, Any]) -> Path | None:
    raw = surface_ref.get("note_path")
    if raw is None:
        raw = surface_ref.get("external_id")
    if raw is None:
        return None
    return Path(str(raw)).expanduser().resolve()


def _extract_frontmatter(text: str) -> dict[str, Any]:
    match = _FRONTMATTER_RE.search(text)
    if match is None:
        return {}
    metadata: dict[str, Any] = {}
    for raw_line in match.group("body").splitlines():
        if ":" not in raw_line:
            continue
        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        try:
            metadata[key] = json.loads(raw_value)
        except json.JSONDecodeError:
            metadata[key] = raw_value
    return metadata


def _allowed_decisions(text: str, metadata: Mapping[str, Any]) -> tuple[str, ...]:
    configured = metadata.get("allowed_decisions")
    if isinstance(configured, str):
        return (configured,)
    if isinstance(configured, (list, tuple)):
        return tuple(str(item) for item in configured)
    decisions: list[str] = []
    for line in text.splitlines():
        match = _CHECKBOX_RE.match(line)
        if match:
            decisions.append(match.group("label").strip())
    return tuple(decisions)


def _select_allowed_decision(allowed: tuple[str, ...], intent: str) -> str | None:
    candidates = {
        "approve": _APPROVAL_INTENTS,
        "revise": _REVISION_INTENTS,
        "park": _PARK_INTENTS,
    }.get(intent, (intent,))
    for candidate in candidates:
        if candidate in allowed:
            return candidate
    return None


def _mark_decision(text: str, decision: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        match = _CHECKBOX_RE.match(line)
        if match is None:
            lines.append(line)
            continue
        label = match.group("label").strip()
        mark = "x" if label == decision else " "
        lines.append(f"{match.group('prefix')}[{mark}]{match.group('suffix')}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _intent_for_decision(decision: str) -> str:
    text = decision.strip()
    if text in _APPROVAL_INTENTS:
        return "approve"
    if text in _REVISION_INTENTS:
        return "revise"
    return "park"


def _missing_required_artifacts(value: Any) -> list[str]:
    missing: list[str] = []
    for index, item in enumerate(_as_sequence(value), start=1):
        if isinstance(item, Mapping):
            label = str(item.get("path") or item.get("uri") or item.get("name") or f"artifact_{index}")
            if item.get("exists") is False or item.get("present") is False:
                missing.append(label)
                continue
            raw_path = item.get("path")
            if raw_path and not Path(str(raw_path)).exists():
                missing.append(label)
            continue
        path = Path(str(item))
        if not path.exists():
            missing.append(str(path))
    return missing


def _blockers(context: Mapping[str, Any]) -> list[Any]:
    blockers: list[Any] = []
    for key in ("adoption_blockers", "readiness_blockers", "blockers"):
        blockers.extend(_as_sequence(context.get(key, ())))
    return [item for item in blockers if item]


def _unsafe_reasons(metadata: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in _UNSAFE_CONTEXT_KEYS:
        for value in _as_sequence(context.get(key, ())):
            normalized = _normalize_effect(value)
            if normalized in _UNSAFE_EFFECTS:
                reasons.append(f"{key}:{normalized}")
    action_text = " ".join(
        str(metadata.get(key) or context.get(key) or "")
        for key in ("requested_action", "exact_action", "exact_action_approved", "action")
    )
    normalized_action = _normalize_text(action_text)
    if _action_requests_public_publish(normalized_action):
        reasons.append("action:public_publish")
    for phrase, label in (
        ("external send", "external_send"),
        ("send telegram", "telegram_send"),
        ("telegram send", "telegram_send"),
        ("deploy", "deploy"),
        ("live trade", "live_trade"),
        ("broker", "live_trade"),
        ("auth", "auth"),
        ("credential", "auth"),
        ("secret", "auth"),
        ("money", "money"),
        ("delete", "destructive_change"),
        ("destructive", "destructive_change"),
        ("obsidian write", "obsidian_write"),
        ("northstar write", "northstar_write"),
        ("oldmac mutation", "oldmac_mutation"),
    ):
        if phrase in normalized_action:
            reasons.append(f"action:{label}")
    return sorted(dict.fromkeys(reasons))


def _action_requests_public_publish(text: str) -> bool:
    if "public_publish" not in text and "public publish" not in text and "publish publicly" not in text:
        return False
    safe_markers = (
        "public publish blocked",
        "keep public publish blocked",
        "does not authorize public publish",
        "does not authorize public publishing",
        "do not publish",
        "without public publish",
    )
    return not any(marker in text for marker in safe_markers)


def _normalize_effect(value: Any) -> str:
    text = value.value if hasattr(value, "value") else str(value)
    return _normalize_text(text).replace(" ", "_")


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", " ", value.lower()).strip()


def _as_sequence(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        return tuple(value.values())
    try:
        return tuple(value)
    except TypeError:
        return (value,)


def _jsonable_context(context: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(context), sort_keys=True, default=str))


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return slug or "human-gate"
