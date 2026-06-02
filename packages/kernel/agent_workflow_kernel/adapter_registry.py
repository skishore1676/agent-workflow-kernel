"""Adapter registry for the generic workflow kernel facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .adapters import LaneAdapter, RuntimeAdapter, SurfaceAdapter
from .contracts import AdapterFamily, RiskClass, StageType


class AdapterRegistryError(ValueError):
    """Raised when a workflow stage cannot be safely mapped to an adapter."""


@dataclass(frozen=True, slots=True)
class AdapterRegistration:
    """Portable adapter metadata used before invocation."""

    adapter_id: str
    family: AdapterFamily
    adapter: Any
    operations: tuple[str, ...]
    side_effects: tuple[RiskClass, ...] = (RiskClass.READ_ONLY,)
    replay_safe: bool = False
    requires_idempotency_key: bool = True
    default_timeout_seconds: int | None = None
    proof_capabilities: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_runtime_adapter(
        cls,
        adapter: RuntimeAdapter,
        *,
        side_effects: tuple[RiskClass, ...] = (RiskClass.READ_ONLY,),
        replay_safe: bool = False,
    ) -> "AdapterRegistration":
        capabilities = adapter.capabilities()
        return cls(
            adapter_id=capabilities.adapter_id,
            family=capabilities.family,
            adapter=adapter,
            operations=capabilities.operations,
            side_effects=side_effects,
            replay_safe=replay_safe,
            metadata=dict(capabilities.metadata),
        )

    @classmethod
    def from_surface_adapter(
        cls,
        adapter: SurfaceAdapter,
        *,
        side_effects: tuple[RiskClass, ...] = (RiskClass.INTERNAL_STATE,),
        replay_safe: bool = False,
    ) -> "AdapterRegistration":
        capabilities = adapter.capabilities()
        return cls(
            adapter_id=capabilities.adapter_id,
            family=capabilities.family,
            adapter=adapter,
            operations=capabilities.operations,
            side_effects=side_effects,
            replay_safe=replay_safe,
            metadata=dict(capabilities.metadata),
        )

    @classmethod
    def from_lane_adapter(
        cls,
        adapter: LaneAdapter,
        *,
        side_effects: tuple[RiskClass, ...] = (RiskClass.READ_ONLY,),
        replay_safe: bool = True,
    ) -> "AdapterRegistration":
        capabilities = adapter.capabilities()
        return cls(
            adapter_id=capabilities.adapter_id,
            family=capabilities.family,
            adapter=adapter,
            operations=capabilities.operations,
            side_effects=side_effects,
            replay_safe=replay_safe,
            metadata=dict(capabilities.metadata),
        )

    def supports(self, operation: str) -> bool:
        return operation in self.operations


class AdapterRegistry:
    """In-memory registry keyed by logical adapter id."""

    def __init__(self, registrations: tuple[AdapterRegistration, ...] = ()) -> None:
        self._registrations: dict[str, AdapterRegistration] = {}
        for registration in registrations:
            self.register(registration)

    def register(self, registration: AdapterRegistration) -> None:
        if registration.adapter_id in self._registrations:
            raise AdapterRegistryError(f"duplicate adapter registration: {registration.adapter_id}")
        self._registrations[registration.adapter_id] = registration

    def resolve(
        self,
        adapter_ref: str,
        *,
        stage_type: StageType,
    ) -> AdapterRegistration:
        registration = self._registrations.get(adapter_ref)
        if registration is None:
            raise AdapterRegistryError(f"missing adapter registration: {adapter_ref}")
        expected_family = adapter_family_for_stage(stage_type, adapter_ref)
        if registration.family != expected_family:
            raise AdapterRegistryError(
                "adapter family mismatch: "
                f"stage expects {expected_family.value}, registration is {registration.family.value}"
            )
        return registration

    def validate_ref(self, adapter_ref: str) -> None:
        if adapter_ref not in self._registrations:
            raise AdapterRegistryError(f"missing adapter registration: {adapter_ref}")


def adapter_family_for_stage(stage_type: StageType, adapter_ref: str) -> AdapterFamily:
    if stage_type == StageType.HUMAN_GATE or adapter_ref.startswith("surface."):
        return AdapterFamily.SURFACE
    if adapter_ref.startswith("host."):
        return AdapterFamily.HOST
    if adapter_ref.startswith("runtime.") or stage_type in {
        StageType.AGENT_WORK,
        StageType.AGENT_GATE,
        StageType.A2A_REVIEW_LOOP,
    }:
        return AdapterFamily.RUNTIME
    return AdapterFamily.LANE


__all__ = [
    "AdapterRegistration",
    "AdapterRegistry",
    "AdapterRegistryError",
    "adapter_family_for_stage",
]
