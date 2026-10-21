"""Portable lease policy resolution for workflow stage claims."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import ResolvedLeasePolicy, StageDef, WorkflowDef


def resolve_stage_lease_policy(
    workflow: WorkflowDef,
    stage: StageDef,
    *,
    runtime_default_seconds: int,
    explicit_lease_seconds: int | None = None,
) -> ResolvedLeasePolicy:
    """Resolve the lease used to claim a stage run.

    Precedence is deliberately small and declarative:
    explicit runner override > stage lease > actor lease > workflow default >
    runtime default.
    """

    if explicit_lease_seconds is not None:
        seconds = _positive_int(explicit_lease_seconds, "explicit lease override")
        return ResolvedLeasePolicy(
            lease_seconds=seconds,
            source="runner_override",
            source_ref="WorkflowKernel.run_once.lease_seconds",
            actor_ref=_actor_ref(stage),
        )

    stage_seconds = _lease_seconds_from_mapping(stage.lease)
    if stage_seconds is not None:
        return ResolvedLeasePolicy(
            lease_seconds=stage_seconds,
            source="stage",
            source_ref=f"stages.{stage.id}.lease",
            actor_ref=_actor_ref(stage),
        )

    actor_ref, actor_config = _actor_config_for_stage(workflow, stage)
    actor_seconds = _lease_seconds_from_mapping(
        actor_config.get("lease") if isinstance(actor_config, Mapping) else None
    )
    if actor_seconds is not None:
        actor_name = _actor_name(actor_ref) or actor_ref or "unknown"
        return ResolvedLeasePolicy(
            lease_seconds=actor_seconds,
            source="actor",
            source_ref=f"actors.{actor_name}.lease",
            actor_ref=actor_ref,
        )

    workflow_seconds = _lease_seconds_from_mapping(workflow.defaults.get("lease"))
    if workflow_seconds is not None:
        return ResolvedLeasePolicy(
            lease_seconds=workflow_seconds,
            source="workflow_default",
            source_ref="defaults.lease",
            actor_ref=_actor_ref(stage),
        )

    return ResolvedLeasePolicy(
        lease_seconds=_positive_int(runtime_default_seconds, "runtime default lease"),
        source="runtime_default",
        source_ref="KernelRuntimeConfig.default_lease_seconds",
        actor_ref=_actor_ref(stage),
    )


def resolved_lease_policy_from_stage_run(run: Any) -> ResolvedLeasePolicy | None:
    seconds = getattr(run, "lease_seconds", None)
    source = getattr(run, "lease_source", None)
    source_ref = getattr(run, "lease_source_ref", None)
    if seconds is None or source is None or source_ref is None:
        return None
    return ResolvedLeasePolicy(
        lease_seconds=int(seconds),
        source=str(source),
        source_ref=str(source_ref),
        actor_ref=getattr(run, "actor_ref", None),
    )


def _lease_seconds_from_mapping(value: Any) -> int | None:
    if not isinstance(value, Mapping):
        return None
    raw_seconds = value.get("seconds")
    if raw_seconds in (None, ""):
        return None
    return _positive_int(raw_seconds, "lease.seconds")


def _positive_int(value: Any, label: str) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if seconds <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return seconds


def _actor_config_for_stage(
    workflow: WorkflowDef,
    stage: StageDef,
) -> tuple[str | None, Mapping[str, Any]]:
    actor_ref = _actor_ref(stage)
    actor_name = _actor_name(actor_ref)
    if actor_name and isinstance(workflow.actors.get(actor_name), Mapping):
        return actor_ref, workflow.actors[actor_name]
    if actor_ref and isinstance(workflow.actors.get(actor_ref), Mapping):
        return actor_ref, workflow.actors[actor_ref]
    return actor_ref, {}


def _actor_ref(stage: StageDef) -> str | None:
    if not stage.actors:
        return None
    return str(stage.actors[next(iter(stage.actors))])


def _actor_name(actor_ref: str | None) -> str | None:
    if not actor_ref:
        return None
    if actor_ref.startswith("actors."):
        return actor_ref.split(".", 1)[1]
    return actor_ref
