"""Stateless kernel helper functions.

Extracted verbatim from kernel.py (behavior-identical). Private module-level
functions used by WorkflowKernel; separated so kernel.py holds just the
orchestrator class. Internal to the package.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping

from .adapter_registry import (
    AdapterRegistration,
    AdapterRegistry,
    AdapterRegistryError,
    adapter_family_for_stage,
)
from .adapters import (
    ADAPTER_STATUS_SUCCEEDED,
    make_adapter_receipt,
)
from .contracts import (
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    ArtifactRef,
    FailureClass,
    Receipt,
    RiskClass,
    StageDef,
    StageRun,
    StageRunStatus,
    StageType,
    Transition,
    WorkflowDef,
    WorkflowInstance,
    WorkflowStatus,
    to_plain_data,
)
from .dsl import workflow_to_canonical_json
from .lease import resolved_lease_policy_from_stage_run, resolve_stage_lease_policy
from .policy import (
    ALLOWED_TRANSITION_GUARDS,
    FAIL_CLOSED_TRANSITION_GUARDS,
    ActionRequest,
    ApprovalDecision,
    GateDecision,
    HardGate,
    HumanApprovalReceipt,
    PolicyEngine,
)
from .prompts import (
    PromptHashMismatchError,
    PromptRegistry,
    PromptRegistryError,
    RenderedContext,
    digest_data,
    render_context_packet,
)
from .receipts import build_prompt_provenance
from .runner import RunnerResult, WorkflowRunner
from .storage import WorkflowLedger, iso_timestamp

from ._internal_types import *  # noqa: F401,F403 (shared kernel types/constants)


def _index_transitions(transitions: tuple[Transition, ...]) -> dict[tuple[str, str], Transition]:
    indexed: dict[tuple[str, str], Transition] = {}
    for transition in transitions:
        key = (transition.from_stage, transition.on)
        if key in indexed:
            raise ValueError(
                f"duplicate transition for stage {transition.from_stage!r} outcome {transition.on!r}"
            )
        indexed[key] = transition
    return indexed


def _effective_policy_for_stage(
    workflow: WorkflowDef,
    stage: StageDef,
    *,
    registration: AdapterRegistration | None,
    include_stage_policy: bool = True,
) -> _EffectivePolicy:
    risk_classes: list[RiskClass] = []
    hard_gates: list[HardGate] = []
    forbidden_actions: list[str] = []
    unknown_policy_refs: list[str] = []
    side_effects_known = True
    side_effects_ambiguous = False

    layers: dict[str, Any] = {
        "workflow_defaults": to_plain_data(workflow.defaults),
        "workflow_policies": to_plain_data(workflow.policies),
        "stage_policy": to_plain_data(stage.policy) if include_stage_policy else {},
        "adapter_policy": None,
    }

    def merge(components: _PolicyComponents) -> None:
        nonlocal side_effects_known, side_effects_ambiguous
        risk_classes.extend(components.risk_classes)
        hard_gates.extend(components.hard_gates)
        forbidden_actions.extend(components.forbidden_actions)
        unknown_policy_refs.extend(components.unknown_policy_refs)
        side_effects_known = side_effects_known and components.side_effects_known
        side_effects_ambiguous = side_effects_ambiguous or components.side_effects_ambiguous

    merge(_policy_components(workflow.defaults.get("policy_class")))
    merge(_policy_components(workflow.defaults.get("capability_policy")))
    merge(_policy_components(workflow.policies))
    if include_stage_policy:
        merge(_policy_components(stage.policy))

    if registration is not None:
        risk_classes.extend(registration.side_effects)
        adapter_layer = {
            "adapter_id": registration.adapter_id,
            "family": registration.family.value,
            "side_effects": [risk.value for risk in registration.side_effects],
            "replay_safe": registration.replay_safe,
            "requires_idempotency_key": registration.requires_idempotency_key,
            "metadata": to_plain_data(registration.metadata),
        }
        layers["adapter_policy"] = adapter_layer
        merge(_policy_components(registration.metadata.get("policy")))
        merge(_policy_components(registration.metadata.get("side_effects")))
        merge(_policy_components(registration.metadata.get("risk_classes")))
        merge(_policy_components(registration.metadata.get("hard_gates")))
        merge(_surface_contract_policy_components(registration.metadata.get("surface_contract")))
    elif stage.type == StageType.HUMAN_GATE:
        risk_classes.append(RiskClass.REVIEW_ONLY)

    if unknown_policy_refs:
        side_effects_known = False
        side_effects_ambiguous = True

    deduped_risks = _dedupe_risk_classes(risk_classes) or (RiskClass.READ_ONLY,)
    return _EffectivePolicy(
        risk_classes=deduped_risks,
        hard_gates=_dedupe_hard_gates(hard_gates),
        forbidden_actions=_dedupe_strings(forbidden_actions),
        side_effects_known=side_effects_known,
        side_effects_ambiguous=side_effects_ambiguous,
        unknown_policy_refs=_dedupe_strings(unknown_policy_refs),
        layers=layers,
    )


def _stage_action_request(
    workflow: WorkflowDef,
    stage: StageDef,
    run: StageRun,
    *,
    registration: AdapterRegistration | None,
    operation: str,
    effective_policy: _EffectivePolicy,
    target_ref: str | None = None,
    extra_arguments: Mapping[str, Any] | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> ActionRequest:
    arguments: dict[str, Any] = {
        "stage_id": stage.id,
        "stage_run_id": run.stage_run_id,
        "stage_type": stage.type.value,
        "effective_policy": to_plain_data(effective_policy),
    }
    if extra_arguments:
        arguments.update(dict(extra_arguments))
    adapter_ref = registration.adapter_id if registration is not None else stage.adapter
    return ActionRequest(
        action=operation,
        target_ref=target_ref or adapter_ref,
        arguments=arguments,
        risk_classes=effective_policy.risk_classes,
        hard_gates=effective_policy.hard_gates,
        workflow_id=workflow.id,
        instance_id=run.instance_id,
        stage_id=stage.id,
        actor_ref=run.actor_ref,
        adapter_ref=adapter_ref,
        evidence_refs=evidence_refs,
        forbidden_actions=effective_policy.forbidden_actions,
        side_effects_known=effective_policy.side_effects_known,
        side_effects_ambiguous=effective_policy.side_effects_ambiguous,
    )


def _policy_components(value: Any) -> _PolicyComponents:
    if value in (None, ""):
        return _PolicyComponents()
    if isinstance(value, RiskClass):
        return _PolicyComponents(risk_classes=(value,))
    if isinstance(value, HardGate):
        return _PolicyComponents(hard_gates=(value,))
    if isinstance(value, str):
        return _policy_class_components(value)
    if isinstance(value, Mapping):
        risk_classes: list[RiskClass] = []
        hard_gates: list[HardGate] = []
        forbidden_actions: list[str] = []
        unknown_policy_refs: list[str] = []
        side_effects_known = True
        side_effects_ambiguous = False

        for key in ("class", "policy_class", "risk_class", "policy_ref"):
            components = _policy_components(value.get(key))
            risk_classes.extend(components.risk_classes)
            hard_gates.extend(components.hard_gates)
            unknown_policy_refs.extend(components.unknown_policy_refs)
            side_effects_known = side_effects_known and components.side_effects_known
            side_effects_ambiguous = side_effects_ambiguous or components.side_effects_ambiguous

        for key in ("classes", "risk_classes", "side_effects"):
            components = _policy_components(value.get(key))
            risk_classes.extend(components.risk_classes)
            hard_gates.extend(components.hard_gates)
            unknown_policy_refs.extend(components.unknown_policy_refs)
            side_effects_known = side_effects_known and components.side_effects_known
            side_effects_ambiguous = side_effects_ambiguous or components.side_effects_ambiguous

        hard_components = _hard_gate_components(value.get("hard_gates"))
        risk_classes.extend(hard_components.risk_classes)
        hard_gates.extend(hard_components.hard_gates)
        unknown_policy_refs.extend(hard_components.unknown_policy_refs)

        forbidden_actions.extend(_string_tuple(value.get("forbidden_actions")))
        forbidden_actions.extend(_string_tuple(value.get("forbidden")))
        if value.get("external_publish_allowed") is False:
            forbidden_actions.extend(("public_publish", "publish", "external_send"))
        external_effects = value.get("external_effects")
        if external_effects is True:
            risk_classes.append(RiskClass.EXTERNAL_EFFECT)
            hard_gates.append(HardGate.EXTERNAL_SEND)
            side_effects_ambiguous = True
        elif isinstance(external_effects, str) or (
            isinstance(external_effects, tuple | list | set | frozenset)
            and external_effects
        ):
            risk_classes.append(RiskClass.EXTERNAL_EFFECT)
            hard_gates.append(HardGate.EXTERNAL_SEND)
        if value.get("reads_private_source") is True:
            risk_classes.append(RiskClass.READ_ONLY)

        requires_approval = (
            value.get("requires_explicit_approval") is True
            or value.get("requires_prior_approval") is True
        )
        dry_run_only = value.get("dry_run_only") is True
        has_hard_policy = bool(hard_gates) or any(
            risk
            in {
                RiskClass.EXTERNAL_EFFECT,
                RiskClass.PRODUCTION_EFFECT,
                RiskClass.FINANCIAL_EFFECT,
                RiskClass.AUTH_EFFECT,
                RiskClass.DESTRUCTIVE_EFFECT,
                RiskClass.FORBIDDEN,
            }
            for risk in risk_classes
        )
        if requires_approval and not has_hard_policy and not dry_run_only:
            side_effects_ambiguous = True
        if value.get("side_effects_known") is False:
            side_effects_known = False
        if value.get("side_effects_ambiguous") is True:
            side_effects_ambiguous = True

        return _PolicyComponents(
            risk_classes=_dedupe_risk_classes(risk_classes),
            hard_gates=_dedupe_hard_gates(hard_gates),
            forbidden_actions=_dedupe_strings(forbidden_actions),
            side_effects_known=side_effects_known,
            side_effects_ambiguous=side_effects_ambiguous,
            unknown_policy_refs=_dedupe_strings(unknown_policy_refs),
        )
    if isinstance(value, tuple | list | set | frozenset):
        risk_classes: list[RiskClass] = []
        hard_gates: list[HardGate] = []
        forbidden_actions: list[str] = []
        unknown_policy_refs: list[str] = []
        side_effects_known = True
        side_effects_ambiguous = False
        for item in value:
            components = _policy_components(item)
            risk_classes.extend(components.risk_classes)
            hard_gates.extend(components.hard_gates)
            forbidden_actions.extend(components.forbidden_actions)
            unknown_policy_refs.extend(components.unknown_policy_refs)
            side_effects_known = side_effects_known and components.side_effects_known
            side_effects_ambiguous = side_effects_ambiguous or components.side_effects_ambiguous
        return _PolicyComponents(
            risk_classes=_dedupe_risk_classes(risk_classes),
            hard_gates=_dedupe_hard_gates(hard_gates),
            forbidden_actions=_dedupe_strings(forbidden_actions),
            side_effects_known=side_effects_known,
            side_effects_ambiguous=side_effects_ambiguous,
            unknown_policy_refs=_dedupe_strings(unknown_policy_refs),
        )
    return _PolicyComponents(unknown_policy_refs=(str(value),), side_effects_known=False, side_effects_ambiguous=True)


def _policy_class_components(name: str) -> _PolicyComponents:
    normalized = _normalize_policy_name(name)
    if not normalized:
        return _PolicyComponents()
    if normalized in _POLICY_CLASS_MAP:
        risks, gates = _POLICY_CLASS_MAP[normalized]
        return _PolicyComponents(risk_classes=risks, hard_gates=gates)
    try:
        return _PolicyComponents(risk_classes=(RiskClass(normalized),))
    except ValueError:
        pass
    try:
        gate = HardGate(normalized)
        return _PolicyComponents(hard_gates=(gate,), risk_classes=_risk_classes_for_hard_gate(gate))
    except ValueError:
        return _PolicyComponents(
            unknown_policy_refs=(name,),
            side_effects_known=False,
            side_effects_ambiguous=True,
        )


def _hard_gate_components(value: Any) -> _PolicyComponents:
    if value in (None, ""):
        return _PolicyComponents()
    if isinstance(value, tuple | list | set | frozenset):
        risks: list[RiskClass] = []
        gates: list[HardGate] = []
        unknown: list[str] = []
        for item in value:
            component = _hard_gate_components(item)
            risks.extend(component.risk_classes)
            gates.extend(component.hard_gates)
            unknown.extend(component.unknown_policy_refs)
        return _PolicyComponents(
            risk_classes=_dedupe_risk_classes(risks),
            hard_gates=_dedupe_hard_gates(gates),
            unknown_policy_refs=_dedupe_strings(unknown),
            side_effects_known=not bool(unknown),
            side_effects_ambiguous=bool(unknown),
        )
    text = _normalize_policy_name(str(value))
    try:
        gate = HardGate(text)
    except ValueError:
        return _PolicyComponents(
            unknown_policy_refs=(str(value),),
            side_effects_known=False,
            side_effects_ambiguous=True,
        )
    return _PolicyComponents(hard_gates=(gate,), risk_classes=_risk_classes_for_hard_gate(gate))


def _surface_contract_policy_components(value: Any) -> _PolicyComponents:
    if not isinstance(value, Mapping):
        return _PolicyComponents()
    dry_run_only = bool(value.get("dry_run_only", False))
    live_mutation_allowed = bool(value.get("live_mutation_allowed", False))
    external_effects = _string_tuple(value.get("external_effects"))
    if dry_run_only and not live_mutation_allowed:
        return _PolicyComponents()
    if live_mutation_allowed or external_effects:
        return _PolicyComponents(
            risk_classes=(RiskClass.EXTERNAL_EFFECT,),
            hard_gates=(HardGate.EXTERNAL_SEND,),
            side_effects_ambiguous=not bool(external_effects),
        )
    return _PolicyComponents()


def _guard_has_required_artifacts(
    *,
    stage: StageDef,
    adapter_result: AdapterResult | None,
    guard: str,
) -> _GuardDecision:
    required_roles = _required_artifact_roles(stage.outputs)
    if not required_roles:
        return _GuardDecision(True, guard, "stage declares no required artifact roles")
    if adapter_result is None:
        return _GuardDecision(
            False,
            guard,
            "Transition guard 'has_required_artifacts' cannot inspect artifacts for this stage result.",
            {"required_roles": list(required_roles)},
        )
    present_roles = tuple(artifact.role for artifact in adapter_result.artifact_refs)
    missing = tuple(role for role in required_roles if role not in present_roles)
    if missing:
        return _GuardDecision(
            False,
            guard,
            "Transition guard 'has_required_artifacts' blocked missing required artifact roles: "
            + ", ".join(missing),
            {"required_roles": list(required_roles), "present_roles": list(present_roles)},
        )
    return _GuardDecision(
        True,
        guard,
        "all required artifact roles are present",
        {"required_roles": list(required_roles), "present_roles": list(present_roles)},
    )


def _resolve_budget_limit(
    *,
    workflow: WorkflowDef,
    stage: StageDef,
    target_stage: StageDef | None,
    guard: str,
) -> _BudgetLimit:
    for source, candidate in _budget_sources_for_guard(
        workflow=workflow,
        stage=stage,
        target_stage=target_stage,
        guard=guard,
    ):
        budget = _budget_limit_from_mapping(source=source, value=candidate, guard=guard)
        if budget is not None:
            return budget
    return _BudgetLimit(
        limit=None,
        source=None,
        key=None,
        reason=f"no configured budget found for {guard}",
    )


def _budget_sources_for_guard(
    *,
    workflow: WorkflowDef,
    stage: StageDef,
    target_stage: StageDef | None,
    guard: str,
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    sources: list[tuple[str, Mapping[str, Any]]] = []

    def add(source: str, value: Any) -> None:
        if isinstance(value, Mapping):
            sources.append((source, value))

    add(f"stages.{stage.id}.budget", stage.budget)
    if guard == "within_retry_budget":
        add(f"stages.{stage.id}.retry", stage.retry)
    add(f"stages.{stage.id}.policy", stage.policy)
    if target_stage is not None and target_stage.id != stage.id:
        add(f"stages.{target_stage.id}.budget", target_stage.budget)
        if guard == "within_retry_budget":
            add(f"stages.{target_stage.id}.retry", target_stage.retry)
        add(f"stages.{target_stage.id}.policy", target_stage.policy)
    defaults = workflow.defaults
    if guard == "within_retry_budget":
        add("defaults.retry", defaults.get("retry"))
    add("defaults.budget", defaults.get("budget"))
    add("defaults", defaults)
    add("policies.budget", workflow.policies.get("budget"))
    add("policies", workflow.policies)
    return tuple(sources)


def _budget_limit_from_mapping(
    *,
    source: str,
    value: Mapping[str, Any],
    guard: str,
) -> _BudgetLimit | None:
    for key in _BUDGET_GUARD_KEYS[guard]:
        if key in value:
            return _coerce_budget_limit(value[key], source=source, key=key)
    for nested_key in _BUDGET_GUARD_NESTED_KEYS[guard]:
        nested = value.get(nested_key)
        if isinstance(nested, Mapping):
            for key in _BUDGET_GUARD_KEYS[guard]:
                if key in nested:
                    return _coerce_budget_limit(
                        nested[key],
                        source=f"{source}.{nested_key}",
                        key=key,
                    )
        elif nested_key in value and nested not in (None, ""):
            return _BudgetLimit(
                limit=None,
                source=source,
                key=nested_key,
                reason="budget section is not a mapping",
            )
    return None


def _coerce_budget_limit(value: Any, *, source: str, key: str) -> _BudgetLimit:
    if isinstance(value, bool):
        return _BudgetLimit(
            limit=None,
            source=source,
            key=key,
            reason="budget value must be an integer, not boolean",
        )
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return _BudgetLimit(
            limit=None,
            source=source,
            key=key,
            reason="budget value must be an integer",
        )
    if limit < 0:
        return _BudgetLimit(
            limit=None,
            source=source,
            key=key,
            reason="budget value must be non-negative",
        )
    return _BudgetLimit(limit=limit, source=source, key=key)


def _budget_guard_consumes_transition(guard: str, transition: Transition) -> bool:
    outcome = transition.on.strip().lower().replace("-", "_").replace(" ", "_")
    return outcome in _BUDGET_CONSUMING_OUTCOMES.get(guard, frozenset())


def _policy_snapshot(gate: Any, effective_policy: _EffectivePolicy | None = None) -> dict[str, Any]:
    snapshot = to_plain_data(gate)
    if effective_policy is not None:
        snapshot["effective_policy"] = to_plain_data(effective_policy)
    return snapshot


def _normalize_policy_name(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _dedupe_risk_classes(values: list[RiskClass] | tuple[RiskClass, ...]) -> tuple[RiskClass, ...]:
    seen: set[RiskClass] = set()
    result: list[RiskClass] = []
    for risk in values:
        if risk in seen:
            continue
        seen.add(risk)
        result.append(risk)
    return tuple(result)


def _dedupe_hard_gates(values: list[HardGate] | tuple[HardGate, ...]) -> tuple[HardGate, ...]:
    seen: set[HardGate] = set()
    result: list[HardGate] = []
    for gate in values:
        if gate in seen:
            continue
        seen.add(gate)
        result.append(gate)
    return tuple(result)


def _dedupe_strings(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _risk_classes_for_hard_gate(gate: HardGate) -> tuple[RiskClass, ...]:
    return _HARD_GATE_RISK_MAP.get(gate, (RiskClass.EXTERNAL_EFFECT,))


_POLICY_CLASS_MAP: dict[str, tuple[tuple[RiskClass, ...], tuple[HardGate, ...]]] = {
    "read_only": ((RiskClass.READ_ONLY,), ()),
    "read_only_review": ((RiskClass.READ_ONLY,), ()),
    "local_draft": ((RiskClass.LOCAL_DRAFT,), ()),
    "internal_generation": ((RiskClass.LOCAL_DRAFT,), ()),
    "public_draft_review": ((RiskClass.LOCAL_DRAFT, RiskClass.REVIEW_ONLY), ()),
    "public_publish_preflight": ((RiskClass.REVIEW_ONLY, RiskClass.INTERNAL_STATE), ()),
    "review_only": ((RiskClass.REVIEW_ONLY,), ()),
    "internal_state": ((RiskClass.INTERNAL_STATE,), ()),
    "external_effect": ((RiskClass.EXTERNAL_EFFECT,), (HardGate.EXTERNAL_SEND,)),
    "external_send": ((RiskClass.EXTERNAL_EFFECT,), (HardGate.EXTERNAL_SEND,)),
    "external_effect_or_uncertain_review": (
        (RiskClass.EXTERNAL_EFFECT,),
        (HardGate.EXTERNAL_SEND,),
    ),
    "public_publish": ((RiskClass.EXTERNAL_EFFECT,), (HardGate.PUBLIC_PUBLISH,)),
    "deploy": ((RiskClass.PRODUCTION_EFFECT,), (HardGate.DEPLOY,)),
    "deploy_or_prod_mutation": ((RiskClass.PRODUCTION_EFFECT,), (HardGate.DEPLOY,)),
    "production_effect": ((RiskClass.PRODUCTION_EFFECT,), (HardGate.DEPLOY,)),
    "auth": ((RiskClass.AUTH_EFFECT,), (HardGate.AUTH,)),
    "auth_effect": ((RiskClass.AUTH_EFFECT,), (HardGate.AUTH,)),
    "auth_or_secret_change": ((RiskClass.AUTH_EFFECT,), (HardGate.AUTH,)),
    "money": ((RiskClass.FINANCIAL_EFFECT,), (HardGate.MONEY,)),
    "financial_effect": ((RiskClass.FINANCIAL_EFFECT,), (HardGate.MONEY,)),
    "money_or_broker_action": (
        (RiskClass.FINANCIAL_EFFECT,),
        (HardGate.MONEY, HardGate.LIVE_TRADE),
    ),
    "high_cost_compute": ((RiskClass.FINANCIAL_EFFECT,), (HardGate.MONEY,)),
    "destructive_change": ((RiskClass.DESTRUCTIVE_EFFECT,), (HardGate.DESTRUCTIVE_CHANGE,)),
    "destructive_effect": ((RiskClass.DESTRUCTIVE_EFFECT,), (HardGate.DESTRUCTIVE_CHANGE,)),
    "forbidden": ((RiskClass.FORBIDDEN,), ()),
}

_HARD_GATE_RISK_MAP: dict[HardGate, tuple[RiskClass, ...]] = {
    HardGate.PUBLIC_PUBLISH: (RiskClass.EXTERNAL_EFFECT,),
    HardGate.DEPLOY: (RiskClass.PRODUCTION_EFFECT,),
    HardGate.LIVE_TRADE: (RiskClass.FINANCIAL_EFFECT,),
    HardGate.AUTH: (RiskClass.AUTH_EFFECT,),
    HardGate.MONEY: (RiskClass.FINANCIAL_EFFECT,),
    HardGate.EXTERNAL_SEND: (RiskClass.EXTERNAL_EFFECT,),
    HardGate.DESTRUCTIVE_CHANGE: (RiskClass.DESTRUCTIVE_EFFECT,),
}


def _operation_for_stage(stage: StageDef, *, adapter_family: AdapterFamily) -> str:
    operation = stage.inputs.get("operation")
    if operation is not None:
        return str(operation)
    if adapter_family == AdapterFamily.LANE:
        return "build_stage_input"
    if adapter_family == AdapterFamily.SURFACE:
        return "publish"
    if adapter_family == AdapterFamily.HOST:
        return "invoke"
    return "invoke"


def _human_decision_action(stage: StageDef) -> str:
    operation = stage.inputs.get("decision_action") or stage.inputs.get("operation")
    if operation is not None:
        return str(operation)
    return "human_decision"


def _human_gate_allowed_decisions(stage: StageDef) -> tuple[str, ...]:
    configured = stage.surface.get("allowed_decisions", stage.inputs.get("allowed_decisions"))
    if configured is not None:
        return _string_tuple(configured)
    choice_options = _human_gate_choice_options(stage)
    if choice_options:
        return tuple(str(option["id"]) for option in choice_options)
    if stage.outcomes:
        return tuple(stage.outcomes)
    return (
        ApprovalDecision.APPROVED.value,
        ApprovalDecision.REJECTED.value,
        ApprovalDecision.REVISE.value,
        ApprovalDecision.PARK.value,
    )


def _human_gate_evidence_refs(stage: StageDef, gate: Any | None) -> tuple[str, ...]:
    configured = stage.surface.get("evidence_refs", stage.inputs.get("evidence_refs"))
    if configured is not None:
        return _string_tuple(configured)
    if gate is not None:
        return _string_tuple(getattr(gate, "evidence_refs", ()))
    return ()


def _human_gate_action_arguments(stage: StageDef) -> dict[str, Any]:
    arguments: dict[str, Any] = {"outcomes": list(stage.outcomes)}
    choice_manifest = _human_gate_choice_manifest(stage)
    if choice_manifest:
        arguments["choice_option_ids"] = [
            str(option["id"]) for option in choice_manifest.get("options", ())
        ]
        arguments["choice_manifest_hash"] = digest_data(choice_manifest)
    return arguments


def _human_gate_choice_manifest(stage: StageDef) -> dict[str, Any]:
    options = _human_gate_choice_options(stage)
    if not options:
        return {}
    return {
        "schema": "human_gate_choice_manifest.v1",
        "stage_id": stage.id,
        "options": list(options),
    }


def _human_gate_choice_options(stage: StageDef) -> tuple[dict[str, Any], ...]:
    configured = (
        stage.surface.get("choice_options")
        or stage.inputs.get("choice_options")
        or stage.surface.get("options")
        or stage.inputs.get("options")
    )
    if configured is None:
        return ()
    if isinstance(configured, Mapping):
        configured = [
            {"id": key, **(value if isinstance(value, Mapping) else {"label": value})}
            for key, value in configured.items()
        ]
    if isinstance(configured, str):
        configured = (configured,)
    options: list[dict[str, Any]] = []
    for index, raw_option in enumerate(configured, start=1):
        if isinstance(raw_option, Mapping):
            option = to_plain_data(raw_option)
            if not isinstance(option, Mapping):
                continue
            option_id = (
                option.get("id")
                or option.get("decision")
                or option.get("value")
                or option.get("label")
                or f"option_{index}"
            )
            normalized = {str(key): option[key] for key in sorted(option, key=str)}
            normalized["id"] = str(option_id)
            normalized.setdefault("label", str(option_id))
            options.append(normalized)
        else:
            option_id = str(raw_option)
            options.append({"id": option_id, "label": option_id})
    return tuple(options)


def _human_ref(stage: StageDef) -> str:
    configured = stage.surface.get("human_ref", stage.inputs.get("human_ref"))
    if configured is not None:
        return str(configured)
    return _actor_ref(stage) or "human"


def _outcome_for_stage_result(stage: StageDef, adapter_result: AdapterResult | None) -> str:
    if adapter_result is not None:
        outcome = adapter_result.outputs.get("outcome")
        if isinstance(outcome, str) and outcome:
            return outcome
        if adapter_result.next_hint in stage.outcomes:
            return str(adapter_result.next_hint)
        if adapter_result.status in stage.outcomes:
            return adapter_result.status
    if len(stage.outcomes) == 1:
        return stage.outcomes[0]
    return "succeeded"


def _workflow_status_for_terminal(terminal: str) -> WorkflowStatus:
    mapping = {
        "done": WorkflowStatus.DONE,
        "blocked": WorkflowStatus.BLOCKED,
        "policy_denied": WorkflowStatus.POLICY_DENIED,
        "waiting_on_schedule": WorkflowStatus.WAITING_ON_SCHEDULE,
        "final_approval_required": WorkflowStatus.FINAL_APPROVAL_REQUIRED,
        "cancelled": WorkflowStatus.CANCELLED,
    }
    return mapping.get(terminal, WorkflowStatus.BLOCKED)


def _side_effect_scope(
    registration: AdapterRegistration,
    stage: StageDef,
    operation: str,
    gate: Any,
) -> dict[str, Any]:
    return {
        "workflow_adapter_ref": stage.adapter,
        "adapter_id": registration.adapter_id,
        "adapter_family": registration.family.value,
        "operation": operation,
        "stage_id": stage.id,
        "stage_type": stage.type.value,
        "side_effects": [risk.value for risk in registration.side_effects],
        "replay_safe": registration.replay_safe,
        "requires_idempotency_key": registration.requires_idempotency_key,
        "policy_decision": getattr(gate, "decision", None),
        "policy_reason": getattr(gate, "decision_reason", None),
    }


def _stage_output_contract_errors(stage: StageDef, adapter_result: AdapterResult) -> list[str]:
    errors: list[str] = []
    required_artifact_roles = _required_artifact_roles(stage.outputs)
    produced_roles = {artifact.role for artifact in adapter_result.artifact_refs}
    for role in required_artifact_roles:
        if role not in produced_roles:
            errors.append(f"missing required artifact role {role!r}")
    for artifact in adapter_result.artifact_refs:
        if artifact.role in required_artifact_roles:
            if not artifact.uri:
                errors.append(f"required artifact role {artifact.role!r} is missing a uri")
            if not artifact.content_hash:
                errors.append(f"required artifact role {artifact.role!r} is missing a content_hash")

    for field_path in _required_output_fields(stage.outputs):
        if not _output_path_exists(adapter_result.outputs, field_path):
            errors.append(f"missing required output field {field_path!r}")
    return errors


def _required_artifact_roles(outputs: Mapping[str, Any]) -> tuple[str, ...]:
    artifacts = outputs.get("artifacts") or ()
    roles: list[str] = []
    if isinstance(artifacts, Mapping):
        for role, spec in artifacts.items():
            required = bool(spec.get("required", False)) if isinstance(spec, Mapping) else bool(spec)
            if required:
                roles.append(str(role))
        return _dedupe_strings(roles)
    for index, artifact in enumerate(artifacts, start=1):
        if not isinstance(artifact, Mapping):
            continue
        if bool(artifact.get("required", False)):
            roles.append(str(artifact.get("role") or f"artifact_{index}"))
    return _dedupe_strings(roles)


def _required_output_fields(outputs: Mapping[str, Any]) -> tuple[str, ...]:
    fields: list[str] = []
    for key in ("required_fields", "required_outputs"):
        configured = outputs.get(key)
        if configured is not None:
            fields.extend(_string_tuple(configured))

    field_specs = outputs.get("fields")
    if isinstance(field_specs, Mapping):
        field_specs = [
            {"name": name, **(spec if isinstance(spec, Mapping) else {})}
            for name, spec in field_specs.items()
        ]
    if isinstance(field_specs, (list, tuple)):
        for spec in field_specs:
            if isinstance(spec, Mapping) and bool(spec.get("required", False)):
                name = spec.get("name") or spec.get("field")
                if name:
                    fields.append(str(name))

    schema = outputs.get("outcome_schema")
    if isinstance(schema, Mapping):
        fields.extend(_string_tuple(schema.get("required")))
    return tuple(dict.fromkeys(fields))


def _output_path_exists(outputs: Mapping[str, Any], field_path: str) -> bool:
    current: Any = outputs
    for part in field_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return current not in (None, "")


def _invoke_registered_stage_adapter(
    registration: AdapterRegistration,
    *,
    invocation: AdapterInvocation,
    workflow: WorkflowDef,
    stage: StageDef,
    run: StageRun,
    stage_input: Mapping[str, Any],
    created_at: str,
) -> AdapterResult:
    if registration.family == AdapterFamily.RUNTIME:
        return registration.adapter.invoke(invocation, stage_input)
    if registration.family == AdapterFamily.LANE:
        return _invoke_lane_stage_adapter(
            registration,
            invocation=invocation,
            workflow=workflow,
            stage=stage,
            run=run,
            stage_input=stage_input,
            created_at=created_at,
        )
    if registration.family == AdapterFamily.HOST and hasattr(registration.adapter, "invoke"):
        return registration.adapter.invoke(invocation, stage_input)
    raise ValueError(
        f"adapter family {registration.family.value!r} is not supported for owned stage invocation"
    )


def _invoke_lane_stage_adapter(
    registration: AdapterRegistration,
    *,
    invocation: AdapterInvocation,
    workflow: WorkflowDef,
    stage: StageDef,
    run: StageRun,
    stage_input: Mapping[str, Any],
    created_at: str,
) -> AdapterResult:
    adapter = registration.adapter
    operation = invocation.operation
    if operation == "open_work":
        outputs = dict(adapter.open_work(stage_input))
        artifact_refs: tuple[ArtifactRef, ...] = ()
    elif operation == "build_stage_input":
        outputs = dict(adapter.build_stage_input(run, stage_input))
        artifact_refs = _artifact_refs_from_stage_input(outputs)
    elif operation == "prepare_human_gate":
        outputs = dict(adapter.prepare_human_gate(run, stage_input))
        artifact_refs = ()
    elif operation == "validate_artifacts":
        artifact_refs = _artifact_refs_from_stage_input(stage_input)
        receipt = adapter.validate_artifacts(run, artifact_refs)
        outputs = {
            "lane_receipt_ref": receipt.receipt_id,
            "lane_receipt": to_plain_data(receipt),
            "artifact_count": len(artifact_refs),
        }
        return AdapterResult(
            invocation_id=invocation.invocation_id,
            status=receipt.status,
            outputs=_with_default_lane_outcome(stage, outputs),
            artifact_refs=receipt.artifact_refs,
            receipt_ref=receipt.receipt_id,
            residual_risk=receipt.residual_risk,
            next_hint=receipt.next_action,
        )
    else:
        raise ValueError(
            f"{registration.adapter_id} does not implement owned lane operation {operation!r}"
        )

    plain_outputs = _with_default_lane_outcome(stage, outputs)
    receipt = make_adapter_receipt(
        invocation,
        status=ADAPTER_STATUS_SUCCEEDED,
        summary=(
            f"Kernel invoked lane adapter {registration.adapter_id}.{operation} "
            f"for workflow {workflow.id}."
        ),
        created_at=created_at,
        stage_id=stage.id,
        artifact_refs=artifact_refs,
        outputs=plain_outputs,
        checks_run=("lane_adapter_registered", "owned_lane_operation_supported"),
    )
    return AdapterResult(
        invocation_id=invocation.invocation_id,
        status=ADAPTER_STATUS_SUCCEEDED,
        outputs=plain_outputs,
        artifact_refs=receipt.artifact_refs,
        receipt_ref=receipt.receipt_id,
        residual_risk=receipt.residual_risk,
        next_hint=receipt.next_action,
    )


def _artifact_refs_from_stage_input(stage_input: Mapping[str, Any]) -> tuple[ArtifactRef, ...]:
    raw_refs = stage_input.get("artifact_refs")
    if not isinstance(raw_refs, (list, tuple)):
        return ()
    refs: list[ArtifactRef] = []
    for raw in raw_refs:
        if isinstance(raw, ArtifactRef):
            refs.append(raw)
        elif isinstance(raw, Mapping):
            try:
                refs.append(
                    ArtifactRef(
                        artifact_id=str(raw["artifact_id"]),
                        role=str(raw["role"]),
                        uri=str(raw["uri"]),
                        content_hash=str(raw["content_hash"]),
                        mime_type=str(raw.get("mime_type", "text/plain")),
                        size_bytes=raw.get("size_bytes"),
                        created_by=raw.get("created_by"),
                        visibility=str(raw.get("visibility", "internal")),
                    )
                )
            except KeyError:
                continue
    return tuple(refs)


def _with_default_lane_outcome(stage: StageDef, outputs: Mapping[str, Any]) -> dict[str, Any]:
    plain_outputs = dict(outputs)
    if not isinstance(plain_outputs.get("outcome"), str) or not plain_outputs.get("outcome"):
        plain_outputs["outcome"] = _default_success_outcome_for_stage(stage)
    return plain_outputs


def _default_success_outcome_for_stage(stage: StageDef) -> str:
    preferred_by_type = {
        StageType.AGENT_WORK: ("ready", "revised", "done"),
        StageType.AGENT_GATE: ("approved_for_generation", "support", "accepted", "pass"),
        StageType.A2A_REVIEW_LOOP: ("pass", "accepted"),
        StageType.SYSTEM_ACTION: (
            "ready",
            "valid",
            "ready_for_approval",
            "package_ready",
            "approval_needed",
            "surfaced",
            "verified",
            "applied",
            "done_without_publish",
            "done",
        ),
        StageType.WAIT_SCHEDULE: ("ready", "skipped"),
        StageType.RECOVERY: ("still_running", "resumed"),
    }
    for outcome in preferred_by_type.get(stage.type, ()):
        if outcome in stage.outcomes:
            return outcome
    if len(stage.outcomes) == 1:
        return stage.outcomes[0]
    return ADAPTER_STATUS_SUCCEEDED


def _retry_result_for_adapter_failure(
    *,
    stage: StageDef,
    run: StageRun,
    registration: AdapterRegistration,
    adapter_result: AdapterResult,
    created_at: str,
) -> RunnerResult | None:
    if not _retry_enabled(stage.retry):
        return None
    max_attempts = _retry_max_attempts(stage.retry)
    if run.attempt >= max_attempts:
        return None
    if not _retry_is_safe(registration, run):
        return RunnerResult(
            decision="blocked",
            failure_class=FailureClass.UNKNOWN_SIDE_EFFECT_STATE,
            failure_summary=(
                f"Adapter returned {adapter_result.status}; retry policy is configured, "
                "but replay is not proven safe for this adapter side-effect scope."
            ),
            approval_required=True,
        )
    return RunnerResult(
        decision="retry",
        failure_class=FailureClass.RUNTIME_FAILURE,
        failure_summary=f"Adapter returned {adapter_result.status}; queued append-only retry.",
        retry_after_at=_retry_after_at(created_at, stage.retry),
    )


def _retry_enabled(retry: Mapping[str, Any]) -> bool:
    if bool(retry.get("enabled", False)):
        return True
    return _retry_max_attempts(retry) > 1


def _retry_max_attempts(retry: Mapping[str, Any]) -> int:
    try:
        return max(1, int(retry.get("max_attempts", 1)))
    except (TypeError, ValueError):
        return 1


def _retry_is_safe(registration: AdapterRegistration, run: StageRun) -> bool:
    safe_side_effects = {
        RiskClass.READ_ONLY,
        RiskClass.LOCAL_DRAFT,
        RiskClass.REVIEW_ONLY,
        RiskClass.INTERNAL_STATE,
    }
    if registration.replay_safe:
        return True
    if all(effect in safe_side_effects for effect in registration.side_effects):
        return True
    return bool(run.idempotency_key) and not registration.requires_idempotency_key


def _retry_after_at(created_at: str, retry: Mapping[str, Any]) -> str:
    try:
        backoff_seconds = int(retry.get("backoff_seconds", 0))
    except (TypeError, ValueError):
        backoff_seconds = 0
    base = _coerce_datetime(created_at) or datetime.now(UTC)
    return iso_timestamp(base + timedelta(seconds=max(0, backoff_seconds)))


def _human_decision_validation_error(
    decision: HumanApprovalReceipt | None,
    gate: Any,
    *,
    now: Any,
    allowed_decisions: tuple[str, ...] = (),
) -> str | None:
    if decision is None:
        return "Missing human decision receipt."
    decision_text = _decision_text(decision.decision)
    if not decision.approval_id:
        return "Human decision receipt is missing an approval_id."
    if not decision.human_ref:
        return "Human decision receipt is missing a human_ref."
    if not decision.canonical_surface:
        return "Human decision receipt is missing a canonical_surface."
    if decision_text not in _KNOWN_HUMAN_DECISIONS and decision_text not in allowed_decisions:
        return f"Unsupported human decision {decision_text!r}."
    if decision.gate_id != gate.gate_id:
        return "Human decision receipt does not match the waiting gate."
    if decision.exact_action_approved != gate.requested_action:
        return "Human decision receipt does not name the exact waiting action."
    if decision.action_fingerprint != gate.action_fingerprint:
        return "Human decision receipt fingerprint does not match the waiting gate."
    current_time = _coerce_datetime(now) or datetime.now(UTC)
    revoked_at = _coerce_datetime(decision.revoked_at)
    if revoked_at is not None and revoked_at <= current_time:
        return "Human decision receipt has been revoked."
    expires_at = _coerce_datetime(decision.expires_at)
    if expires_at is not None and expires_at <= current_time:
        return "Human decision receipt has expired."
    return None


def _human_decision_outcome_candidates(decision_text: str) -> tuple[str, ...]:
    aliases = {
        "approved": ("approved", "approval_granted", "read_clear", "clear", "done", "succeeded"),
        "approve": ("approve", "approved", "approval_granted", "read_clear", "clear", "done", "succeeded"),
        "approval_granted": ("approval_granted", "approved", "read_clear", "clear", "done", "succeeded"),
        "read_clear": ("read_clear", "approved", "approval_granted", "clear", "done", "succeeded"),
        "clear": ("clear", "read_clear", "approved", "approval_granted", "done", "succeeded"),
        "rejected": ("rejected", "reject", "denied", "approval_denied", "blocked"),
        "reject": ("reject", "rejected", "denied", "approval_denied", "blocked"),
        "denied": ("denied", "rejected", "reject", "approval_denied", "blocked"),
        "revise": ("revise", "revision_requested", "revise_plan", "needs_revision"),
        "revision_requested": ("revision_requested", "revise", "revise_plan", "needs_revision"),
        "park": ("park", "parked", "defer", "blocked"),
        "parked": ("parked", "park", "defer", "blocked"),
        "defer": ("defer", "park", "parked", "blocked"),
        "blocked": ("blocked", "park", "defer"),
    }
    return aliases.get(decision_text, (decision_text,))


def _decision_text(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


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


_KNOWN_HUMAN_DECISIONS = frozenset(
    {
        ApprovalDecision.APPROVED.value,
        ApprovalDecision.REJECTED.value,
        ApprovalDecision.REVISE.value,
        ApprovalDecision.PARK.value,
        "approve",
        "approval_granted",
        "read_clear",
        "clear",
        "reject",
        "denied",
        "approval_denied",
        "revision_requested",
        "revise_plan",
        "needs_revision",
        "parked",
        "defer",
        "blocked",
    }
)

_APPROVING_HUMAN_DECISIONS = frozenset(
    {
        ApprovalDecision.APPROVED.value,
        "approve",
        "approval_granted",
        "read_clear",
        "clear",
        "done",
        "succeeded",
    }
)


def _runtime_input(
    workflow: WorkflowDef,
    stage: StageDef,
    run: StageRun,
    rendered_context: RenderedContext | None = None,
    *,
    ledger: WorkflowLedger | None = None,
) -> dict[str, Any]:
    payload = {
        "workflow": {"id": workflow.id, "version": workflow.version},
        "stage": to_plain_data(stage),
        "stage_run": to_plain_data(run),
    }
    workflow_inputs: Mapping[str, Any] = {}
    if ledger is not None:
        workflow_inputs = ledger.get_workflow_input_snapshot(run.instance_id) or {}
        payload["workflow_inputs"] = dict(workflow_inputs)
        human_decisions = _prior_human_decisions(ledger, run.instance_id)
        if human_decisions:
            payload["prior_human_decisions"] = human_decisions
            payload["latest_human_decision"] = human_decisions[-1]
        artifact_refs = _prior_artifact_refs(ledger, run.instance_id)
        if artifact_refs:
            payload["artifact_refs"] = artifact_refs
            payload["artifacts_by_stage"] = _artifacts_by_stage(artifact_refs)
        receipts = _prior_receipts(ledger, run.instance_id)
        if receipts:
            payload["prior_receipts"] = receipts
        receipt_outputs = _prior_receipts_with_outputs(ledger, run.instance_id)
        if receipt_outputs:
            payload["receipts_by_stage"] = _receipts_by_stage(receipt_outputs)
    payload["inputs"] = _resolve_stage_inputs(stage.inputs, payload, workflow_inputs)
    if rendered_context is not None:
        payload["context_packet"] = rendered_context.packet_data
        payload["rendered_input"] = rendered_context.rendered_input
        payload["rendered_input_digest"] = rendered_context.rendered_input_digest
    return payload


def _resolve_stage_inputs(
    stage_inputs: Mapping[str, Any],
    runtime_payload: Mapping[str, Any],
    workflow_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        str(key): _resolve_stage_input_value(value, runtime_payload, workflow_inputs)
        for key, value in stage_inputs.items()
    }


def _resolve_stage_input_value(
    value: Any,
    runtime_payload: Mapping[str, Any],
    workflow_inputs: Mapping[str, Any],
) -> Any:
    if isinstance(value, str):
        if value.startswith("input."):
            return _path_get(workflow_inputs, value.removeprefix("input."))
        if value.startswith("artifacts."):
            artifacts_by_stage = runtime_payload.get("artifacts_by_stage")
            if not isinstance(artifacts_by_stage, Mapping):
                return None
            return _path_get(artifacts_by_stage, value.removeprefix("artifacts."))
        if value.startswith("receipts."):
            receipts_by_stage = runtime_payload.get("receipts_by_stage")
            if not isinstance(receipts_by_stage, Mapping):
                return None
            receipts = _path_get(receipts_by_stage, value.removeprefix("receipts."))
            if isinstance(receipts, list) and receipts:
                return receipts[-1]
            return receipts
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _resolve_stage_input_value(item, runtime_payload, workflow_inputs)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_stage_input_value(item, runtime_payload, workflow_inputs) for item in value]
    if isinstance(value, tuple):
        return tuple(_resolve_stage_input_value(item, runtime_payload, workflow_inputs) for item in value)
    return value


def _path_get(root: Mapping[str, Any], path: str) -> Any:
    current: Any = root
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _prior_human_decisions(ledger: WorkflowLedger, instance_id: str) -> list[dict[str, Any]]:
    rows = ledger.connection.execute(
        """
        SELECT decision_id, stage_run_id, gate_id, decision, human_ref,
               canonical_surface, action_fingerprint, receipt_json, created_at
        FROM human_decisions
        WHERE instance_id = ?
        ORDER BY created_at ASC, decision_id ASC
        """,
        (instance_id,),
    ).fetchall()
    decisions: list[dict[str, Any]] = []
    for row in rows:
        receipt = json.loads(row["receipt_json"])
        constraints = receipt.get("constraints", {}) if isinstance(receipt, Mapping) else {}
        decisions.append(
            {
                "decision_id": row["decision_id"],
                "stage_run_id": row["stage_run_id"],
                "gate_id": row["gate_id"],
                "decision": row["decision"],
                "human_ref": row["human_ref"],
                "canonical_surface": row["canonical_surface"],
                "action_fingerprint": row["action_fingerprint"],
                "selected_option": constraints.get("selected_option")
                if isinstance(constraints, Mapping)
                else None,
                "choice_manifest_hash": constraints.get("choice_manifest_hash")
                if isinstance(constraints, Mapping)
                else None,
                "receipt": receipt,
                "created_at": row["created_at"],
            }
        )
    return decisions


def _prior_artifact_refs(ledger: WorkflowLedger, instance_id: str) -> list[dict[str, Any]]:
    rows = ledger.connection.execute(
        """
        SELECT ar.*, sr.stage_id
        FROM artifact_refs ar
        LEFT JOIN stage_runs sr ON sr.stage_run_id = ar.stage_run_id
        WHERE ar.instance_id = ?
        ORDER BY ar.created_at ASC, ar.artifact_id ASC
        """,
        (instance_id,),
    ).fetchall()
    artifacts: list[dict[str, Any]] = []
    for row in rows:
        artifacts.append(
            {
                "artifact_id": row["artifact_id"],
                "stage_run_id": row["stage_run_id"],
                "stage_id": row["stage_id"],
                "receipt_id": row["receipt_id"],
                "role": row["role"],
                "uri": row["uri"],
                "content_hash": row["content_hash"],
                "mime_type": row["mime_type"],
                "size_bytes": row["size_bytes"],
                "created_by": row["created_by"],
                "visibility": row["visibility"],
                "created_at": row["created_at"],
            }
        )
    return artifacts


def _artifacts_by_stage(artifact_refs: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for artifact in artifact_refs:
        stage_id = artifact.get("stage_id")
        role = artifact.get("role")
        if not stage_id or not role:
            continue
        grouped.setdefault(str(stage_id), {})[str(role)] = artifact
    return grouped


def _prior_receipts_with_outputs(ledger: WorkflowLedger, instance_id: str) -> list[dict[str, Any]]:
    rows = ledger.connection.execute(
        """
        SELECT r.*, sr.stage_id
        FROM receipts r
        LEFT JOIN stage_runs sr ON sr.stage_run_id = r.stage_run_id
        WHERE r.instance_id = ?
        ORDER BY r.created_at ASC, r.receipt_id ASC
        """,
        (instance_id,),
    ).fetchall()
    receipts: list[dict[str, Any]] = []
    for row in rows:
        receipt = json.loads(row["receipt_json"])
        receipts.append(
            {
                "receipt_id": row["receipt_id"],
                "stage_run_id": row["stage_run_id"],
                "stage_id": row["stage_id"],
                "kind": row["receipt_kind"],
                "status": row["status"],
                "summary": row["summary"],
                "created_at": row["created_at"],
                "receipt": receipt,
                "outputs": _receipt_runtime_outputs(receipt),
            }
        )
    return receipts


def _receipts_by_stage(receipts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for receipt in receipts:
        stage_id = receipt.get("stage_id")
        if stage_id:
            grouped.setdefault(str(stage_id), []).append(receipt)
    return grouped


def _receipt_runtime_outputs(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    provenance = receipt.get("runtime_provenance", {})
    if not isinstance(provenance, Mapping):
        return {}
    outputs = provenance.get("outputs", {})
    return outputs if isinstance(outputs, Mapping) else {}


def _stage_run_prompt_provenance(
    ledger: WorkflowLedger,
    *,
    stage: StageDef,
    stage_run_id: str,
) -> dict[str, Any]:
    row = ledger.connection.execute(
        """
        SELECT prompt_hash, context_packet_ref, context_packet_hash, rendered_context_hash
        FROM stage_runs
        WHERE stage_run_id = ?
        """,
        (stage_run_id,),
    ).fetchone()
    if row is None or not row["prompt_hash"]:
        return {}
    return {
        "prompt_bundle_digest": row["prompt_hash"],
        "context_packet_ref": row["context_packet_ref"],
        "context_packet_hash": row["context_packet_hash"],
        "rendered_input_digest": row["rendered_context_hash"],
        "refs": [to_plain_data(ref) for ref in stage.prompt_refs],
    }


def _actor_ref(stage: StageDef) -> str | None:
    if not stage.actors:
        return None
    return str(stage.actors[next(iter(stage.actors))])


def _surface_ref_from_outputs(outputs: Mapping[str, Any]) -> Mapping[str, Any] | None:
    surface_ref = outputs.get("surface_ref")
    if isinstance(surface_ref, Mapping):
        return dict(surface_ref)
    return None


def _surface_external_ref(outputs: Mapping[str, Any]) -> str | None:
    surface_ref = _surface_ref_from_outputs(outputs)
    if surface_ref is None:
        return None
    for key in ("external_id", "surface_id", "note_path"):
        value = surface_ref.get(key)
        if value:
            return str(value)
    return None


def _receipt_outputs(receipt: Receipt) -> Mapping[str, Any]:
    outputs = receipt.runtime_provenance.get("outputs", {})
    return outputs if isinstance(outputs, Mapping) else {}


def _is_surface_decision_receipt(receipt: Receipt) -> bool:
    outputs = _receipt_outputs(receipt)
    decision = outputs.get("decision")
    return decision is not None and str(decision).strip() != ""


def _surface_ingest_failure_summary(
    decision_receipts: tuple[Receipt, ...],
    candidate_receipts: tuple[Receipt, ...],
) -> str:
    if not decision_receipts:
        return "Surface adapter returned no human decision receipts."
    if len(candidate_receipts) != 1:
        blocked = next((receipt for receipt in decision_receipts if receipt.status != ADAPTER_STATUS_SUCCEEDED), None)
        if blocked is not None:
            return blocked.summary
        return (
            "Surface decision ingest must return exactly one structured human decision "
            f"receipt; got {len(candidate_receipts)}."
        )
    if candidate_receipts[0].status != ADAPTER_STATUS_SUCCEEDED:
        return candidate_receipts[0].summary
    return ""


def _human_approval_from_surface_receipt(
    receipt: Receipt,
    *,
    gate: Any,
    surface_adapter_id: str,
) -> tuple[HumanApprovalReceipt | None, str | None]:
    outputs = _receipt_outputs(receipt)
    required_fields = (
        "gate_id",
        "human_ref",
        "canonical_surface",
        "decision",
        "exact_action_approved",
        "action_fingerprint",
    )
    missing = tuple(field for field in required_fields if not str(outputs.get(field, "")).strip())
    if missing:
        return None, (
            "Surface decision receipt is missing required approval fields: "
            + ", ".join(missing)
        )
    constraints = {
        "surface_adapter_id": surface_adapter_id,
        "surface_receipt_ref": receipt.receipt_id,
    }
    for flag in ("test_only", "non_live"):
        if flag in outputs:
            constraints[flag] = bool(outputs[flag])
    for key in ("selected_option", "choice_manifest", "choice_manifest_hash"):
        if key in outputs:
            constraints[key] = to_plain_data(outputs[key])
    return (
        HumanApprovalReceipt(
            approval_id=str(outputs.get("approval_id") or receipt.receipt_id),
            gate_id=str(outputs["gate_id"]),
            human_ref=str(outputs["human_ref"]),
            canonical_surface=str(outputs["canonical_surface"]),
            decision=_approval_decision_value(outputs["decision"]),
            exact_action_approved=str(outputs["exact_action_approved"]),
            action_fingerprint=str(outputs["action_fingerprint"]),
            evidence_refs=_string_tuple(outputs.get("evidence_refs", ())),
            constraints=constraints,
            created_at=receipt.created_at,
            transcript_or_message_ref=str(
                outputs.get("transcript_or_message_ref")
                or outputs.get("source_note_path")
                or receipt.receipt_id
            ),
        ),
        None,
    )


def _approval_decision_value(value: Any) -> ApprovalDecision | str:
    text = _decision_text(value)
    try:
        return ApprovalDecision(text)
    except ValueError:
        return text


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _make_kernel_adapter_receipt(
    invocation: AdapterInvocation,
    *,
    rendered_context: RenderedContext | None,
    lease_policy: Any | None = None,
    **kwargs: Any,
):
    receipt = make_adapter_receipt(invocation, **kwargs)
    outputs = kwargs.get("outputs")
    usage = _usage_from_outputs(outputs if isinstance(outputs, Mapping) else {})
    if usage:
        receipt = replace(
            receipt,
            runtime_provenance={
                **receipt.runtime_provenance,
                "usage": usage,
            },
        )
    if lease_policy is not None:
        receipt = replace(
            receipt,
            runtime_provenance={
                **receipt.runtime_provenance,
                "lease": to_plain_data(lease_policy),
            },
        )
    if rendered_context is None:
        return receipt
    return replace(
        receipt,
        context_packet_ref=rendered_context.packet.context_id,
        prompt_provenance=build_prompt_provenance(rendered_context),
    )


def _usage_from_outputs(outputs: Mapping[str, Any]) -> dict[str, Any]:
    usage = outputs.get("usage")
    if not isinstance(usage, Mapping):
        return {}
    allowed = {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "wall_time_ms",
        "cold_start",
        "session_id",
        "model",
        "source",
    }
    return {str(key): to_plain_data(value) for key, value in usage.items() if str(key) in allowed}


def _prior_receipts(ledger: WorkflowLedger, instance_id: str) -> tuple[Mapping[str, Any], ...]:
    rows = ledger.connection.execute(
        """
        SELECT receipt_json FROM receipts
        WHERE instance_id = ?
        ORDER BY created_at, receipt_id
        """,
        (instance_id,),
    ).fetchall()
    receipts = []
    for row in rows:
        try:
            receipts.append(json.loads(row["receipt_json"]))
        except (TypeError, json.JSONDecodeError):
            receipts.append({"unparseable_receipt": True})
    return tuple(receipts)

__all__ = [
    "_index_transitions",
    "_effective_policy_for_stage",
    "_stage_action_request",
    "_policy_components",
    "_policy_class_components",
    "_hard_gate_components",
    "_surface_contract_policy_components",
    "_guard_has_required_artifacts",
    "_resolve_budget_limit",
    "_budget_sources_for_guard",
    "_budget_limit_from_mapping",
    "_coerce_budget_limit",
    "_budget_guard_consumes_transition",
    "_policy_snapshot",
    "_normalize_policy_name",
    "_dedupe_risk_classes",
    "_dedupe_hard_gates",
    "_dedupe_strings",
    "_risk_classes_for_hard_gate",
    "_operation_for_stage",
    "_human_decision_action",
    "_human_gate_allowed_decisions",
    "_human_gate_evidence_refs",
    "_human_gate_action_arguments",
    "_human_gate_choice_manifest",
    "_human_gate_choice_options",
    "_human_ref",
    "_outcome_for_stage_result",
    "_workflow_status_for_terminal",
    "_side_effect_scope",
    "_stage_output_contract_errors",
    "_required_artifact_roles",
    "_required_output_fields",
    "_output_path_exists",
    "_invoke_registered_stage_adapter",
    "_invoke_lane_stage_adapter",
    "_artifact_refs_from_stage_input",
    "_with_default_lane_outcome",
    "_default_success_outcome_for_stage",
    "_retry_result_for_adapter_failure",
    "_retry_enabled",
    "_retry_max_attempts",
    "_retry_is_safe",
    "_retry_after_at",
    "_human_decision_validation_error",
    "_human_decision_outcome_candidates",
    "_decision_text",
    "_coerce_datetime",
    "_runtime_input",
    "_resolve_stage_inputs",
    "_resolve_stage_input_value",
    "_path_get",
    "_prior_human_decisions",
    "_prior_artifact_refs",
    "_artifacts_by_stage",
    "_prior_receipts_with_outputs",
    "_receipts_by_stage",
    "_receipt_runtime_outputs",
    "_stage_run_prompt_provenance",
    "_actor_ref",
    "_surface_ref_from_outputs",
    "_surface_external_ref",
    "_receipt_outputs",
    "_is_surface_decision_receipt",
    "_surface_ingest_failure_summary",
    "_human_approval_from_surface_receipt",
    "_approval_decision_value",
    "_string_tuple",
    "_make_kernel_adapter_receipt",
    "_usage_from_outputs",
    "_prior_receipts",
    "_POLICY_CLASS_MAP",
    "_HARD_GATE_RISK_MAP",
    "_KNOWN_HUMAN_DECISIONS",
    "_APPROVING_HUMAN_DECISIONS",
]
