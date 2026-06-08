"""Registration helpers for the generic native A2A review-loop adapter."""

from __future__ import annotations

from typing import Any

from agent_workflow_kernel import AdapterRegistration, RiskClass

from .runtime import A2AReviewRuntimeAdapter


def a2a_runtime_registrations(**kwargs: Any) -> tuple[AdapterRegistration, ...]:
    """Return the runtime registration for :class:`runtime.a2a`."""

    adapter = A2AReviewRuntimeAdapter(**kwargs)
    return (
        AdapterRegistration.from_runtime_adapter(
            adapter,
            side_effects=(RiskClass.LOCAL_DRAFT, RiskClass.REVIEW_ONLY),
            replay_safe=True,
        ),
    )
