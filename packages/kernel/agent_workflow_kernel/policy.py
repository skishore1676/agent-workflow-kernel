"""Generic policy evaluation and human approval checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping

from .contracts import PolicyGate, RiskClass


class GateDecision(StrEnum):
    ALLOW = "allow"
    ALLOW_WITH_RECEIPT = "allow_with_receipt"
    REQUIRE_HUMAN = "require_human"
    DENY = "deny"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISE = "revise"
    PARK = "park"


class HardGate(StrEnum):
    PUBLIC_PUBLISH = "public_publish"
    DEPLOY = "deploy"
    LIVE_TRADE = "live_trade"
    AUTH = "auth"
    MONEY = "money"
    EXTERNAL_SEND = "external_send"
    DESTRUCTIVE_CHANGE = "destructive_change"


HARD_GATE_RISK_CLASSES: frozenset[RiskClass] = frozenset(
    {
        RiskClass.EXTERNAL_EFFECT,
        RiskClass.PRODUCTION_EFFECT,
        RiskClass.FINANCIAL_EFFECT,
        RiskClass.AUTH_EFFECT,
        RiskClass.DESTRUCTIVE_EFFECT,
    }
)

DEFAULT_ALLOW_WITH_RECEIPT_RISK_CLASSES: frozenset[RiskClass] = frozenset(
    {
        RiskClass.READ_ONLY,
        RiskClass.LOCAL_DRAFT,
        RiskClass.REVIEW_ONLY,
        RiskClass.INTERNAL_STATE,
    }
)


@dataclass(slots=True, frozen=True)
class ActionRequest:
    """One exact action the kernel is being asked to permit."""

    action: str
    target_ref: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    artifact_hashes: tuple[str, ...] = ()
    context_packet_digest: str | None = None
    risk_classes: tuple[RiskClass, ...] = (RiskClass.READ_ONLY,)
    hard_gates: tuple[HardGate, ...] = ()
    workflow_id: str = "workflow"
    instance_id: str = "instance"
    stage_id: str = "stage"
    actor_ref: str | None = None
    adapter_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    side_effects_known: bool = True
    side_effects_ambiguous: bool = False


@dataclass(slots=True, frozen=True)
class HumanApprovalReceipt:
    """Human approval bound to one exact action fingerprint."""

    approval_id: str
    gate_id: str
    human_ref: str
    canonical_surface: str
    decision: ApprovalDecision
    exact_action_approved: str
    action_fingerprint: str
    evidence_refs: tuple[str, ...] = ()
    constraints: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime | str | None = None
    expires_at: datetime | str | None = None
    revoked_at: datetime | str | None = None
    transcript_or_message_ref: str | None = None


@dataclass(slots=True, frozen=True)
class ApprovalValidation:
    valid: bool
    reason: str


def action_fingerprint(
    *,
    action: str,
    target_ref: str,
    arguments: Mapping[str, Any] | None = None,
    artifact_hashes: tuple[str, ...] = (),
    context_packet_digest: str | None = None,
) -> str:
    """Return a stable digest for the exact action inputs that require approval."""

    payload = {
        "action": action,
        "target_ref": target_ref,
        "arguments": _canonical_data(arguments or {}),
        "artifact_hashes": list(artifact_hashes),
        "context_packet_digest": context_packet_digest,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fingerprint_request(request: ActionRequest) -> str:
    return action_fingerprint(
        action=request.action,
        target_ref=request.target_ref,
        arguments=request.arguments,
        artifact_hashes=request.artifact_hashes,
        context_packet_digest=request.context_packet_digest,
    )


def validate_approval(
    approval: HumanApprovalReceipt | None,
    *,
    expected_fingerprint: str,
    expected_action: str | None = None,
    now: datetime | str | None = None,
) -> ApprovalValidation:
    if approval is None:
        return ApprovalValidation(False, "missing approval receipt")
    if approval.decision is not ApprovalDecision.APPROVED:
        return ApprovalValidation(False, f"approval decision is {approval.decision.value}")
    if expected_action is not None and approval.exact_action_approved != expected_action:
        return ApprovalValidation(False, "approval does not name the exact action")
    if approval.action_fingerprint != expected_fingerprint:
        return ApprovalValidation(False, "approval fingerprint does not match action")
    current_time = _coerce_datetime(now) or datetime.now(UTC)
    revoked_at = _coerce_datetime(approval.revoked_at)
    if revoked_at is not None and revoked_at <= current_time:
        return ApprovalValidation(False, "approval has been revoked")
    expires_at = _coerce_datetime(approval.expires_at)
    if expires_at is not None and expires_at <= current_time:
        return ApprovalValidation(False, "approval has expired")
    return ApprovalValidation(True, "approval matches action")


class PolicyEngine:
    """Evaluate generic kernel policy without lane-specific exceptions."""

    def evaluate(
        self,
        request: ActionRequest,
        *,
        approval: HumanApprovalReceipt | None = None,
        now: datetime | str | None = None,
    ) -> PolicyGate:
        fingerprint = fingerprint_request(request)
        decision, reason, approval_ref = self._decide(
            request,
            fingerprint=fingerprint,
            approval=approval,
            now=now,
        )
        gate_id = f"gate-{fingerprint[:16]}"
        return PolicyGate(
            gate_id=gate_id,
            workflow_id=request.workflow_id,
            instance_id=request.instance_id,
            stage_id=request.stage_id,
            requested_action=request.action,
            action_fingerprint=fingerprint,
            risk_classes=request.risk_classes,
            decision=decision.value,
            evidence_refs=request.evidence_refs,
            approval_receipt_ref=approval_ref,
            decision_reason=reason,
        )

    def _decide(
        self,
        request: ActionRequest,
        *,
        fingerprint: str,
        approval: HumanApprovalReceipt | None,
        now: datetime | str | None,
    ) -> tuple[GateDecision, str, str | None]:
        if request.action in request.forbidden_actions or RiskClass.FORBIDDEN in request.risk_classes:
            return GateDecision.DENY, "action is forbidden by policy", None

        if not request.side_effects_known or request.side_effects_ambiguous:
            validation = validate_approval(
                approval,
                expected_fingerprint=fingerprint,
                expected_action=request.action,
                now=now,
            )
            if validation.valid:
                return GateDecision.ALLOW, "human approved ambiguous side effect", approval.approval_id
            return GateDecision.REQUIRE_HUMAN, "side effects are unknown or ambiguous", None

        if request.hard_gates or _has_hard_gate_risk(request.risk_classes):
            validation = validate_approval(
                approval,
                expected_fingerprint=fingerprint,
                expected_action=request.action,
                now=now,
            )
            if validation.valid:
                return GateDecision.ALLOW, "human approval matches hard-gated action", approval.approval_id
            return GateDecision.REQUIRE_HUMAN, validation.reason, None

        if all(risk in DEFAULT_ALLOW_WITH_RECEIPT_RISK_CLASSES for risk in request.risk_classes):
            return GateDecision.ALLOW_WITH_RECEIPT, "allowed with receipt", None

        return GateDecision.REQUIRE_HUMAN, "risk class requires human review", None


def _has_hard_gate_risk(risk_classes: tuple[RiskClass, ...]) -> bool:
    return any(risk in HARD_GATE_RISK_CLASSES for risk in risk_classes)


def _canonical_data(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return _canonical_data(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _canonical_data(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, tuple | list):
        return [_canonical_data(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(_canonical_data(item) for item in value)
    return value


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
