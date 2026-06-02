"""OpenClaw reference-host mapping helpers.

The objects in this module are compatibility data only. They describe how a
local parity fixture refers to OpenClaw concepts without resolving paths,
contacting the runtime, or teaching the portable kernel about OpenClaw names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from agent_workflow_kernel.adapters import RuntimeRef, SurfaceRef
from agent_workflow_kernel.contracts import to_plain_data
from agent_workflow_kernel.prompts import digest_data


OPENCLAW_IDENTITY_CROSSWALK_SCHEMA = "openclaw.awk_identity_crosswalk.v1"


@dataclass(slots=True, frozen=True)
class OpenClawWorkLedgerIds:
    """Work Ledger-compatible identifiers carried by read-only fixtures."""

    work_item_id: str | None = None
    handoff_id: str | None = None
    receipt_ids: tuple[str, ...] = ()
    interaction_id: str | None = None
    turn_id: str | None = None


@dataclass(slots=True, frozen=True)
class OpenClawReferenceMapping:
    """Reference-host mapping for an OpenClaw lane/agent fixture."""

    lane_id: str
    agent_id: str
    work_ledger_ids: OpenClawWorkLedgerIds = field(default_factory=OpenClawWorkLedgerIds)
    surface_refs: tuple[SurfaceRef, ...] = ()
    runtime_refs: tuple[RuntimeRef, ...] = ()
    host_ref: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        """Return plain JSON-compatible mapping metadata for receipts."""

        return to_plain_data(
            {
                "lane_id": self.lane_id,
                "agent_id": self.agent_id,
                "work_ledger_ids": self.work_ledger_ids,
                "surface_refs": self.surface_refs,
                "runtime_refs": self.runtime_refs,
                "host_ref": self.host_ref,
            }
        )


@dataclass(slots=True, frozen=True)
class OpenClawIdentityCrosswalk:
    """Durable adapter-side identity bridge between AWK and OpenClaw state."""

    crosswalk_id: str
    awk_instance_id: str
    workflow_id: str
    workflow_version: str
    openclaw_artifact_id: str
    current_stage_id: str | None = None
    terminal_stage_id: str | None = None
    lane_id: str | None = None
    openclaw_artifact_record_path: str | None = None
    handoff_path: str | None = None
    runner_receipt_path: str | None = None
    work_ledger_id: str | None = None
    work_id: str | None = None
    work_item_id: str | None = None
    work_ledger_handoff_id: str | None = None
    work_ledger_receipt_id: str | None = None
    source_hashes: Mapping[str, str | None] = field(default_factory=dict)
    terminal_event_id: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        """Return JSON-compatible crosswalk metadata for receipts and events."""

        return to_plain_data(
            {
                "schema": OPENCLAW_IDENTITY_CROSSWALK_SCHEMA,
                "crosswalk_id": self.crosswalk_id,
                "awk_instance_id": self.awk_instance_id,
                "workflow_id": self.workflow_id,
                "workflow_version": self.workflow_version,
                "openclaw_artifact_id": self.openclaw_artifact_id,
                "current_stage_id": self.current_stage_id,
                "terminal_stage_id": self.terminal_stage_id,
                "lane_id": self.lane_id,
                "openclaw_artifact_record_path": self.openclaw_artifact_record_path,
                "handoff_path": self.handoff_path,
                "runner_receipt_path": self.runner_receipt_path,
                "work_ledger_id": self.work_ledger_id,
                "work_id": self.work_id,
                "work_item_id": self.work_item_id,
                "work_ledger_handoff_id": self.work_ledger_handoff_id,
                "work_ledger_receipt_id": self.work_ledger_receipt_id,
                "source_hashes": dict(self.source_hashes),
                "terminal_event_id": self.terminal_event_id,
            }
        )

    def fingerprint(self) -> str:
        return digest_data(self.to_metadata())


IMMUTABLE_CROSSWALK_IDENTITY_FIELDS = (
    "awk_instance_id",
    "workflow_id",
    "workflow_version",
    "openclaw_artifact_id",
    "lane_id",
    "handoff_path",
    "runner_receipt_path",
    "work_ledger_id",
    "work_id",
    "work_item_id",
    "work_ledger_handoff_id",
    "work_ledger_receipt_id",
)


def openclaw_identity_crosswalk_conflicts(
    existing: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Return immutable identity fields that conflict between two crosswalks."""

    conflicts: list[dict[str, Any]] = []
    for field_name in IMMUTABLE_CROSSWALK_IDENTITY_FIELDS:
        existing_value = existing.get(field_name)
        candidate_value = candidate.get(field_name)
        if existing_value in (None, "") or candidate_value in (None, ""):
            continue
        if str(existing_value) != str(candidate_value):
            conflicts.append(
                {
                    "field": field_name,
                    "existing": existing_value,
                    "candidate": candidate_value,
                }
            )
    return tuple(conflicts)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _required_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"OpenClaw mapping fixture requires non-empty {key!r}")
    return value


def work_ledger_ids_from_fixture(data: Mapping[str, Any] | None) -> OpenClawWorkLedgerIds:
    """Build Work Ledger-compatible ids from fixture data."""

    if data is None:
        data = {}
    return OpenClawWorkLedgerIds(
        work_item_id=data.get("work_item_id"),
        handoff_id=data.get("handoff_id"),
        receipt_ids=_string_tuple(data.get("receipt_ids")),
        interaction_id=data.get("interaction_id"),
        turn_id=data.get("turn_id"),
    )


def surface_refs_from_fixture(items: Any) -> tuple[SurfaceRef, ...]:
    """Convert fixture surface references into kernel surface refs."""

    if not items:
        return ()
    refs: list[SurfaceRef] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("surface_refs entries must be mappings")
        refs.append(
            SurfaceRef(
                surface_id=_required_string(item, "surface_id"),
                kind=_required_string(item, "kind"),
                external_id=item.get("external_id"),
                title=item.get("title"),
                readback_required=bool(item.get("readback_required", False)),
                status=str(item.get("status", "observed")),
            )
        )
    return tuple(refs)


def runtime_refs_from_fixture(items: Any) -> tuple[RuntimeRef, ...]:
    """Convert fixture runtime references into kernel runtime refs."""

    if not items:
        return ()
    refs: list[RuntimeRef] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("runtime_refs entries must be mappings")
        refs.append(
            RuntimeRef(
                runtime_id=_required_string(item, "runtime_id"),
                kind=_required_string(item, "kind"),
                external_id=item.get("external_id"),
                host_ref=item.get("host_ref"),
                redacted_locator=item.get("redacted_locator"),
                status=str(item.get("status", "observed")),
            )
        )
    return tuple(refs)


def mapping_from_fixture(fixture: Mapping[str, Any]) -> OpenClawReferenceMapping:
    """Build a reference mapping from a local OpenClaw parity fixture."""

    data = fixture.get("mapping", fixture)
    if not isinstance(data, Mapping):
        raise ValueError("OpenClaw fixture mapping must be a mapping")

    return OpenClawReferenceMapping(
        lane_id=_required_string(data, "lane_id"),
        agent_id=_required_string(data, "agent_id"),
        work_ledger_ids=work_ledger_ids_from_fixture(data.get("work_ledger")),
        surface_refs=surface_refs_from_fixture(data.get("surface_refs")),
        runtime_refs=runtime_refs_from_fixture(data.get("runtime_refs")),
        host_ref=data.get("host_ref"),
    )
