"""Registration helpers for native Ivy/Jonah AWK adapters."""

from __future__ import annotations

from typing import Any

from agent_workflow_kernel import AdapterRegistration
from agent_workflow_kernel_artifact_validation import artifact_hash_validator_registrations
from agent_workflow_kernel_a2a import a2a_runtime_registrations


def ivy_editorial_registrations(**kwargs: Any) -> tuple[AdapterRegistration, ...]:
    """Build standard registrations for the native editorial lane adapters."""

    kwargs_runtime = dict(kwargs.pop("runtime", {}))
    kwargs_lane = dict(kwargs.pop("lane", {}))

    # Keep runtime wiring in the generic A2A package for compatibility and reuse.
    (runtime_registration,) = a2a_runtime_registrations(**kwargs_runtime)
    (lane_registration,) = artifact_hash_validator_registrations(**kwargs_lane)

    return (
        runtime_registration,
        lane_registration,
    )
