"""Durable actor-session identity helpers.

This module intentionally covers only portable key construction. Concrete
runtime sessions, storage rows, reattachment, and cancellation stay with runner
and adapter implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from .contracts import to_plain_data


ACTOR_SESSION_KEY_SCHEMA_VERSION = "actor-session-key.v1"
ACTOR_SESSION_KEY_PREFIX = "ask:v1:"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ActorSessionScope(StrEnum):
    """Portable reuse scope for an actor runtime session."""

    WORKFLOW_INSTANCE = "workflow_instance"
    PROGRAM_INSTANCE = "program_instance"


@dataclass(frozen=True, slots=True)
class ActorSessionBinding:
    """Stable fields that determine whether an actor session may be reused."""

    scope: ActorSessionScope | str
    scope_id: str
    actor_ref: str
    adapter_id: str
    profile_binding_digest: str
    workflow_id: str
    workflow_version: str | None = None
    runtime_namespace: str = "default"
    program_id: str | None = None


def digest_actor_session_profile(profile: Mapping[str, Any]) -> str:
    """Digest the standing actor/profile inputs that bind a session."""

    return _digest_data(
        {
            "schema_version": "actor-session-profile.v1",
            "profile": profile,
        }
    )


def canonical_actor_session_binding(binding: ActorSessionBinding | Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical key payload for a session binding.

    The returned mapping is suitable for receipts, audits, and deterministic key
    generation. Unknown fields are ignored so callers cannot accidentally make
    host-local metadata part of the portable key.
    """

    raw = to_plain_data(binding)
    if not isinstance(raw, Mapping):
        raise TypeError("actor session binding must be a dataclass or mapping")
    scope = str(raw.get("scope") or "")
    if scope not in {item.value for item in ActorSessionScope}:
        allowed = ", ".join(item.value for item in ActorSessionScope)
        raise ValueError(f"scope must be one of: {allowed}")

    data = {
        "schema_version": ACTOR_SESSION_KEY_SCHEMA_VERSION,
        "scope": scope,
        "scope_id": _required_text(raw.get("scope_id"), "scope_id"),
        "workflow_id": _required_text(raw.get("workflow_id"), "workflow_id"),
        "workflow_version": _optional_text(raw.get("workflow_version")),
        "program_id": _optional_text(raw.get("program_id")),
        "actor_ref": _required_text(raw.get("actor_ref"), "actor_ref"),
        "adapter_id": _required_text(raw.get("adapter_id"), "adapter_id"),
        "runtime_namespace": _optional_text(raw.get("runtime_namespace")) or "default",
        "profile_binding_digest": _required_digest(
            raw.get("profile_binding_digest"),
            "profile_binding_digest",
        ),
    }
    if scope == ActorSessionScope.WORKFLOW_INSTANCE.value and not data["workflow_version"]:
        raise ValueError("workflow_instance actor sessions require workflow_version")
    if scope == ActorSessionScope.PROGRAM_INSTANCE.value and not data["program_id"]:
        raise ValueError("program_instance actor sessions require program_id")
    return _canonicalize_data(data)


def canonical_actor_session_key(binding: ActorSessionBinding | Mapping[str, Any]) -> str:
    """Build the canonical durable actor-session key.

    The opaque digest keeps the key stable for storage indexes while the
    canonical binding remains the human-readable receipt payload.
    """

    return ACTOR_SESSION_KEY_PREFIX + _digest_data(
        canonical_actor_session_binding(binding)
    ).removeprefix("sha256:")


def _required_text(value: Any, label: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _required_digest(value: Any, label: str) -> str:
    text = _required_text(value, label)
    if not _SHA256_RE.match(text):
        raise ValueError(f"{label} must be a sha256:<64-hex> digest")
    return text


def _digest_data(value: Any) -> str:
    return "sha256:" + sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonicalize_data(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonicalize_data(value: Any) -> Any:
    plain = to_plain_data(value)
    if isinstance(plain, Mapping):
        return {str(key): _canonicalize_data(plain[key]) for key in sorted(plain, key=str)}
    if isinstance(plain, list):
        return [_canonicalize_data(item) for item in plain]
    return plain
