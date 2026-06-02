"""Validation helpers for workflow definitions.

The kernel validates the portable graph shape here. Runtime-specific adapter
resolution, prompt rendering, policy mechanics, and domain interpretation live
outside this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import StageType, WorkflowDef, WorkflowStatus
from .policy import ALLOWED_TRANSITION_GUARDS


class WorkflowValidationError(ValueError):
    """Raised when a workflow definition is not valid kernel DSL."""


REQUIRED_TOP_LEVEL_SECTIONS = ("schema", "workflow", "inputs", "stages", "transitions")
REQUIRED_WORKFLOW_FIELDS = ("id", "version", "name")
REQUIRED_STAGE_FIELDS = ("id", "type", "adapter", "outcomes")
ALLOWED_TERMINAL_STATUSES = frozenset(status.value for status in WorkflowStatus)


def require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowValidationError(f"{label} must be a mapping")
    return value


def require_sequence(value: Any, *, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WorkflowValidationError(f"{label} must be a sequence")
    return value


def validate_workflow_mapping(data: Mapping[str, Any]) -> None:
    """Validate raw DSL mapping before it is normalized into contracts."""

    for section in REQUIRED_TOP_LEVEL_SECTIONS:
        if section not in data:
            raise WorkflowValidationError(f"missing required top-level section: {section}")

    workflow = require_mapping(data["workflow"], label="workflow")
    for field in REQUIRED_WORKFLOW_FIELDS:
        if not workflow.get(field):
            raise WorkflowValidationError(f"workflow.{field} is required")

    require_mapping(data["inputs"], label="inputs")
    defaults = data.get("defaults")
    if defaults is not None:
        require_mapping(defaults, label="defaults")
        _validate_optional_lease(defaults.get("lease"), "defaults.lease")
    actors = data.get("actors")
    if actors is not None:
        actor_map = require_mapping(actors, label="actors")
        for actor_name, actor in actor_map.items():
            if not isinstance(actor, Mapping):
                continue
            _validate_optional_lease(actor.get("lease"), f"actors.{actor_name}.lease")
    stages = require_sequence(data["stages"], label="stages")
    transitions = require_sequence(data["transitions"], label="transitions")

    stage_ids: set[str] = set()
    outcomes_by_stage: dict[str, set[str]] = {}

    for index, raw_stage in enumerate(stages):
        stage = require_mapping(raw_stage, label=f"stages[{index}]")
        for field in REQUIRED_STAGE_FIELDS:
            if field not in stage or stage[field] in (None, ""):
                raise WorkflowValidationError(f"stages[{index}].{field} is required")

        stage_id = _require_str(stage["id"], f"stages[{index}].id")
        if stage_id in stage_ids:
            raise WorkflowValidationError(f"duplicate stage id: {stage_id}")
        stage_ids.add(stage_id)

        _coerce_stage_type(stage["type"], f"stages[{index}].type")
        _require_str(stage["adapter"], f"stages[{index}].adapter")
        _validate_optional_lease(stage.get("lease"), f"stages[{index}].lease")

        outcomes = require_sequence(stage["outcomes"], label=f"stages[{index}].outcomes")
        if not outcomes:
            raise WorkflowValidationError(f"stages[{index}].outcomes must not be empty")
        outcome_names: set[str] = set()
        for outcome_index, outcome in enumerate(outcomes):
            outcome_name = _require_str(
                outcome,
                f"stages[{index}].outcomes[{outcome_index}]",
            )
            if outcome_name in outcome_names:
                raise WorkflowValidationError(
                    f"duplicate outcome {outcome_name!r} on stage {stage_id}"
                )
            outcome_names.add(outcome_name)
        outcomes_by_stage[stage_id] = outcome_names

    transition_keys: set[tuple[str, str]] = set()

    for index, raw_transition in enumerate(transitions):
        transition = require_mapping(raw_transition, label=f"transitions[{index}]")
        from_stage = _require_str(transition.get("from"), f"transitions[{index}].from")
        outcome = _require_str(transition.get("on"), f"transitions[{index}].on")
        transition_key = (from_stage, outcome)
        if transition_key in transition_keys:
            raise WorkflowValidationError(
                f"duplicate transition for stage {from_stage!r} outcome {outcome!r}"
            )
        transition_keys.add(transition_key)

        if from_stage not in stage_ids:
            raise WorkflowValidationError(
                f"transition references unknown from stage: {from_stage}"
            )
        if outcome not in outcomes_by_stage[from_stage]:
            raise WorkflowValidationError(
                f"transition outcome {outcome!r} is not declared by stage {from_stage}"
            )

        has_to = bool(transition.get("to"))
        has_terminal = bool(transition.get("terminal"))
        if has_to == has_terminal:
            raise WorkflowValidationError(
                f"transitions[{index}] must define exactly one of 'to' or 'terminal'"
            )

        guard = transition.get("guard")
        if guard not in (None, ""):
            guard_name = _require_str(guard, f"transitions[{index}].guard")
            if guard_name not in ALLOWED_TRANSITION_GUARDS:
                allowed = ", ".join(sorted(ALLOWED_TRANSITION_GUARDS))
                raise WorkflowValidationError(
                    f"unknown transition guard {guard_name!r}; expected one of: {allowed}"
                )

        if has_to:
            to_stage = _require_str(transition["to"], f"transitions[{index}].to")
            if to_stage not in stage_ids:
                raise WorkflowValidationError(
                    f"transition references unknown target stage: {to_stage}"
                )
        else:
            terminal = _require_str(
                transition["terminal"],
                f"transitions[{index}].terminal",
            )
            if terminal not in ALLOWED_TERMINAL_STATUSES:
                allowed = ", ".join(sorted(ALLOWED_TERMINAL_STATUSES))
                raise WorkflowValidationError(
                    f"unknown terminal status {terminal!r}; expected one of: {allowed}"
                )


def validate_workflow_def(workflow: WorkflowDef) -> None:
    """Validate an already-normalized workflow contract."""

    raw: dict[str, Any] = {
        "schema": workflow.schema,
        "workflow": {
            "id": workflow.id,
            "version": workflow.version,
            "name": workflow.name,
        },
        "inputs": workflow.inputs,
        "stages": [
            {
                "id": stage.id,
                "type": stage.type.value,
                "adapter": stage.adapter,
                "outcomes": list(stage.outcomes),
                **({"lease": stage.lease} if stage.lease else {}),
            }
            for stage in workflow.stages
        ],
        "transitions": [
            {
                "from": transition.from_stage,
                "on": transition.on,
                **({"to": transition.to_stage} if transition.to_stage else {}),
                **({"terminal": transition.terminal} if transition.terminal else {}),
                **({"guard": transition.guard} if transition.guard else {}),
            }
            for transition in workflow.transitions
        ],
    }
    validate_workflow_mapping(raw)


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowValidationError(f"{label} must be a non-empty string")
    return value


def _coerce_stage_type(value: Any, label: str) -> StageType:
    try:
        return StageType(value)
    except ValueError as exc:
        allowed = ", ".join(stage_type.value for stage_type in StageType)
        raise WorkflowValidationError(
            f"unknown stage type {value!r} at {label}; expected one of: {allowed}"
        ) from exc


def _validate_optional_lease(value: Any, label: str) -> None:
    if value in (None, ""):
        return
    lease = require_mapping(value, label=label)
    unknown_keys = set(lease) - {"seconds"}
    if unknown_keys:
        unknown = ", ".join(sorted(str(key) for key in unknown_keys))
        raise WorkflowValidationError(f"{label} has unknown field(s): {unknown}")
    if "seconds" not in lease:
        raise WorkflowValidationError(f"{label}.seconds is required")
    seconds = lease["seconds"]
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        raise WorkflowValidationError(f"{label}.seconds must be a positive integer")
