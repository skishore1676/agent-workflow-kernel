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


# --------------------------------------------------------------------------- #
# Session budget: a hard turn/token/time cap for a bounded conversational
# session (ladder rung 3). This GENERALIZES the stage-level budget guards
# (within_ping_pong_budget / within_research_iteration_budget in kernel.py),
# which bound a *stage's* re-entries, to bound a *conversation's* turn count,
# token spend, and wall-clock — keyed off a single mapping the way a stage
# `budget:` dict is. It carries the same fail-closed integer semantics: a
# non-integer, boolean, or negative limit is rejected (a careless config can
# never disable the cap), and a missing limit means "no cap on that axis"
# (you opt INTO a cap, you never silently lose one you set). The companion
# runner consumes this to enforce the rung-3 contract; the cap-hit is a
# resumable pause, not a crash.
# --------------------------------------------------------------------------- #


_SESSION_BUDGET_KEYS: Mapping[str, tuple[str, ...]] = {
    "max_turns": ("max_turns", "max_turn", "turn_cap"),
    "max_total_tokens": ("max_total_tokens", "max_tokens", "token_cap"),
    "max_wall_seconds": ("max_wall_seconds", "max_seconds", "time_cap_seconds"),
}


def _coerce_session_limit(value: Any, *, label: str) -> int | None:
    """A non-negative integer cap, or None for "no cap on this axis".

    Fail closed on a careless value: a boolean or non-integer is a config
    error (raises), NOT a silently-disabled cap. ``None``/missing means the
    axis is uncapped on purpose — the caller opted out of that one axis, it is
    not an accidental removal of a cap that was set."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"session budget {label!r} must be an integer, not boolean")
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"session budget {label!r} must be an integer") from exc
    if limit < 0:
        raise ValueError(f"session budget {label!r} must be non-negative")
    return limit


@dataclass(frozen=True, slots=True)
class SessionBudget:
    """Hard caps for one bounded conversational session.

    Any axis left ``None`` is uncapped (opt-in caps). The session runner checks
    ``cap_hit(...)`` BEFORE producing each turn, so a cap stops the loop at a
    turn boundary (never mid-turn) and the stop is resumable. ``zero`` axes are
    legal and immediately cap (a 0-turn session is a no-op that still records a
    ledger row) — but a *missing* cap is uncapped, a *negative* cap is rejected.
    """

    max_turns: int | None = None
    max_total_tokens: int | None = None
    max_wall_seconds: int | None = None

    def __post_init__(self) -> None:
        # Re-validate via the coercer so a directly-constructed budget obeys the
        # same fail-closed rule as one parsed from config.
        object.__setattr__(self, "max_turns", _coerce_session_limit(self.max_turns, label="max_turns"))
        object.__setattr__(
            self, "max_total_tokens", _coerce_session_limit(self.max_total_tokens, label="max_total_tokens")
        )
        object.__setattr__(
            self, "max_wall_seconds", _coerce_session_limit(self.max_wall_seconds, label="max_wall_seconds")
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "SessionBudget":
        """Parse a lane ``budget:`` mapping into a SessionBudget, accepting the
        same key aliases the stage budget guards accept. Unknown keys are
        ignored (forward-compatible); a present-but-bad value fails closed."""
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("session budget must be a mapping")
        resolved: dict[str, int | None] = {}
        for canonical, aliases in _SESSION_BUDGET_KEYS.items():
            picked: Any = None
            for alias in aliases:
                if alias in value:
                    picked = value[alias]
                    break
            resolved[canonical] = picked
        return cls(
            max_turns=resolved["max_turns"],
            max_total_tokens=resolved["max_total_tokens"],
            max_wall_seconds=resolved["max_wall_seconds"],
        )

    def is_capped(self) -> bool:
        """True if at least one axis is bounded. A fully-unbounded budget is a
        rung-4 escape hatch; the companion runner refuses to start on one."""
        return any(
            limit is not None
            for limit in (self.max_turns, self.max_total_tokens, self.max_wall_seconds)
        )

    def has_hard_backstop(self) -> bool:
        """True only if an axis caps the loop REGARDLESS of provider behavior.

        ``is_capped()`` is satisfied by a token-only budget, but tokens are not a
        backstop: a provider that reports zero/missing ``usage.total_tokens`` (a
        stub, a failure mode, a misconfigured adapter) never advances the token
        total, so a never-wrapping persona on a token-only cap loops FOREVER
        (Codex verified at 0 tokens). The only axes immune to provider usage are
        the turn count (always advances one per produced turn) and wall-clock
        (always advances with real time). At least one of those MUST bound the
        session or it is not genuinely capped."""
        return self.max_turns is not None or self.max_wall_seconds is not None

    def with_hard_backstop(self, *, default_max_turns: int) -> "SessionBudget":
        """Return a budget guaranteed to have a usage-independent hard cap.

        If this budget already has a turn or wall-clock cap it is returned
        unchanged. Otherwise (e.g. a token-only budget) a ``max_turns`` backstop
        is layered on so NO configuration can produce an unbounded loop. The
        existing token/wall axes are preserved as secondary budgets."""
        if self.has_hard_backstop():
            return self
        if not isinstance(default_max_turns, int) or isinstance(default_max_turns, bool):
            raise ValueError("default_max_turns must be an integer")
        if default_max_turns <= 0:
            raise ValueError("default_max_turns backstop must be a positive integer")
        return SessionBudget(
            max_turns=default_max_turns,
            max_total_tokens=self.max_total_tokens,
            max_wall_seconds=self.max_wall_seconds,
        )

    def cap_hit(
        self, *, turns: int, total_tokens: int, elapsed_seconds: float
    ) -> str | None:
        """Return the name of the FIRST axis whose cap is reached/exceeded, or
        None if the session may produce another turn. Checked at a turn
        boundary. ``turns`` is the count already produced (so ``turns >=
        max_turns`` stops before producing the (max_turns+1)-th)."""
        if self.max_turns is not None and turns >= self.max_turns:
            return "max_turns"
        if self.max_total_tokens is not None and total_tokens >= self.max_total_tokens:
            return "max_total_tokens"
        if self.max_wall_seconds is not None and elapsed_seconds >= self.max_wall_seconds:
            return "max_wall_seconds"
        return None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "max_turns": self.max_turns,
            "max_total_tokens": self.max_total_tokens,
            "max_wall_seconds": self.max_wall_seconds,
        }


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
