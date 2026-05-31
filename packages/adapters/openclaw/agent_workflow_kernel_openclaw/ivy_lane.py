"""Read-only Ivy/Jonah lane adoption mapping.

This module consumes local OpenClaw-shaped fixtures only. It deliberately maps
the editorial lane into kernel workflow concepts without importing or mutating
OpenClaw runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent_workflow_kernel.adapters import (
    ADAPTER_STATUS_BLOCKED,
    ADAPTER_STATUS_NEEDS_HUMAN,
    ADAPTER_STATUS_SUCCEEDED,
    make_adapter_receipt,
)
from agent_workflow_kernel.contracts import AdapterFamily, AdapterInvocation, ArtifactRef, Receipt, to_plain_data
from agent_workflow_kernel.dsl import canonical_json

from .mapping import OpenClawReferenceMapping, mapping_from_fixture
from .readonly import DEFAULT_CREATED_AT, artifact_refs_from_fixture, guard_read_only_operation


IVY_JONAH_FIXTURE_SCHEMA = "openclaw.ivy-jonah.fixture.v1"
IVY_JONAH_ADOPTION_REPORT_SCHEMA = "openclaw.ivy-jonah.adoption-report.v1"
IVY_JONAH_WORKFLOW_ID = "ivy_jonah_editorial"
PUBLIC_PUBLISH_FORBIDDEN_ACTIONS = (
    "publish",
    "send externally",
    "external_publish",
    "email",
    "x_post",
    "linkedin_post",
    "browser_publish",
)


@dataclass(slots=True, frozen=True)
class IvyJonahActorRef:
    """OpenClaw actor identity carried by an Ivy/Jonah fixture."""

    name: str
    agent_id: str | None = None
    role: str = "agent"
    session_key: str | None = None


@dataclass(slots=True, frozen=True)
class IvyJonahReviewSurface:
    """Human or operator-visible review surface reference."""

    surface_id: str
    kind: str
    title: str | None = None
    uri: str | None = None
    p_stage: str | None = None
    status: str = "observed"
    readback_required: bool = True


@dataclass(slots=True, frozen=True)
class IvyJonahTranscriptRef:
    """Transcript or native-session proof reference."""

    transcript_id: str
    kind: str
    uri: str
    status: str = "observed"
    proof_schema: str | None = None


@dataclass(slots=True, frozen=True)
class IvyJonahPublishPacketRef:
    """Local publish-packet artifact that must not imply public publishing."""

    packet_id: str
    kind: str
    uri: str
    status: str
    external_publish_performed: bool = False


@dataclass(slots=True, frozen=True)
class IvyJonahStageObservation:
    """One OpenClaw lane event aligned to ``ivy_jonah_editorial``."""

    stage_id: str
    p_stage: str
    status: str
    outcome: str
    actor: str
    adapter: str
    receipt_kind: str
    summary: str
    artifact_roles: tuple[str, ...] = ()
    requires_human_gate: bool = False
    public_publish_blocked: bool = False
    source: str | None = None

    def to_data(self) -> dict[str, Any]:
        return to_plain_data(self)


@dataclass(slots=True, frozen=True)
class IvyJonahFixture:
    """Normalized local fixture for the OpenClaw Ivy/Jonah editorial lane."""

    fixture_id: str
    created_at: str
    project_id: str
    handoff_type: str
    action: str
    p_stage: str
    ivy_actor: IvyJonahActorRef
    jonah_actor: IvyJonahActorRef
    human_actor: IvyJonahActorRef
    mapping: OpenClawReferenceMapping
    review_surfaces: tuple[IvyJonahReviewSurface, ...] = ()
    transcript_refs: tuple[IvyJonahTranscriptRef, ...] = ()
    publish_packet_refs: tuple[IvyJonahPublishPacketRef, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def instance_id(self) -> str:
        return f"openclaw-ivy-jonah:{self.fixture_id}"


@dataclass(slots=True, frozen=True)
class IvyJonahAdoptionReport:
    """Deterministic shadow-adoption decision packet for Ivy/Jonah."""

    schema: str
    fixture_id: str
    workflow_id: str
    project_id: str
    handoff_type: str
    p_stage: str
    ready_for_shadow: bool
    requires_human_gate: bool
    public_publish_blocked: bool
    open_questions: tuple[str, ...]
    stage_observations: tuple[IvyJonahStageObservation, ...]
    receipt_ids: tuple[str, ...]
    mapping: Mapping[str, Any]
    evidence_refs: Mapping[str, Any]
    residual_risk: str

    def to_data(self) -> dict[str, Any]:
        return to_plain_data(self)

    def to_json(self) -> str:
        return canonical_json(self.to_data()) + "\n"


@dataclass(slots=True, frozen=True)
class IvyJonahAdoption:
    """Complete read-only conversion result for an Ivy/Jonah fixture."""

    fixture: IvyJonahFixture
    stage_observations: tuple[IvyJonahStageObservation, ...]
    receipts: tuple[Receipt, ...]
    report: IvyJonahAdoptionReport


def load_ivy_jonah_fixture(path: str | Path) -> IvyJonahFixture:
    """Load and normalize a local Ivy/Jonah fixture."""

    fixture_path = Path(path)
    with fixture_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return ivy_jonah_fixture_from_mapping(data)


def ivy_jonah_fixture_from_mapping(data: Mapping[str, Any]) -> IvyJonahFixture:
    """Validate fixture shape and carry OpenClaw references without resolving them."""

    if data.get("schema") != IVY_JONAH_FIXTURE_SCHEMA:
        data = _normalize_exported_ivy_fixture(data)
        if data.get("schema") != IVY_JONAH_FIXTURE_SCHEMA:
            raise ValueError(f"Unsupported Ivy/Jonah fixture schema: {data.get('schema')}")
    fixture_id = _required_string(data, "fixture_id")
    project = _required_mapping(data, "project")
    handoff = _required_mapping(data, "handoff")
    actors = _required_mapping(data, "actors")
    guard_read_only_operation(str(_mapping(data.get("invocation")).get("operation", "inspect_fixture")))

    return IvyJonahFixture(
        fixture_id=fixture_id,
        created_at=str(data.get("created_at") or DEFAULT_CREATED_AT),
        project_id=_required_string(project, "project_id"),
        handoff_type=_required_string(handoff, "handoff_type"),
        action=_required_string(handoff, "action"),
        p_stage=str(project.get("p_stage") or handoff.get("p_stage") or "P3").upper(),
        ivy_actor=_actor_from_mapping(_required_mapping(actors, "ivy"), default_role="writer"),
        jonah_actor=_actor_from_mapping(_required_mapping(actors, "jonah"), default_role="editor"),
        human_actor=_actor_from_mapping(_required_mapping(actors, "human"), default_role="final_approver"),
        mapping=mapping_from_fixture(data),
        review_surfaces=tuple(_review_surface_from_mapping(item) for item in data.get("review_surfaces") or ()),
        transcript_refs=tuple(_transcript_ref_from_mapping(item) for item in data.get("transcript_refs") or ()),
        publish_packet_refs=tuple(_publish_packet_from_mapping(item) for item in data.get("publish_packet_refs") or ()),
        artifact_refs=artifact_refs_from_fixture(data.get("artifacts")),
        raw=data,
    )


def adopt_ivy_jonah_fixture(fixture: Mapping[str, Any] | IvyJonahFixture) -> IvyJonahAdoption:
    """Convert a local OpenClaw Ivy/Jonah fixture into AWK adoption evidence."""

    parsed = fixture if isinstance(fixture, IvyJonahFixture) else ivy_jonah_fixture_from_mapping(fixture)
    stage_observations = stage_observations_from_fixture(parsed)
    receipts = receipts_from_stage_observations(parsed, stage_observations)
    report = adoption_report_from_observations(parsed, stage_observations, receipts)
    return IvyJonahAdoption(
        fixture=parsed,
        stage_observations=stage_observations,
        receipts=receipts,
        report=report,
    )


def stage_observations_from_fixture(fixture: IvyJonahFixture) -> tuple[IvyJonahStageObservation, ...]:
    """Map OpenClaw P-stage lane facts to ``ivy_jonah_editorial`` stages."""

    observations: list[IvyJonahStageObservation] = []
    handoff_type = fixture.handoff_type
    action = fixture.action
    p_stage = fixture.p_stage

    if handoff_type == "or_research_p3_approved_to_p4" and action == "advance_to_p4":
        observations.extend(_p3_to_p5_shadow_path(fixture))
    elif handoff_type in {"or_research_m5_publish_decision", "or_research_p5_publish_decision"}:
        observations.extend(_p5_publish_decision_path(fixture))
    else:
        observations.append(
            IvyJonahStageObservation(
                stage_id="accept_source_approval",
                p_stage=p_stage,
                status=ADAPTER_STATUS_BLOCKED,
                outcome="blocked",
                actor=fixture.human_actor.name,
                adapter="surface.human_review",
                receipt_kind="unsupported_ivy_jonah_handoff.v1",
                summary=f"Unsupported Ivy/Jonah handoff {handoff_type}/{action}.",
                requires_human_gate=True,
                public_publish_blocked=True,
                source="fixture.handoff",
            )
        )
    return tuple(observations)


def receipts_from_stage_observations(
    fixture: IvyJonahFixture,
    observations: Sequence[IvyJonahStageObservation],
) -> tuple[Receipt, ...]:
    """Emit read-only adapter receipts for each mapped stage observation."""

    receipts: list[Receipt] = []
    for observation in observations:
        invocation = AdapterInvocation(
            invocation_id=f"openclaw.ivy_lane:{fixture.fixture_id}:{observation.stage_id}",
            workflow_id=IVY_JONAH_WORKFLOW_ID,
            instance_id=fixture.instance_id,
            stage_run_id=f"{fixture.fixture_id}:{observation.stage_id}",
            adapter_family=_adapter_family_for_observation(observation),
            adapter_id="openclaw.ivy_lane.readonly",
            operation="map_reference_host",
            input_ref=f"fixture:{fixture.fixture_id}",
            context_packet_ref=f"fixture:{fixture.fixture_id}:context",
            idempotency_key=f"{fixture.fixture_id}:{observation.stage_id}",
        )
        outputs = {
            "fixture_id": fixture.fixture_id,
            "project_id": fixture.project_id,
            "handoff_type": fixture.handoff_type,
            "stage_observation": observation.to_data(),
            "openclaw_mapping": fixture.mapping.to_metadata(),
            "read_only": True,
        }
        receipts.append(
            make_adapter_receipt(
                invocation,
                status=observation.status,
                stage_id=observation.stage_id,
                summary=observation.summary,
                created_at=fixture.created_at,
                artifact_refs=fixture.artifact_refs,
                outputs=outputs,
                checks_run=(
                    "fixture_supplied",
                    "operation_read_only",
                    "ivy_jonah_stage_mapped",
                    "public_publish_gate_preserved",
                ),
                policy_snapshot={
                    "risk_class": "read_only",
                    "external_effects": False,
                    "requires_human_gate": observation.requires_human_gate,
                    "public_publish_blocked": observation.public_publish_blocked,
                },
                residual_risk=_residual_risk(fixture, observation),
                next_action=_next_action_for_observation(fixture, observation),
            )
        )
    return tuple(receipts)


def adoption_report_from_observations(
    fixture: IvyJonahFixture,
    observations: Sequence[IvyJonahStageObservation],
    receipts: Sequence[Receipt],
) -> IvyJonahAdoptionReport:
    """Build a stable shadow-readiness report from mapped Ivy/Jonah evidence."""

    open_questions = list(_fixture_open_questions(fixture))
    if not fixture.transcript_refs and any(item.stage_id == "editor_review" for item in observations):
        open_questions.append("No transcript_ref was supplied for the Ivy/Jonah editor review.")
    if not fixture.review_surfaces and any(item.requires_human_gate for item in observations):
        open_questions.append("No review_surface was supplied for the human gate.")
    if any(item.external_publish_performed for item in fixture.publish_packet_refs):
        open_questions.append("A publish_packet_ref says external_publish_performed=true; shadow takeover must remain blocked.")

    public_publish_blocked = _public_publish_blocked(fixture, observations)
    requires_human_gate = any(item.requires_human_gate for item in observations)
    required_stages = _required_stage_ids_for_fixture(fixture)
    observed_stage_ids = {item.stage_id for item in observations}
    terminal_ok = all(item.status in {ADAPTER_STATUS_SUCCEEDED, ADAPTER_STATUS_NEEDS_HUMAN} for item in observations)
    ready_for_shadow = (
        terminal_ok
        and required_stages.issubset(observed_stage_ids)
        and public_publish_blocked
        and requires_human_gate
        and not any(item.external_publish_performed for item in fixture.publish_packet_refs)
    )

    return IvyJonahAdoptionReport(
        schema=IVY_JONAH_ADOPTION_REPORT_SCHEMA,
        fixture_id=fixture.fixture_id,
        workflow_id=IVY_JONAH_WORKFLOW_ID,
        project_id=fixture.project_id,
        handoff_type=fixture.handoff_type,
        p_stage=fixture.p_stage,
        ready_for_shadow=ready_for_shadow,
        requires_human_gate=requires_human_gate,
        public_publish_blocked=public_publish_blocked,
        open_questions=tuple(sorted(dict.fromkeys(open_questions))),
        stage_observations=tuple(observations),
        receipt_ids=tuple(receipt.receipt_id for receipt in receipts),
        mapping=fixture.mapping.to_metadata(),
        evidence_refs={
            "review_surfaces": [to_plain_data(item) for item in fixture.review_surfaces],
            "transcript_refs": [to_plain_data(item) for item in fixture.transcript_refs],
            "publish_packet_refs": [to_plain_data(item) for item in fixture.publish_packet_refs],
            "artifact_refs": [to_plain_data(item) for item in fixture.artifact_refs],
        },
        residual_risk=_report_residual_risk(fixture),
    )


def _p3_to_p5_shadow_path(fixture: IvyJonahFixture) -> list[IvyJonahStageObservation]:
    return [
        IvyJonahStageObservation(
            stage_id="accept_source_approval",
            p_stage="P3",
            status=ADAPTER_STATUS_SUCCEEDED,
            outcome="selected",
            actor=fixture.human_actor.name,
            adapter="surface.human_review",
            receipt_kind="source_approval_selected.v1",
            summary="Suman-approved P3 handoff selected for Ivy P4 drafting.",
            artifact_roles=("p3_review_handoff",),
            requires_human_gate=True,
            public_publish_blocked=True,
            source=fixture.handoff_type,
        ),
        IvyJonahStageObservation(
            stage_id="build_draft_package",
            p_stage="P4",
            status=ADAPTER_STATUS_SUCCEEDED,
            outcome="ready",
            actor=fixture.ivy_actor.name,
            adapter="runtime.agent",
            receipt_kind="draft_package.v1",
            summary="Ivy advances the approved P3 into a P4 draft package for Jonah review.",
            artifact_roles=("draft_package", "source_trail"),
            public_publish_blocked=True,
            source="or_research_p4_ready",
        ),
        IvyJonahStageObservation(
            stage_id="editor_review",
            p_stage="P4",
            status=ADAPTER_STATUS_SUCCEEDED,
            outcome="accepted",
            actor=fixture.jonah_actor.name,
            adapter="runtime.a2a",
            receipt_kind="editorial_review.v1",
            summary="Jonah completes bounded Ivy/Jonah artifact review and clears P4 for P5.",
            artifact_roles=("editor_transcript", "editor_verdict"),
            public_publish_blocked=True,
            source="artifact_review_verdict.v1",
        ),
        IvyJonahStageObservation(
            stage_id="validate_editorial_state",
            p_stage="P4",
            status=ADAPTER_STATUS_SUCCEEDED,
            outcome="valid",
            actor="work_ledger_runner",
            adapter="lane.artifact_hash_validator",
            receipt_kind="editorial_hash_validation.v1",
            summary="Shadow mapping treats the Jonah-cleared P4 and transcript refs as the validation boundary.",
            artifact_roles=("editor_verdict", "draft_package"),
            public_publish_blocked=True,
            source="or_research_p5_ready",
        ),
        IvyJonahStageObservation(
            stage_id="p5_final_approval",
            p_stage="P5",
            status=ADAPTER_STATUS_NEEDS_HUMAN,
            outcome="approve_packet",
            actor=fixture.human_actor.name,
            adapter="surface.human_review",
            receipt_kind="final_editorial_decision.v1",
            summary="Ivy advances the Jonah-cleared P4 to P5 and stops for Suman final approval.",
            artifact_roles=("p5_review_surface",),
            requires_human_gate=True,
            public_publish_blocked=True,
            source="or_research_p5_ready",
        ),
    ]


def _p5_publish_decision_path(fixture: IvyJonahFixture) -> list[IvyJonahStageObservation]:
    return [
        IvyJonahStageObservation(
            stage_id="p5_final_approval",
            p_stage="P5",
            status=ADAPTER_STATUS_NEEDS_HUMAN,
            outcome="approve_packet",
            actor=fixture.human_actor.name,
            adapter="surface.human_review",
            receipt_kind="final_editorial_decision.v1",
            summary="P5 publish decision is converted into a local publish packet and browser staging plan only.",
            artifact_roles=("publish_bundle", "browser_staging_plan"),
            requires_human_gate=True,
            public_publish_blocked=True,
            source=fixture.handoff_type,
        )
    ]


def _adapter_family_for_observation(observation: IvyJonahStageObservation) -> AdapterFamily:
    if observation.adapter.startswith("runtime."):
        return AdapterFamily.RUNTIME
    if observation.adapter.startswith("surface."):
        return AdapterFamily.SURFACE
    if observation.adapter.startswith("lane."):
        return AdapterFamily.LANE
    return AdapterFamily.HOST


def _required_stage_ids_for_fixture(fixture: IvyJonahFixture) -> set[str]:
    if fixture.handoff_type == "or_research_p3_approved_to_p4":
        return {
            "accept_source_approval",
            "build_draft_package",
            "editor_review",
            "validate_editorial_state",
            "p5_final_approval",
        }
    if fixture.handoff_type in {"or_research_m5_publish_decision", "or_research_p5_publish_decision"}:
        return {"p5_final_approval"}
    return {"accept_source_approval"}


def _public_publish_blocked(
    fixture: IvyJonahFixture,
    observations: Sequence[IvyJonahStageObservation],
) -> bool:
    if not observations or not all(item.public_publish_blocked for item in observations):
        return False
    if any(item.external_publish_performed for item in fixture.publish_packet_refs):
        return False
    forbidden = tuple(str(item).lower() for item in fixture.raw.get("forbidden_actions") or ())
    if forbidden:
        return any(action in forbidden for action in PUBLIC_PUBLISH_FORBIDDEN_ACTIONS)
    return True


def _fixture_open_questions(fixture: IvyJonahFixture) -> tuple[str, ...]:
    questions = fixture.raw.get("open_questions") or ()
    if isinstance(questions, str):
        return (questions,)
    return tuple(str(item) for item in questions if str(item).strip())


def _residual_risk(fixture: IvyJonahFixture, observation: IvyJonahStageObservation) -> str:
    if observation.stage_id == "p5_final_approval":
        return "Public publish remains behind a human gate; no external publish/send action is modeled."
    return str(fixture.raw.get("residual_risk") or "Fixture data can drift from live OpenClaw state.")


def _report_residual_risk(fixture: IvyJonahFixture) -> str:
    if fixture.handoff_type in {"or_research_m5_publish_decision", "or_research_p5_publish_decision"}:
        return "Shadow adoption covers local publish packet preparation only; browser/public publishing stays out of scope."
    return "Shadow adoption still needs live dual-run evidence before replacing OpenClaw orchestration."


def _next_action_for_observation(fixture: IvyJonahFixture, observation: IvyJonahStageObservation) -> str:
    if observation.stage_id == "p5_final_approval":
        return "Keep P5 or public publish decisions behind Suman approval; do not externally publish from AWK shadow mode."
    return f"Continue mapping {fixture.handoff_type} through read-only AWK shadow receipts."


def _normalize_exported_ivy_fixture(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept the OpenClaw fixture exporter shape as an Ivy/Jonah fixture."""

    ivy = data.get("ivy")
    if not isinstance(ivy, Mapping):
        return data

    project = _mapping(ivy.get("project"))
    source_config = _mapping(ivy.get("source_config"))
    actors = _mapping(ivy.get("actors"))
    p3_stage = _stage_by_name(ivy.get("stages"), "P3")
    p5_stage = _stage_by_name(ivy.get("stages"), "P5")
    handoff_type = str(ivy.get("handoff_type") or "or_research_p3_approved_to_p4")
    action = str(p3_stage.get("next_action") or "advance_to_p4")

    normalized = dict(data)
    normalized["schema"] = IVY_JONAH_FIXTURE_SCHEMA
    normalized["created_at"] = data.get("created_at") or data.get("generated_at") or DEFAULT_CREATED_AT
    normalized["project"] = {
        "project_id": str(project.get("project_id") or data.get("fixture_id") or "openclaw-ivy-project"),
        "title": project.get("title"),
        "p_stage": str(project.get("gate") or p5_stage.get("stage") or p3_stage.get("stage") or "P3"),
        "target_channel": project.get("target_channel"),
        "article_type": project.get("article_type"),
    }
    normalized["handoff"] = {
        "handoff_type": handoff_type,
        "action": action,
        "p_stage": str(p3_stage.get("stage") or project.get("gate") or "P3"),
        "source": "openclaw_lane_fixture_exporter",
        "phase": "p4_editor_review" if handoff_type == "or_research_p3_approved_to_p4" else "publish_packet",
    }
    normalized["actors"] = {
        "ivy": _exported_actor(actors, "ivy", "ivy_agent_id", "Ivy", "editorial_writer"),
        "jonah": _exported_actor(actors, "jonah", "jonah_agent_id", "Jonah", "editorial_reviewer"),
        "human": _exported_actor(actors, "human", "human_agent_id", "Suman", "final_approver"),
    }
    normalized["review_surfaces"] = [
        _exported_review_surface(item)
        for item in ivy.get("review_surfaces") or data.get("surface_refs") or ()
        if isinstance(item, Mapping)
    ]
    normalized["transcript_refs"] = [
        item for item in ivy.get("transcript_refs") or () if isinstance(item, Mapping)
    ]
    normalized["publish_packet_refs"] = _exported_publish_packet_refs(ivy.get("publish_packet_refs"))
    normalized["forbidden_actions"] = (
        data.get("forbidden_actions")
        or source_config.get("forbidden_actions")
        or ["publish", "send externally", "external_publish"]
    )
    normalized["residual_risk"] = source_config.get("residual_risk") or data.get("residual_risk")
    normalized["open_questions"] = data.get("open_questions") or (
        "Exporter fixture should be compared with a live dual-run receipt before takeover.",
    )
    return normalized


def _stage_by_name(stages: Any, stage_name: str) -> Mapping[str, Any]:
    if not isinstance(stages, Sequence) or isinstance(stages, (str, bytes)):
        return {}
    for item in stages:
        if isinstance(item, Mapping) and str(item.get("stage", "")).upper() == stage_name:
            return item
    return {}


def _exported_actor(
    actors: Mapping[str, Any],
    name_key: str,
    agent_key: str,
    default_name: str,
    role: str,
) -> Mapping[str, Any]:
    value = actors.get(name_key)
    if isinstance(value, Mapping):
        actor = dict(value)
        actor.setdefault("name", default_name)
        actor.setdefault("role", role)
        return actor
    return {
        "name": str(value or default_name),
        "agent_id": _optional_string(actors.get(agent_key)),
        "role": role,
    }


def _exported_review_surface(item: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "surface_id": str(item.get("surface_id") or item.get("id") or item.get("kind") or "review_surface"),
        "kind": str(item.get("kind") or "review_surface"),
        "title": item.get("title"),
        "uri": item.get("uri") or item.get("external_id"),
        "p_stage": item.get("p_stage"),
        "status": str(item.get("status") or "observed"),
        "readback_required": bool(item.get("readback_required", True)),
    }


def _exported_publish_packet_refs(items: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        items = (items,) if items else ()
    refs: list[Mapping[str, Any]] = []
    for index, item in enumerate(items):
        if isinstance(item, Mapping):
            refs.append(item)
        elif item:
            refs.append(
                {
                    "packet_id": f"exported-publish-packet-{index + 1}",
                    "kind": "publish_packet_ref",
                    "uri": str(item),
                    "status": "prepared_local_only",
                    "external_publish_performed": False,
                }
            )
    return tuple(refs)


def _actor_from_mapping(data: Mapping[str, Any], *, default_role: str) -> IvyJonahActorRef:
    return IvyJonahActorRef(
        name=_required_string(data, "name"),
        agent_id=_optional_string(data.get("agent_id")),
        role=str(data.get("role") or default_role),
        session_key=_optional_string(data.get("session_key")),
    )


def _review_surface_from_mapping(data: Any) -> IvyJonahReviewSurface:
    item = _ensure_mapping(data, "review_surfaces entries")
    return IvyJonahReviewSurface(
        surface_id=_required_string(item, "surface_id"),
        kind=_required_string(item, "kind"),
        title=_optional_string(item.get("title")),
        uri=_optional_string(item.get("uri")),
        p_stage=_optional_string(item.get("p_stage")),
        status=str(item.get("status") or "observed"),
        readback_required=bool(item.get("readback_required", True)),
    )


def _transcript_ref_from_mapping(data: Any) -> IvyJonahTranscriptRef:
    item = _ensure_mapping(data, "transcript_refs entries")
    return IvyJonahTranscriptRef(
        transcript_id=_required_string(item, "transcript_id"),
        kind=_required_string(item, "kind"),
        uri=_required_string(item, "uri"),
        status=str(item.get("status") or "observed"),
        proof_schema=_optional_string(item.get("proof_schema")),
    )


def _publish_packet_from_mapping(data: Any) -> IvyJonahPublishPacketRef:
    item = _ensure_mapping(data, "publish_packet_refs entries")
    return IvyJonahPublishPacketRef(
        packet_id=_required_string(item, "packet_id"),
        kind=_required_string(item, "kind"),
        uri=_required_string(item, "uri"),
        status=_required_string(item, "status"),
        external_publish_performed=bool(item.get("external_publish_performed", False)),
    )


def _required_mapping(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    return _ensure_mapping(data.get(key), key)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _ensure_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Ivy/Jonah fixture requires {label} to be a mapping")
    return value


def _required_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Ivy/Jonah fixture requires non-empty {key!r}")
    return value


def _optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
