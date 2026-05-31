"""Receipt provenance helpers for prompt and context driven runs."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import ArtifactRef, Receipt
from .prompts import RenderedContext, canonicalize_data, digest_data


def build_prompt_provenance(rendered_context: RenderedContext) -> dict[str, Any]:
    """Build immutable prompt and context provenance for a receipt."""

    return {
        "registry_snapshot_digest": rendered_context.prompt_bundle.registry_snapshot_digest,
        "refs": rendered_context.prompt_bundle.provenance_refs(),
        "context": {
            "packet_id": rendered_context.packet.context_id,
            "packet_schema_version": rendered_context.packet.schema_version,
            "packet_digest": rendered_context.packet_digest,
            "canonical_bundle_digest": rendered_context.canonical_bundle_digest,
            "rendered_input_digest": rendered_context.rendered_input_digest,
        },
    }


def build_runtime_provenance(
    *,
    adapter_id: str,
    model: str,
    adapter_version: str | None = None,
    model_version: str | None = None,
    host_runtime: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize runtime identity fields required by prompt receipts."""

    data: dict[str, Any] = {
        "adapter_id": adapter_id,
        "model": model,
    }
    if adapter_version is not None:
        data["adapter_version"] = adapter_version
    if model_version is not None:
        data["model_version"] = model_version
    if host_runtime is not None:
        data["host_runtime"] = host_runtime
    if metadata:
        data["metadata"] = canonicalize_data(metadata)
    return data


def build_policy_snapshot(
    *,
    rendered_context: RenderedContext,
    granted: Sequence[str] = (),
    denied: Sequence[str] = (),
    policy_id: str | None = None,
    policy_version: str | None = None,
    policy_content_hash: str | None = None,
    redaction_mode: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the effective permission snapshot for a receipt."""

    policy_prompt = _first_policy_prompt(rendered_context)
    data: dict[str, Any] = {
        "policy_id": policy_id or (policy_prompt.get("id") if policy_prompt else None),
        "policy_version": policy_version or (policy_prompt.get("version") if policy_prompt else None),
        "content_hash": policy_content_hash or (policy_prompt.get("content_hash") if policy_prompt else None),
        "effective_permissions_digest": rendered_context.tool_permissions_digest,
        "granted": sorted(granted),
        "denied": sorted(denied),
    }
    if redaction_mode is not None:
        data["redaction_mode"] = redaction_mode
    if metadata:
        data["metadata"] = canonicalize_data(metadata)
    return data


def build_receipt(
    *,
    receipt_id: str,
    kind: str,
    status: str,
    summary: str,
    created_at: str,
    rendered_context: RenderedContext,
    runtime: Mapping[str, Any],
    granted_permissions: Sequence[str] = (),
    denied_permissions: Sequence[str] = (),
    artifact_refs: Sequence[ArtifactRef] = (),
    residual_risk: str | None = None,
    next_action: str | None = None,
    redaction_mode: str | None = None,
) -> Receipt:
    """Build a receipt carrying prompt, context, runtime, and policy provenance."""

    runtime_provenance = build_runtime_provenance(
        adapter_id=str(runtime["adapter_id"]),
        model=str(runtime["model"]),
        adapter_version=runtime.get("adapter_version"),
        model_version=runtime.get("model_version"),
        host_runtime=runtime.get("host_runtime"),
        metadata=runtime.get("metadata"),
    )
    policy_snapshot = build_policy_snapshot(
        rendered_context=rendered_context,
        granted=granted_permissions,
        denied=denied_permissions,
        redaction_mode=redaction_mode,
    )
    return Receipt(
        receipt_id=receipt_id,
        kind=kind,
        workflow_id=rendered_context.packet.workflow_id,
        instance_id=rendered_context.packet.instance_id,
        stage_id=rendered_context.packet.stage_id,
        stage_run_id=rendered_context.packet.stage_run_id,
        status=status,
        summary=summary,
        created_at=created_at,
        artifact_refs=tuple(artifact_refs),
        context_packet_ref=rendered_context.packet.context_id,
        prompt_provenance=build_prompt_provenance(rendered_context),
        runtime_provenance=runtime_provenance,
        policy_snapshot=policy_snapshot,
        residual_risk=residual_risk,
        next_action=next_action,
    )


def receipt_digest(receipt: Receipt) -> str:
    """Digest a receipt's canonical JSON-compatible data."""

    return digest_data(receipt)


def _first_policy_prompt(rendered_context: RenderedContext) -> Mapping[str, Any] | None:
    for ref in rendered_context.prompt_bundle.provenance_refs():
        if ref["kind"] == "policy":
            return ref
    return None
