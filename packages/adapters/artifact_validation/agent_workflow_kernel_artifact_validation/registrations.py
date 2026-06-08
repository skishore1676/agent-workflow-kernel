"""Registration helpers for hash-validation lane adapters."""

from __future__ import annotations

from typing import Any

from agent_workflow_kernel import AdapterRegistration, RiskClass

from .lane import ArtifactHashValidatorAdapter


def artifact_hash_validator_registrations(**kwargs: Any) -> tuple[AdapterRegistration, ...]:
    """Build standard registrations for :class:`lane.artifact_hash_validator`."""

    adapter = ArtifactHashValidatorAdapter(**kwargs)
    return (
        AdapterRegistration.from_lane_adapter(
            adapter,
            side_effects=(RiskClass.READ_ONLY,),
            replay_safe=True,
        ),
    )
