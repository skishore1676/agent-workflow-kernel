"""Shared internal kernel types, constants, and aliases.

Extracted from kernel.py so the WorkflowKernel class body and the kernel
helper functions (_helpers.py) can both import these without a circular
dependency. Internal to the package; not part of the public API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from .contracts import RiskClass, WorkflowStatus
from .policy import HardGate


KernelDecision = Literal["idle", "succeeded", "failed", "retry", "blocked", "waiting_on_human"]
KernelTransitionDecision = Literal["queued", "terminal", "blocked"]

BUDGET_TRANSITION_GUARDS = frozenset(
    {
        "within_retry_budget",
        "within_revision_budget",
        "within_ping_pong_budget",
        "within_research_iteration_budget",
        "within_resume_budget",
    }
)

_BUDGET_GUARD_KEYS: Mapping[str, tuple[str, ...]] = {
    "within_retry_budget": ("max_attempts", "max_retry_attempts", "max_retries"),
    "within_revision_budget": ("max_revision_turns", "max_revisions"),
    "within_ping_pong_budget": ("max_ping_pong_turns", "max_ping_pong"),
    "within_research_iteration_budget": (
        "max_research_iterations",
        "max_research_turns",
        "max_iterations",
    ),
    "within_resume_budget": ("max_resume_attempts", "max_resumes"),
}

_BUDGET_GUARD_NESTED_KEYS: Mapping[str, tuple[str, ...]] = {
    "within_retry_budget": ("retry", "retry_budget"),
    "within_revision_budget": ("revision_budget",),
    "within_ping_pong_budget": ("ping_pong_budget", "a2a_budget"),
    "within_research_iteration_budget": ("research_budget", "iteration_budget"),
    "within_resume_budget": ("resume_budget",),
}

_BUDGET_CONSUMING_OUTCOMES: Mapping[str, frozenset[str]] = {
    "within_retry_budget": frozenset({"retry", "retry_needed", "retry_scheduled"}),
    "within_revision_budget": frozenset({"needs_revision", "revise", "refine"}),
    "within_ping_pong_budget": frozenset({"question", "answer", "needs_clarification"}),
    "within_research_iteration_budget": frozenset(
        {"needs_more_research", "approve_more_research", "more_research"}
    ),
    "within_resume_budget": frozenset({"retry_needed", "resume_needed", "recover"}),
}

@dataclass(frozen=True, slots=True)
class _TransitionResult:
    decision: KernelTransitionDecision
    queued_stage_id: str | None = None
    terminal_status: WorkflowStatus | None = None
    failure_summary: str | None = None


@dataclass(frozen=True, slots=True)
class _EffectivePolicy:
    risk_classes: tuple[RiskClass, ...]
    hard_gates: tuple[HardGate, ...]
    forbidden_actions: tuple[str, ...]
    side_effects_known: bool
    side_effects_ambiguous: bool
    unknown_policy_refs: tuple[str, ...] = ()
    layers: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _GuardDecision:
    allowed: bool
    guard: str | None
    reason: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _BudgetLimit:
    limit: int | None
    source: str | None
    key: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _PolicyComponents:
    risk_classes: tuple[RiskClass, ...] = ()
    hard_gates: tuple[HardGate, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    side_effects_known: bool = True
    side_effects_ambiguous: bool = False
    unknown_policy_refs: tuple[str, ...] = ()

__all__ = [
    "KernelDecision",
    "KernelTransitionDecision",
    "BUDGET_TRANSITION_GUARDS",
    "_BUDGET_GUARD_KEYS",
    "_BUDGET_GUARD_NESTED_KEYS",
    "_BUDGET_CONSUMING_OUTCOMES",
    "_TransitionResult",
    "_EffectivePolicy",
    "_GuardDecision",
    "_BudgetLimit",
    "_PolicyComponents",
]
