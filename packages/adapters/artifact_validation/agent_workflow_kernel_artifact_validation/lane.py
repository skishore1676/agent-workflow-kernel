"""Native AWK artifact hash validator for staged system actions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from agent_workflow_kernel import (
    AdapterFamily,
    ArtifactRef,
    CapabilitySet,
    LaneDescriptor,
    Receipt,
    StageRun,
    make_adapter_receipt,
)
from agent_workflow_kernel.adapters import ADAPTER_STATUS_SUCCEEDED


HASH_VALIDATOR_SCHEMA = "awk.artifact_hash_validation.v1"


class ArtifactHashValidatorAdapter:
    """Validate that an editor verdict was made against the current draft."""

    adapter_id = "lane.artifact_hash_validator"
    family = AdapterFamily.LANE
    operations = (
        "describe",
        "build_stage_input",
        "validate_artifacts",
    )

    def __init__(self, *, created_at: str | None = None) -> None:
        self.created_at = created_at
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=(
                "editorial_staleness_check",
                "artifact_hash_compare",
                "awk_ledger_state_only",
            ),
            metadata={"schema": HASH_VALIDATOR_SCHEMA},
        )

    def describe(self) -> LaneDescriptor:
        return LaneDescriptor(
            lane_id="artifact-hash-validator",
            name="Artifact Hash Validator",
            capability_set=self.capabilities(),
        )

    def build_stage_input(
        self,
        stage_run: StageRun,
        domain_state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        current_draft = _current_draft_artifact(domain_state)
        reviewed_hash = _reviewed_draft_hash(domain_state)
        current_hash = _string(current_draft.get("content_hash"))
        if not current_hash or not reviewed_hash:
            outcome = "blocked"
            summary = "Cannot validate editorial state without current draft and editor verdict hashes."
        elif current_hash != reviewed_hash:
            outcome = "stale_review"
            summary = "Editor verdict is stale because it reviewed a different draft hash."
        else:
            outcome = "valid"
            summary = "Editor verdict matches the current draft hash."
        return {
            "schema": HASH_VALIDATOR_SCHEMA,
            "stage_run_id": stage_run.stage_run_id,
            "outcome": outcome,
            "summary": summary,
            "current_draft_hash": current_hash,
            "reviewed_draft_hash": reviewed_hash,
            "current_draft_artifact": dict(current_draft),
            "checks": [
                "current_draft_hash_resolved",
                "editor_verdict_hash_resolved",
                "hashes_compared",
            ],
        }

    def validate_artifacts(
        self,
        stage_run: StageRun,
        artifact_refs: tuple[ArtifactRef, ...],
    ) -> Receipt:
        receipt = make_adapter_receipt(
            _synthetic_invocation(stage_run),
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="Artifact hash validator recorded artifact refs.",
            created_at=self._now(),
            stage_id=stage_run.stage_id,
            artifact_refs=artifact_refs,
            outputs={"artifact_count": len(artifact_refs)},
            checks_run=("artifact_refs_recorded",),
        )
        self.receipts.append(receipt)
        return receipt

    def _now(self) -> str:
        return self.created_at or datetime.now(UTC).isoformat(timespec="microseconds")


def _current_draft_artifact(domain_state: Mapping[str, Any]) -> Mapping[str, Any]:
    artifacts_by_stage = _mapping(domain_state.get("artifacts_by_stage"))
    revised = _mapping(_mapping(artifacts_by_stage.get("revise_draft")).get("revised_draft_package"))
    if revised:
        return revised
    return _mapping(_mapping(artifacts_by_stage.get("build_draft_package")).get("draft_package"))


def _reviewed_draft_hash(domain_state: Mapping[str, Any]) -> str:
    for receipt in reversed(_receipt_sequence(domain_state.get("prior_receipts"))):
        value = _reviewed_hash_from_receipt(receipt)
        if value:
            return value
    receipts_by_stage = _mapping(domain_state.get("receipts_by_stage"))
    review_receipts = receipts_by_stage.get("editor_review")
    for receipt in reversed(_receipt_sequence(review_receipts)):
        value = _reviewed_hash_from_receipt(receipt)
        if value:
            return value
    return ""


def _reviewed_hash_from_receipt(receipt: Mapping[str, Any]) -> str:
    if receipt.get("stage_id") != "editor_review":
        return ""
    outputs = _mapping(receipt.get("outputs"))
    for candidate in (
        _mapping(outputs.get("verdict")).get("reviewed_draft_hash"),
        _mapping(_mapping(outputs.get("transcript")).get("verdict")).get("reviewed_draft_hash"),
        outputs.get("reviewed_draft_hash"),
    ):
        value = _string(candidate)
        if value:
            return value
    return ""


def _receipt_sequence(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        value = value.values()
    if not isinstance(value, list | tuple):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _synthetic_invocation(stage_run: StageRun):
    from agent_workflow_kernel import AdapterInvocation

    return AdapterInvocation(
        invocation_id=f"lane.artifact_hash_validator:validate:{stage_run.stage_run_id}",
        workflow_id="workflow",
        instance_id=stage_run.instance_id,
        stage_run_id=stage_run.stage_run_id,
        adapter_family=AdapterFamily.LANE,
        adapter_id="lane.artifact_hash_validator",
        operation="validate_artifacts",
        idempotency_key=stage_run.idempotency_key,
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""
