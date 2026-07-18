"""Non-live X Digest tracer adapters built on generic AWK contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from agent_workflow_kernel import (
    AdapterFamily,
    AdapterInvocation,
    AdapterRegistration,
    AdapterResult,
    ArtifactRef,
    CapabilitySet,
    HostDescriptor,
    LaneDescriptor,
    Receipt,
    RiskClass,
    StageRun,
    digest_data,
    make_adapter_receipt,
    result_from_receipt,
    to_plain_data,
)
from agent_workflow_kernel import ADAPTER_STATUS_SUCCEEDED


X_DIGEST_TRACER_SCHEMA = "awk.x_digest_tracer.v1"


class XBookmarkIntakeLaneAdapter:
    """Build a bookmark digest from a resolved, read-only workflow input."""

    adapter_id = "lane.x_bookmark_intake"
    family = AdapterFamily.LANE
    operations = ("describe", "build_stage_input")

    def __init__(self, *, created_at: str | None = None) -> None:
        self.created_at = created_at
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("resolved_workflow_input", "bookmark_digest", "non_live"),
            metadata={"schema": X_DIGEST_TRACER_SCHEMA},
        )

    def describe(self) -> LaneDescriptor:
        return LaneDescriptor(
            lane_id="x-digest-bookmark-intake",
            name="X Digest Bookmark Intake",
            capability_set=self.capabilities(),
        )

    def build_stage_input(self, stage_run: StageRun, domain_state: Mapping[str, Any]) -> Mapping[str, Any]:
        bookmark_window = _mapping(_mapping(domain_state.get("inputs")).get("bookmark_window"))
        items = _bookmark_items(bookmark_window)
        digest = {
            "schema": "x_bookmark_digest.v1",
            "window": {
                "label": bookmark_window.get("label"),
                "source": bookmark_window.get("source", "fixture"),
            },
            "item_count": len(items),
            "items": items,
        }
        role = _first_output_role(domain_state, default="bookmark_digest")
        outcome = "ready" if items else "no_candidates"
        return {
            "schema": X_DIGEST_TRACER_SCHEMA,
            "outcome": outcome,
            role: digest,
            "bookmark_digest": digest,
            "item_count": len(items),
            "artifact_refs": (_artifact_ref(stage_run, role=role, content=digest, created_by=self.adapter_id),),
        }


class XDigestDraftRuntimeAdapter:
    """Deterministically produce option and draft packets for the tracer lane."""

    adapter_id = "runtime.agent"
    family = AdapterFamily.RUNTIME
    operations = ("invoke",)

    def __init__(self, *, created_at: str | None = None) -> None:
        self.created_at = created_at
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("x_digest_option_packet", "x_digest_draft_packet", "deterministic_fixture"),
            metadata={"schema": X_DIGEST_TRACER_SCHEMA},
        )

    def invoke(self, invocation: AdapterInvocation, runtime_input: Mapping[str, Any]) -> AdapterResult:
        stage_id = _string(_mapping(runtime_input.get("stage")).get("id"))
        if stage_id == "propose_post_options":
            outcome = "options_ready"
            role = _first_output_role(runtime_input, default="option_packet")
            content = _option_packet(runtime_input)
        elif stage_id == "draft_selected_posts":
            outcome = "draft_ready"
            role = _first_output_role(runtime_input, default="draft_post_packet")
            content = _draft_post_packet(runtime_input, revised=False)
        elif stage_id == "revise_posts":
            outcome = "revised"
            role = _first_output_role(runtime_input, default="revised_post_packet")
            content = _draft_post_packet(runtime_input, revised=True)
        else:
            outcome = "blocked"
            role = _first_output_role(runtime_input, default="packet")
            content = {"schema": X_DIGEST_TRACER_SCHEMA, "stage_id": stage_id, "blocked": True}
        artifact = ArtifactRef(
            artifact_id=f"{invocation.stage_run_id}:{role}",
            role=role,
            uri=f"awk://{invocation.instance_id}/{invocation.stage_run_id}/{role}",
            content_hash=digest_data(content),
            mime_type="application/json",
            created_by=self.adapter_id,
        )
        outputs = {
            "schema": X_DIGEST_TRACER_SCHEMA,
            "outcome": outcome,
            role: content,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=f"X Digest tracer runtime produced {role}.",
            created_at=self._now(),
            artifact_refs=(artifact,),
            outputs=outputs,
            checks_run=("deterministic_fixture_generation", "no_external_effects"),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs, artifact_refs=(artifact,))

    def _now(self) -> str:
        return self.created_at or datetime.now(UTC).isoformat(timespec="microseconds")


class XPostPacketValidatorLaneAdapter:
    """Validate that Jonah reviewed the current post packet and alias it for publish."""

    adapter_id = "lane.x_post_packet_validator"
    family = AdapterFamily.LANE
    operations = ("describe", "build_stage_input")

    def __init__(self, *, created_at: str | None = None) -> None:
        self.created_at = created_at

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("reviewed_hash_compare", "validated_publish_packet", "non_live"),
            metadata={"schema": X_DIGEST_TRACER_SCHEMA},
        )

    def describe(self) -> LaneDescriptor:
        return LaneDescriptor(
            lane_id="x-post-packet-validator",
            name="X Post Packet Validator",
            capability_set=self.capabilities(),
        )

    def build_stage_input(self, stage_run: StageRun, domain_state: Mapping[str, Any]) -> Mapping[str, Any]:
        current = _current_post_packet_artifact(domain_state)
        reviewed_hash = _reviewed_hash(domain_state)
        current_hash = _string(current.get("content_hash"))
        if not current_hash or not reviewed_hash:
            outcome = "blocked"
        elif current_hash != reviewed_hash:
            outcome = "stale_review"
        else:
            outcome = "valid"
        role = _first_output_role(domain_state, default="validated_publish_packet")
        packet_content = _current_post_packet_content(domain_state)
        validated = {
            "schema": "x_validated_publish_packet.v1",
            "source_artifact": dict(current),
            "reviewed_draft_hash": reviewed_hash,
            "current_draft_hash": current_hash,
            "posts": packet_content.get("posts", []),
        }
        return {
            "schema": X_DIGEST_TRACER_SCHEMA,
            "outcome": outcome,
            role: validated,
            "current_draft_hash": current_hash,
            "reviewed_draft_hash": reviewed_hash,
            "artifact_refs": (
                _artifact_ref(stage_run, role=role, content=validated, created_by=self.adapter_id),
            ),
        }


class XDryRunPublicPublishHostAdapter:
    """Host-stage dry run that records what would be published after approval."""

    adapter_id = "host.x_public_publish"
    family = AdapterFamily.HOST
    operations = ("invoke", "describe")

    def __init__(self, *, created_at: str | None = None) -> None:
        self.created_at = created_at
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("dry_run_public_publish", "approval_bound", "idempotency_receipt"),
            metadata={"schema": X_DIGEST_TRACER_SCHEMA},
        )

    def describe(self) -> HostDescriptor:
        return HostDescriptor(
            host_id="x-public-publish-dry-run",
            host_kind="dry_run",
            capability_set=self.capabilities(),
        )

    def invoke(self, invocation: AdapterInvocation, runtime_input: Mapping[str, Any]) -> AdapterResult:
        inputs = _mapping(runtime_input.get("inputs"))
        publish_packet = _mapping(inputs.get("publish_packet"))
        approval_receipt = _mapping(inputs.get("approval_receipt"))
        content = {
            "schema": "x_public_publish_receipt.v1",
            "dry_run": True,
            "live_mutation_performed": False,
            "publish_packet": publish_packet,
            "approval_receipt_id": approval_receipt.get("receipt_id"),
            "idempotency_key": invocation.idempotency_key,
        }
        role = _first_output_role(runtime_input, default="x_publish_receipt")
        artifact = ArtifactRef(
            artifact_id=f"{invocation.stage_run_id}:{role}",
            role=role,
            uri=f"awk://{invocation.instance_id}/{invocation.stage_run_id}/{role}",
            content_hash=digest_data(content),
            mime_type="application/json",
            created_by=self.adapter_id,
        )
        outputs = {
            "schema": X_DIGEST_TRACER_SCHEMA,
            "outcome": "published",
            role: content,
            "dry_run": True,
            "live_mutation_performed": False,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="X public publish dry run recorded without external mutation.",
            created_at=self.created_at or datetime.now(UTC).isoformat(timespec="microseconds"),
            artifact_refs=(artifact,),
            outputs=outputs,
            checks_run=("prior_approval_supplied", "dry_run_only", "idempotency_key_recorded"),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs, artifact_refs=(artifact,))


def x_digest_tracer_registrations(**kwargs: Any) -> tuple[AdapterRegistration, ...]:
    created_at = kwargs.get("created_at")
    return (
        AdapterRegistration.from_lane_adapter(XBookmarkIntakeLaneAdapter(created_at=created_at)),
        AdapterRegistration.from_runtime_adapter(XDigestDraftRuntimeAdapter(created_at=created_at)),
        AdapterRegistration.from_lane_adapter(XPostPacketValidatorLaneAdapter(created_at=created_at)),
        AdapterRegistration.from_host_adapter(
            XDryRunPublicPublishHostAdapter(created_at=created_at),
            side_effects=(RiskClass.REVIEW_ONLY, RiskClass.LOCAL_DRAFT),
            replay_safe=True,
        ),
    )


def _bookmark_items(bookmark_window: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = bookmark_window.get("items") or bookmark_window.get("bookmarks") or []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    items = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            continue
        text = _string(item.get("text") or item.get("summary") or item.get("title"))
        if not text:
            continue
        items.append(
            {
                "id": _string(item.get("id")) or f"bookmark-{index}",
                "text": text,
                "url": _string(item.get("url")),
                "author": _string(item.get("author")) or "unknown",
                "project_hint": _string(item.get("project_hint")),
            }
        )
    return items


def _option_packet(runtime_input: Mapping[str, Any]) -> dict[str, Any]:
    digest = _latest_output(runtime_input, "collect_bookmarks", "bookmark_digest")
    items = _sequence(_mapping(digest).get("items"))
    project_context = _mapping(runtime_input.get("inputs")).get("project_context") or _mapping(runtime_input.get("workflow_inputs")).get("project_context")
    options = []
    for index, item in enumerate(items[:4], start=1):
        bookmark = _mapping(item)
        options.append(
            {
                "id": f"option-{index}",
                "source_bookmark_id": bookmark.get("id"),
                "source_url": bookmark.get("url"),
                "angle": _angle_from_text(_string(bookmark.get("text")), project_context),
                "recommended_form": "quote_with_comment" if index % 2 else "original_explainer",
                "why_now": "This bookmark connects to active project context and is suitable for a short public draft.",
            }
        )
    return {
        "schema": "x_post_option_packet.v1",
        "option_count": len(options),
        "options": options,
    }


def _draft_post_packet(runtime_input: Mapping[str, Any], *, revised: bool) -> dict[str, Any]:
    option_packet = _latest_output(runtime_input, "propose_post_options", "option_packet")
    options = _sequence(_mapping(option_packet).get("options"))
    selected = _selected_option_ids(runtime_input) or [str(_mapping(option).get("id")) for option in options[:2]]
    posts = []
    for option in options:
        option_map = _mapping(option)
        if option_map.get("id") not in selected:
            continue
        suffix = " Revised after review." if revised else ""
        posts.append(
            {
                "option_id": option_map.get("id"),
                "text": f"{option_map.get('angle')} {option_map.get('why_now')}{suffix}".strip(),
                "source_url": option_map.get("source_url"),
                "publish_mode": option_map.get("recommended_form"),
            }
        )
    return {
        "schema": "x_post_draft_packet.v1",
        "revision": revised,
        "post_count": len(posts),
        "posts": posts,
    }


def _current_post_packet_artifact(domain_state: Mapping[str, Any]) -> Mapping[str, Any]:
    artifacts_by_stage = _mapping(domain_state.get("artifacts_by_stage"))
    revised = _mapping(_mapping(artifacts_by_stage.get("revise_posts")).get("revised_post_packet"))
    if revised:
        return revised
    return _mapping(_mapping(artifacts_by_stage.get("draft_selected_posts")).get("draft_post_packet"))


def _current_post_packet_content(domain_state: Mapping[str, Any]) -> Mapping[str, Any]:
    revised = _latest_output(domain_state, "revise_posts", "revised_post_packet")
    if revised:
        return _mapping(revised)
    return _mapping(_latest_output(domain_state, "draft_selected_posts", "draft_post_packet"))


def _reviewed_hash(domain_state: Mapping[str, Any]) -> str:
    outputs = _latest_stage_outputs(domain_state, "jonah_review_posts")
    for value in outputs.values():
        candidate = _mapping(value)
        reviewed = _string(candidate.get("reviewed_draft_hash"))
        if reviewed:
            return reviewed
    return _string(outputs.get("reviewed_draft_hash"))


def _selected_option_ids(runtime_input: Mapping[str, Any]) -> list[str]:
    latest = _mapping(runtime_input.get("latest_human_decision"))
    receipt = _mapping(latest.get("receipt"))
    constraints = _mapping(receipt.get("constraints"))
    raw = constraints.get("selected_option_ids") or constraints.get("selected_options") or []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, Sequence):
        return [str(item) for item in raw]
    return []


def _latest_output(runtime_input: Mapping[str, Any], stage_id: str, role: str) -> Any:
    outputs = _latest_stage_outputs(runtime_input, stage_id)
    return outputs.get(role)


def _latest_stage_outputs(runtime_input: Mapping[str, Any], stage_id: str) -> dict[str, Any]:
    receipts = _mapping(runtime_input.get("receipts_by_stage")).get(stage_id)
    if isinstance(receipts, Sequence) and not isinstance(receipts, (str, bytes)) and receipts:
        latest = _mapping(receipts[-1])
        return _mapping(latest.get("outputs"))
    return {}


def _first_output_role(runtime_input: Mapping[str, Any], *, default: str) -> str:
    outputs = _mapping(_mapping(runtime_input.get("stage")).get("outputs"))
    artifacts = outputs.get("artifacts")
    if isinstance(artifacts, Sequence) and not isinstance(artifacts, (str, bytes)):
        for artifact in artifacts:
            role = _string(_mapping(artifact).get("role"))
            if role:
                return role
    return default


def _artifact_ref(stage_run: StageRun, *, role: str, content: Mapping[str, Any], created_by: str) -> dict[str, Any]:
    return {
        "artifact_id": f"{stage_run.stage_run_id}:{role}",
        "role": role,
        "uri": f"awk://{stage_run.instance_id}/{stage_run.stage_run_id}/{role}",
        "content_hash": digest_data(content),
        "mime_type": "application/json",
        "created_by": created_by,
        "visibility": "internal",
    }


def _angle_from_text(text: str, project_context: Any) -> str:
    context = "OpenClaw and applied AI workflows"
    if isinstance(project_context, Mapping):
        context = _string(project_context.get("summary") or project_context.get("focus")) or context
    words = " ".join(text.split()[:18])
    return f"Reframe this bookmark through {context}: {words}"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
