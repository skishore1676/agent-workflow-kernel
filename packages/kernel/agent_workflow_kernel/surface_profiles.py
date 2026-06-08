"""Semantic surface profile resolution for host-specific presentation adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:  # pragma: no cover - exercised in normal environments with PyYAML.
    import yaml
except ImportError:  # pragma: no cover - JSON fallback keeps the module importable.
    yaml = None

from .adapter_registry import AdapterRegistration, AdapterRegistry, AdapterRegistryError
from .contracts import AdapterFamily, StageType, to_plain_data


SURFACE_PROFILE_SCHEMA = "surface.profile.v1"


class SurfaceProfileError(ValueError):
    """Raised when a semantic surface binding cannot be resolved safely."""


@dataclass(slots=True, frozen=True)
class SurfaceBinding:
    """Host mapping from a semantic surface ref to a concrete adapter id."""

    semantic_ref: str
    adapter_id: str
    surface_kind: str | None = None
    mode: str | None = None
    required_operations: tuple[str, ...] = ("publish", "readback", "ingest_decisions")
    fallback_adapter_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        return to_plain_data(self)


@dataclass(slots=True, frozen=True)
class ResolvedSurfaceBinding:
    """Concrete surface registration chosen for one semantic surface ref."""

    binding: SurfaceBinding
    registration: AdapterRegistration
    fallback_registrations: tuple[AdapterRegistration, ...] = ()

    @property
    def semantic_ref(self) -> str:
        return self.binding.semantic_ref

    @property
    def adapter_id(self) -> str:
        return self.registration.adapter_id

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema": SURFACE_PROFILE_SCHEMA,
            "semantic_ref": self.semantic_ref,
            "adapter_id": self.adapter_id,
            "fallback_adapter_ids": [registration.adapter_id for registration in self.fallback_registrations],
            "binding": self.binding.to_metadata(),
            "registration": {
                "adapter_id": self.registration.adapter_id,
                "family": self.registration.family.value,
                "operations": list(self.registration.operations),
                "side_effects": [risk.value for risk in self.registration.side_effects],
                "metadata": to_plain_data(self.registration.metadata),
            },
        }


@dataclass(slots=True, frozen=True)
class SurfaceProfile:
    """Named host profile for semantic-to-concrete surface adapter bindings."""

    profile_id: str
    bindings: tuple[SurfaceBinding, ...]
    schema: str = SURFACE_PROFILE_SCHEMA
    description: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def binding_for(self, semantic_ref: str) -> SurfaceBinding:
        for binding in self.bindings:
            if binding.semantic_ref == semantic_ref:
                return binding
        raise SurfaceProfileError(f"missing surface binding for semantic ref: {semantic_ref}")

    def resolve(
        self,
        semantic_ref: str,
        registry: AdapterRegistry,
        *,
        required_operations: tuple[str, ...] | None = None,
    ) -> ResolvedSurfaceBinding:
        binding = self.binding_for(semantic_ref)
        operations = required_operations or binding.required_operations
        registration = _resolve_surface_registration(registry, binding.adapter_id, operations)
        fallbacks = tuple(
            _resolve_surface_registration(registry, adapter_id, operations)
            for adapter_id in binding.fallback_adapter_ids
        )
        return ResolvedSurfaceBinding(
            binding=binding,
            registration=registration,
            fallback_registrations=fallbacks,
        )

    def validate(self, registry: AdapterRegistry) -> tuple[ResolvedSurfaceBinding, ...]:
        return tuple(self.resolve(binding.semantic_ref, registry) for binding in self.bindings)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "profile_id": self.profile_id,
            "description": self.description,
            "bindings": [binding.to_metadata() for binding in self.bindings],
            "metadata": to_plain_data(self.metadata),
        }


def load_surface_profile(path: str | Path) -> SurfaceProfile:
    """Load a surface profile from YAML or JSON."""

    profile_path = Path(path)
    raw_text = profile_path.read_text(encoding="utf-8")
    if profile_path.suffix.lower() in {".yaml", ".yml"} and yaml is not None:
        data = yaml.safe_load(raw_text)
    else:
        data = json.loads(raw_text)
    return surface_profile_from_mapping(data)


def surface_profile_from_mapping(data: Mapping[str, Any]) -> SurfaceProfile:
    schema = str(data.get("schema") or SURFACE_PROFILE_SCHEMA)
    if schema != SURFACE_PROFILE_SCHEMA:
        raise SurfaceProfileError(f"unsupported surface profile schema: {schema}")
    profile = data.get("profile")
    profile_data = profile if isinstance(profile, Mapping) else data
    profile_id = str(profile_data.get("id") or profile_data.get("profile_id") or "").strip()
    if not profile_id:
        raise SurfaceProfileError("surface profile requires profile.id or profile_id")
    bindings_value = profile_data.get("bindings") or data.get("bindings")
    if not isinstance(bindings_value, list | tuple):
        raise SurfaceProfileError("surface profile requires a bindings list")
    return SurfaceProfile(
        profile_id=profile_id,
        schema=schema,
        description=_optional_string(profile_data.get("description")),
        bindings=tuple(_surface_binding_from_mapping(item) for item in bindings_value),
        metadata=dict(profile_data.get("metadata") or {}),
    )


def _surface_binding_from_mapping(data: Any) -> SurfaceBinding:
    if not isinstance(data, Mapping):
        raise SurfaceProfileError("surface binding must be a mapping")
    semantic_ref = str(data.get("semantic_ref") or data.get("ref") or "").strip()
    adapter_id = str(data.get("adapter_id") or data.get("adapter") or "").strip()
    if not semantic_ref:
        raise SurfaceProfileError("surface binding requires semantic_ref")
    if not adapter_id:
        raise SurfaceProfileError(f"surface binding {semantic_ref!r} requires adapter_id")
    return SurfaceBinding(
        semantic_ref=semantic_ref,
        adapter_id=adapter_id,
        surface_kind=_optional_string(data.get("surface_kind")),
        mode=_optional_string(data.get("mode")),
        required_operations=_string_tuple(data.get("required_operations")) or (
            "publish",
            "readback",
            "ingest_decisions",
        ),
        fallback_adapter_ids=_string_tuple(data.get("fallback_adapter_ids")),
        metadata=dict(data.get("metadata") or {}),
    )


def _resolve_surface_registration(
    registry: AdapterRegistry,
    adapter_id: str,
    required_operations: tuple[str, ...],
) -> AdapterRegistration:
    try:
        registration = registry.resolve(adapter_id, stage_type=StageType.HUMAN_GATE)
    except AdapterRegistryError as exc:
        raise SurfaceProfileError(str(exc)) from exc
    if registration.family != AdapterFamily.SURFACE:
        raise SurfaceProfileError(f"surface binding resolved to non-surface adapter: {adapter_id}")
    missing = tuple(operation for operation in required_operations if not registration.supports(operation))
    if missing:
        raise SurfaceProfileError(
            f"surface adapter {adapter_id!r} is missing required operations: {', '.join(missing)}"
        )
    return registration


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    return ()


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None
