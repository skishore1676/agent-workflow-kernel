"""Jarvis weekly update shadow-adoption helpers.

This module maps a supplied OpenClaw/Northstar weekly update fixture into AWK
stage observations and receipts. It never reads or writes Obsidian, Telegram,
oldmac, or OpenClaw runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any, Mapping

from agent_workflow_kernel import (
    ADAPTER_STATUS_SUCCEEDED,
    make_adapter_receipt,
)
from agent_workflow_kernel import (
    AdapterFamily,
    AdapterInvocation,
    Receipt,
    to_plain_data,
)
from agent_workflow_kernel import digest_data


WEEKLY_UPDATE_FIXTURE_SCHEMA = "openclaw.weekly-update-fixture.v1"
WEEKLY_UPDATE_ADOPTION_REPORT_SCHEMA = "openclaw.weekly-update-adoption-report.v1"
DEFAULT_WORKFLOW_ID = "jarvis_weekly_update_shadow"
DEFAULT_INSTANCE_ID = "instance:jarvis-weekly-update-shadow"
DEFAULT_CREATED_AT = "2000-01-01T00:00:00Z"

DISCOVERY_STAGE = "discover_weekly_artifact"
READBACK_STAGE = "readback_blackboard_card"
HUMAN_GATE_STAGE = "suman_review_gate"
ROUTING_STAGE = "route_follow_up"


@dataclass(slots=True, frozen=True)
class WeeklyUpdateFixture:
    """Normalized weekly-update source data from a local fixture."""

    fixture_id: str
    mode: str
    note_path: str
    item_id: str
    source_artifact: str
    blackboard_bucket: str
    owner: str
    evidence_link: str
    checked: bool
    read_state: str
    created_at: str
    title: str | None = None
    from_agent: str | None = None
    bottom_line: str | None = None
    pattern: str | None = None
    move: str | None = None
    follow_up: Mapping[str, Any] | None = None
    residual_risk: str | None = None

    def source_summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "note_path": self.note_path,
            "item_id": self.item_id,
            "source_artifact": self.source_artifact,
            "blackboard_bucket": self.blackboard_bucket,
            "owner": self.owner,
            "evidence_link": self.evidence_link,
            "checked": self.checked,
            "read_state": self.read_state,
            "from_agent": self.from_agent,
        }


@dataclass(slots=True, frozen=True)
class WeeklyUpdateStageObservation:
    """One deterministic AWK stage observation derived from a fixture."""

    stage_id: str
    stage_type: str
    adapter: str
    outcome: str
    status: str
    summary: str
    receipt_id: str
    metadata: Mapping[str, Any]

    def to_data(self) -> dict[str, Any]:
        return to_plain_data(self)


@dataclass(slots=True, frozen=True)
class WeeklyUpdateAdoptionReport:
    """Deterministic shadow report for a weekly update fixture."""

    schema: str
    report_id: str
    fixture_id: str
    workflow_id: str
    status: str
    current_stage_id: str | None
    terminal_status: str | None
    observations: tuple[WeeklyUpdateStageObservation, ...]
    receipts: tuple[Receipt, ...]
    checks: tuple[str, ...]
    residual_risk: str | None
    next_action: str | None

    def to_data(self) -> dict[str, Any]:
        return to_plain_data(self)


def load_weekly_update_fixture(path: str | Path) -> dict[str, Any]:
    """Load a weekly update JSON fixture."""

    fixture_path = Path(path)
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def weekly_update_from_fixture(fixture: Mapping[str, Any]) -> WeeklyUpdateFixture:
    """Normalize a fixture mapping into a typed weekly update shape."""

    if fixture.get("schema") != WEEKLY_UPDATE_FIXTURE_SCHEMA:
        fixture = _normalize_exported_weekly_fixture(fixture)
        if fixture.get("schema") != WEEKLY_UPDATE_FIXTURE_SCHEMA:
            raise ValueError(f"Unsupported weekly update fixture schema: {fixture.get('schema')}")
    source = _require_mapping(fixture.get("weekly_update"), "weekly_update")
    read_state = _optional_mapping(fixture.get("read_state"))
    details = _optional_mapping(source.get("details"))

    return WeeklyUpdateFixture(
        fixture_id=_required_string(fixture, "fixture_id"),
        mode=_required_string(source, "mode"),
        note_path=_required_string(source, "note_path"),
        item_id=_required_string(source, "item_id"),
        source_artifact=_required_string(source, "source_artifact"),
        blackboard_bucket=_required_string(source, "blackboard_bucket"),
        owner=_required_string(source, "owner"),
        evidence_link=_required_string(source, "evidence_link"),
        checked=bool(read_state.get("checked", source.get("checked", False))),
        read_state=str(read_state.get("state", "checked" if source.get("checked") else "unread")),
        created_at=str(fixture.get("created_at", DEFAULT_CREATED_AT)),
        title=_optional_string(source.get("title")),
        from_agent=_optional_string(source.get("from_agent")),
        bottom_line=_optional_string(details.get("bottom_line")),
        pattern=_optional_string(details.get("pattern")),
        move=_optional_string(details.get("move")),
        follow_up=_optional_mapping(source.get("follow_up")),
        residual_risk=_optional_string(fixture.get("residual_risk")),
    )


def weekly_mode(fixture: Mapping[str, Any] | WeeklyUpdateFixture) -> str:
    return _as_weekly_update(fixture).mode


def weekly_note_path(fixture: Mapping[str, Any] | WeeklyUpdateFixture) -> str:
    return _as_weekly_update(fixture).note_path


def weekly_item_id(fixture: Mapping[str, Any] | WeeklyUpdateFixture) -> str:
    return _as_weekly_update(fixture).item_id


def weekly_source_artifact(fixture: Mapping[str, Any] | WeeklyUpdateFixture) -> str:
    return _as_weekly_update(fixture).source_artifact


def weekly_blackboard_bucket(fixture: Mapping[str, Any] | WeeklyUpdateFixture) -> str:
    return _as_weekly_update(fixture).blackboard_bucket


def weekly_owner(fixture: Mapping[str, Any] | WeeklyUpdateFixture) -> str:
    return _as_weekly_update(fixture).owner


def weekly_evidence_link(fixture: Mapping[str, Any] | WeeklyUpdateFixture) -> str:
    return _as_weekly_update(fixture).evidence_link


def weekly_checked_state(fixture: Mapping[str, Any] | WeeklyUpdateFixture) -> dict[str, Any]:
    weekly = _as_weekly_update(fixture)
    return {"checked": weekly.checked, "state": weekly.read_state}


def observations_from_weekly_update(
    fixture: Mapping[str, Any] | WeeklyUpdateFixture,
    *,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    instance_id: str = DEFAULT_INSTANCE_ID,
) -> tuple[WeeklyUpdateStageObservation, ...]:
    """Convert a weekly update fixture into deterministic stage observations."""

    weekly = _as_weekly_update(fixture)
    observations: list[WeeklyUpdateStageObservation] = []

    discovery_receipt = _receipt(
        weekly,
        workflow_id=workflow_id,
        instance_id=instance_id,
        stage_id=DISCOVERY_STAGE,
        adapter_family=AdapterFamily.LANE,
        adapter_id="lane.openclaw.weekly_update_discovery",
        operation="inspect_weekly_update_fixture",
        status=ADAPTER_STATUS_SUCCEEDED,
        summary="Weekly Suman/Jarvis update artifact was discovered from a supplied fixture.",
        outcome="found",
        outputs={
            "mode": weekly.mode,
            "note_path": weekly.note_path,
            "source_artifact": weekly.source_artifact,
        },
    )
    observations.append(
        _observation(
            stage_id=DISCOVERY_STAGE,
            stage_type="system_action",
            adapter="lane.openclaw.weekly_update_discovery",
            outcome="found",
            status=discovery_receipt.status,
            summary=discovery_receipt.summary,
            receipt=discovery_receipt,
            metadata={
                "mode": weekly.mode,
                "note_path": weekly.note_path,
                "source_artifact": weekly.source_artifact,
            },
        )
    )

    readback_outcome = "read_clear" if weekly.checked else "needs_review"
    readback_receipt = _receipt(
        weekly,
        workflow_id=workflow_id,
        instance_id=instance_id,
        stage_id=READBACK_STAGE,
        adapter_family=AdapterFamily.SURFACE,
        adapter_id="surface.openclaw.blackboard_readback",
        operation="readback_weekly_update_card",
        status=ADAPTER_STATUS_SUCCEEDED,
        summary=(
            "Blackboard reference card is already checked/read."
            if weekly.checked
            else "Blackboard reference card is present and awaits Suman read/clear."
        ),
        outcome=readback_outcome,
        outputs={
            "item_id": weekly.item_id,
            "blackboard_bucket": weekly.blackboard_bucket,
            "owner": weekly.owner,
            "evidence_link": weekly.evidence_link,
            "checked": weekly.checked,
            "read_state": weekly.read_state,
        },
    )
    observations.append(
        _observation(
            stage_id=READBACK_STAGE,
            stage_type="system_action",
            adapter="surface.openclaw.blackboard_readback",
            outcome=readback_outcome,
            status=readback_receipt.status,
            summary=readback_receipt.summary,
            receipt=readback_receipt,
            metadata={
                "item_id": weekly.item_id,
                "blackboard_bucket": weekly.blackboard_bucket,
                "owner": weekly.owner,
                "evidence_link": weekly.evidence_link,
                "checked": weekly.checked,
                "read_state": weekly.read_state,
            },
        )
    )

    if not weekly.checked:
        gate_receipt = _receipt(
            weekly,
            workflow_id=workflow_id,
            instance_id=instance_id,
            stage_id=HUMAN_GATE_STAGE,
            adapter_family=AdapterFamily.SURFACE,
            adapter_id="surface.human_review",
            operation="await_suman_weekly_read_clear",
            status="approval_required",
            summary="Human gate is explicit: Suman must read/clear or request follow-up.",
            outcome="waiting_for_suman",
            outputs={
                "allowed_decisions": ["read_clear", "follow_up_requested", "defer", "blocked"],
                "binds_to": f"receipts.{READBACK_STAGE}",
            },
            next_action="Wait for Suman to read/clear the weekly check-in or request follow-up.",
        )
        observations.append(
            _observation(
                stage_id=HUMAN_GATE_STAGE,
                stage_type="human_gate",
                adapter="surface.human_review",
                outcome="waiting_for_suman",
                status=gate_receipt.status,
                summary=gate_receipt.summary,
                receipt=gate_receipt,
                metadata={
                    "requires_explicit_approval": True,
                    "allowed_decisions": ["read_clear", "follow_up_requested", "defer", "blocked"],
                },
            )
        )
        return tuple(observations)

    route_outcome = _route_outcome(weekly.follow_up)
    route_receipt = _receipt(
        weekly,
        workflow_id=workflow_id,
        instance_id=instance_id,
        stage_id=ROUTING_STAGE,
        adapter_family=AdapterFamily.LANE,
        adapter_id="lane.openclaw.weekly_followup_router",
        operation="shadow_route_weekly_follow_up",
        status=ADAPTER_STATUS_SUCCEEDED,
        summary=(
            "Weekly check-in is read/clear with no follow-up to route."
            if route_outcome == "no_follow_up"
            else "Weekly check-in follow-up route was mapped in shadow mode."
        ),
        outcome=route_outcome,
        outputs={
            "follow_up": to_plain_data(weekly.follow_up or {}),
            "shadow_only": True,
        },
    )
    observations.append(
        _observation(
            stage_id=ROUTING_STAGE,
            stage_type="system_action",
            adapter="lane.openclaw.weekly_followup_router",
            outcome=route_outcome,
            status=route_receipt.status,
            summary=route_receipt.summary,
            receipt=route_receipt,
            metadata={
                "follow_up": to_plain_data(weekly.follow_up or {}),
                "shadow_only": True,
            },
        )
    )
    return tuple(observations)


def receipts_from_weekly_update(
    fixture: Mapping[str, Any] | WeeklyUpdateFixture,
    *,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    instance_id: str = DEFAULT_INSTANCE_ID,
) -> tuple[Receipt, ...]:
    """Return the receipts associated with the fixture-derived observations."""

    return tuple(
        _receipt_from_observation(observation, workflow_id=workflow_id, instance_id=instance_id)
        for observation in observations_from_weekly_update(
            fixture,
            workflow_id=workflow_id,
            instance_id=instance_id,
        )
    )


def adoption_report_from_fixture(
    fixture: Mapping[str, Any],
    *,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    instance_id: str = DEFAULT_INSTANCE_ID,
) -> WeeklyUpdateAdoptionReport:
    """Build a deterministic weekly update shadow adoption report."""

    weekly = weekly_update_from_fixture(fixture)
    observations = observations_from_weekly_update(
        weekly,
        workflow_id=workflow_id,
        instance_id=instance_id,
    )
    receipts = tuple(
        _receipt_from_observation(observation, workflow_id=workflow_id, instance_id=instance_id)
        for observation in observations
    )
    waiting = observations[-1].stage_id == HUMAN_GATE_STAGE and observations[-1].status == "approval_required"
    status = "waiting_on_human" if waiting else "done"
    current_stage_id = HUMAN_GATE_STAGE if waiting else None
    terminal_status = None if waiting else "done"
    next_action = observations[-1].metadata.get("next_action")
    if not isinstance(next_action, str):
        next_action = (
            "Wait for Suman to read/clear the weekly check-in or request follow-up."
            if waiting
            else "No follow-up remains for this shadow fixture."
        )
    report_seed = {
        "schema": WEEKLY_UPDATE_ADOPTION_REPORT_SCHEMA,
        "fixture_id": weekly.fixture_id,
        "workflow_id": workflow_id,
        "status": status,
        "observations": [observation.to_data() for observation in observations],
        "receipt_ids": [receipt.receipt_id for receipt in receipts],
    }
    return WeeklyUpdateAdoptionReport(
        schema=WEEKLY_UPDATE_ADOPTION_REPORT_SCHEMA,
        report_id=digest_data(report_seed),
        fixture_id=weekly.fixture_id,
        workflow_id=workflow_id,
        status=status,
        current_stage_id=current_stage_id,
        terminal_status=terminal_status,
        observations=observations,
        receipts=receipts,
        checks=(
            "fixture_schema_valid",
            "weekly_artifact_discovered",
            "blackboard_reference_readback_mapped",
            "human_gate_explicit",
            "shadow_only_no_external_writes",
        ),
        residual_risk=weekly.residual_risk or "Fixture data can drift from live OpenClaw and Northstar surfaces.",
        next_action=next_action,
    )


def _receipt_from_observation(
    observation: WeeklyUpdateStageObservation,
    *,
    workflow_id: str,
    instance_id: str,
) -> Receipt:
    metadata = dict(observation.metadata)
    weekly = WeeklyUpdateFixture(
        fixture_id=str(metadata.get("fixture_id", "weekly-update-fixture")),
        mode=str(metadata.get("mode", "weekly-personal")),
        note_path=str(metadata.get("note_path", "")),
        item_id=str(metadata.get("item_id", "")),
        source_artifact=str(metadata.get("source_artifact", "")),
        blackboard_bucket=str(metadata.get("blackboard_bucket", "")),
        owner=str(metadata.get("owner", "")),
        evidence_link=str(metadata.get("evidence_link", "")),
        checked=bool(metadata.get("checked", False)),
        read_state=str(metadata.get("read_state", "unknown")),
        created_at=str(metadata.get("created_at", DEFAULT_CREATED_AT)),
    )
    family = AdapterFamily.SURFACE if observation.adapter.startswith("surface.") else AdapterFamily.LANE
    return _receipt(
        weekly,
        workflow_id=workflow_id,
        instance_id=instance_id,
        stage_id=observation.stage_id,
        adapter_family=family,
        adapter_id=observation.adapter,
        operation=str(metadata.get("operation", observation.stage_id)),
        status=observation.status,
        summary=observation.summary,
        outcome=observation.outcome,
        outputs=metadata,
        next_action=str(metadata["next_action"]) if "next_action" in metadata else None,
    )


def _receipt(
    weekly: WeeklyUpdateFixture,
    *,
    workflow_id: str,
    instance_id: str,
    stage_id: str,
    adapter_family: AdapterFamily,
    adapter_id: str,
    operation: str,
    status: str,
    summary: str,
    outcome: str,
    outputs: Mapping[str, Any],
    next_action: str | None = None,
) -> Receipt:
    invocation = AdapterInvocation(
        invocation_id=f"invoke:{weekly.fixture_id}:{stage_id}",
        workflow_id=workflow_id,
        instance_id=instance_id,
        stage_run_id=f"{instance_id}:{stage_id}:1",
        adapter_family=adapter_family,
        adapter_id=adapter_id,
        operation=operation,
        input_ref=f"fixture:{weekly.fixture_id}",
        context_packet_ref=f"context:{weekly.fixture_id}:{stage_id}",
        idempotency_key=f"{weekly.fixture_id}:{stage_id}",
    )
    receipt_outputs = {
        "fixture_id": weekly.fixture_id,
        "outcome": outcome,
        "weekly_update": weekly.source_summary(),
        **dict(outputs),
    }
    return make_adapter_receipt(
        invocation,
        status=status,
        summary=summary,
        created_at=weekly.created_at,
        stage_id=stage_id,
        outputs=receipt_outputs,
        checks_run=(
            "fixture_supplied",
            "no_external_write",
            "blackboard_behavior_preserved",
        ),
        policy_snapshot={
            "risk_class": "read_only",
            "external_effects": False,
            "shadow_only": True,
        },
        residual_risk=weekly.residual_risk,
        next_action=next_action,
    )


def _observation(
    *,
    stage_id: str,
    stage_type: str,
    adapter: str,
    outcome: str,
    status: str,
    summary: str,
    receipt: Receipt,
    metadata: Mapping[str, Any],
) -> WeeklyUpdateStageObservation:
    receipt_outputs = receipt.runtime_provenance["outputs"]
    weekly_metadata = receipt_outputs.get("weekly_update", {})
    if not isinstance(weekly_metadata, Mapping):
        weekly_metadata = {}
    output_metadata = {key: value for key, value in receipt_outputs.items() if key != "weekly_update"}
    merged_metadata = {
        **dict(weekly_metadata),
        **output_metadata,
        **dict(metadata),
        "fixture_id": receipt_outputs["fixture_id"],
        "created_at": receipt.created_at,
        "operation": receipt.runtime_provenance["operation"],
    }
    if receipt.next_action:
        merged_metadata["next_action"] = receipt.next_action
    return WeeklyUpdateStageObservation(
        stage_id=stage_id,
        stage_type=stage_type,
        adapter=adapter,
        outcome=outcome,
        status=status,
        summary=summary,
        receipt_id=receipt.receipt_id,
        metadata=merged_metadata,
    )


def _route_outcome(follow_up: Mapping[str, Any] | None) -> str:
    if not follow_up:
        return "no_follow_up"
    owner = str(follow_up.get("owner") or "").strip().lower()
    if owner == "jarvis":
        return "routed_to_jarvis"
    if owner:
        return "routed_to_owner"
    return "needs_suman"


def _as_weekly_update(fixture: Mapping[str, Any] | WeeklyUpdateFixture) -> WeeklyUpdateFixture:
    if isinstance(fixture, WeeklyUpdateFixture):
        return fixture
    return weekly_update_from_fixture(fixture)


def _normalize_exported_weekly_fixture(fixture: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept the OpenClaw lane fixture exporter shape as a weekly fixture."""

    source = fixture.get("weekly_update")
    if not isinstance(source, Mapping):
        return fixture
    blackboard = _optional_mapping(source.get("blackboard"))
    summary_fields = _optional_mapping(source.get("summary_fields"))
    note_path = source.get("note_path") or source.get("source_artifact") or source.get("note_root") or "missing-weekly-note"
    source_artifact = source.get("source_artifact") or note_path
    evidence_link = source.get("evidence_link") or blackboard.get("evidence_link") or str(note_path)
    checked = bool(blackboard.get("checked", source.get("checked", False)))
    read_state = str(
        blackboard.get(
            "read_state",
            source.get("read_state", "read_clear" if checked else "unread"),
        )
    )

    normalized = dict(fixture)
    normalized["schema"] = WEEKLY_UPDATE_FIXTURE_SCHEMA
    normalized["created_at"] = fixture.get("created_at") or fixture.get("generated_at") or DEFAULT_CREATED_AT
    normalized["weekly_update"] = {
        "mode": source.get("mode") or "weekly-personal",
        "title": source.get("title"),
        "note_path": str(note_path),
        "item_id": source.get("item_id") or fixture.get("fixture_id") or "weekly-update-item",
        "source_artifact": str(source_artifact),
        "blackboard_bucket": blackboard.get("bucket") or "Read / Clear",
        "owner": source.get("owner") or "Suman",
        "evidence_link": str(evidence_link),
        "checked": checked,
        "from_agent": source.get("from_agent") or "Jarvis / weekly_check_in",
        "details": {
            "bottom_line": summary_fields.get("bottom_line"),
            "pattern": summary_fields.get("pattern"),
            "move": summary_fields.get("move") or summary_fields.get("suman_contract"),
        },
        "follow_up": source.get("follow_up"),
    }
    normalized["read_state"] = {"checked": checked, "state": read_state}
    normalized["residual_risk"] = fixture.get(
        "residual_risk",
        "Exporter fixture should be compared with live Blackboard readback before takeover.",
    )
    return normalized


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _optional_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("optional fixture section must be a mapping when present")
    return value


def _required_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"weekly update fixture requires non-empty {key!r}")
    return value


def _optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
