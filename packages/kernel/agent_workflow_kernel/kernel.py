"""Initial generic workflow kernel facade."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping

from .adapter_registry import AdapterRegistration, AdapterRegistry, AdapterRegistryError
from .adapters import (
    ADAPTER_STATUS_SUCCEEDED,
    make_adapter_receipt,
)
from .contracts import (
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    FailureClass,
    Receipt,
    RiskClass,
    StageDef,
    StageRun,
    StageRunStatus,
    StageType,
    Transition,
    WorkflowDef,
    WorkflowInstance,
    WorkflowStatus,
    to_plain_data,
)
from .dsl import workflow_to_canonical_json
from .policy import ActionRequest, ApprovalDecision, GateDecision, HumanApprovalReceipt, PolicyEngine
from .prompts import (
    PromptHashMismatchError,
    PromptRegistry,
    PromptRegistryError,
    RenderedContext,
    digest_data,
    render_context_packet,
)
from .receipts import build_prompt_provenance
from .runner import RunnerResult, WorkflowRunner
from .storage import WorkflowLedger, iso_timestamp


KernelDecision = Literal["idle", "succeeded", "failed", "retry", "blocked", "waiting_on_human"]
KernelTransitionDecision = Literal["queued", "terminal", "blocked"]


@dataclass(frozen=True, slots=True)
class KernelRuntimeConfig:
    """Runtime dependencies for a portable kernel instance."""

    owner_id: str
    adapter_registry: AdapterRegistry
    prompt_registry: PromptRegistry | None = None
    prompt_registry_path: str | Path | None = None
    policy_engine: PolicyEngine = field(default_factory=PolicyEngine)
    default_lease_seconds: int = 300

    def __post_init__(self) -> None:
        if self.prompt_registry is not None and self.prompt_registry_path is not None:
            raise ValueError("provide either prompt_registry or prompt_registry_path, not both")
        if self.prompt_registry is None and self.prompt_registry_path is not None:
            object.__setattr__(
                self,
                "prompt_registry",
                PromptRegistry.load(self.prompt_registry_path),
            )


@dataclass(frozen=True, slots=True)
class KernelStep:
    stage_run: StageRun | None
    decision: KernelDecision
    adapter_result: AdapterResult | None = None
    receipt_id: str | None = None
    failure_summary: str | None = None


@dataclass(frozen=True, slots=True)
class KernelDecisionResult:
    stage_run: StageRun | None
    decision: KernelTransitionDecision
    outcome: str | None = None
    queued_stage_id: str | None = None
    terminal_status: WorkflowStatus | None = None
    failure_summary: str | None = None


@dataclass(frozen=True, slots=True)
class HumanGateSurfaceResult:
    stage_run: StageRun
    adapter_id: str
    operation: Literal["publish", "readback", "ingest_decisions"]
    status: str
    receipt_id: str | None = None
    surface_ref: Mapping[str, Any] | None = None
    outputs: Mapping[str, Any] = field(default_factory=dict)
    decision_result: KernelDecisionResult | None = None
    failure_summary: str | None = None


@dataclass(frozen=True, slots=True)
class _TransitionResult:
    decision: KernelTransitionDecision
    queued_stage_id: str | None = None
    terminal_status: WorkflowStatus | None = None
    failure_summary: str | None = None


class WorkflowKernel:
    """High-level facade for starting and stepping one workflow instance."""

    def __init__(
        self,
        ledger: WorkflowLedger,
        workflow: WorkflowDef,
        config: KernelRuntimeConfig,
    ) -> None:
        self.ledger = ledger
        self.workflow = workflow
        self.config = config
        self._stage_by_id = {stage.id: stage for stage in workflow.stages}
        self._instance_inputs: dict[str, Mapping[str, Any]] = {}
        self._transitions = {
            (transition.from_stage, transition.on): transition
            for transition in workflow.transitions
        }

    def start(
        self,
        *,
        instance_id: str,
        inputs: Mapping[str, Any],
        idempotency_key: str | None = None,
        now: Any = None,
    ) -> WorkflowInstance:
        if not self.workflow.stages:
            raise ValueError("workflow has no stages")
        self.ledger.initialize()
        first_stage = self.workflow.stages[0]
        created_at = iso_timestamp(now)
        input_hash = digest_data(
            {
                "workflow_id": self.workflow.id,
                "workflow_version": self.workflow.version,
                "inputs": inputs,
            }
        )
        workflow_definition_json = workflow_to_canonical_json(self.workflow)
        workflow_definition_hash = digest_data(json.loads(workflow_definition_json))
        instance = WorkflowInstance(
            instance_id=instance_id,
            workflow_def_id=self.workflow.id,
            workflow_version=self.workflow.version,
            status=WorkflowStatus.RUNNING,
            current_stage_id=first_stage.id,
            idempotency_key=idempotency_key,
            input_hash=input_hash,
        )
        try:
            self.ledger.insert_workflow_instance(
                instance,
                created_at=created_at,
                input_snapshot=dict(inputs),
                workflow_definition_json=workflow_definition_json,
                workflow_definition_hash=workflow_definition_hash,
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"workflow instance already exists: {instance_id}") from exc
        self._queue_stage(
            first_stage,
            instance_id=instance_id,
            attempt=1,
            inputs=inputs,
            created_at=created_at,
        )
        self._instance_inputs[instance_id] = dict(inputs)
        self.ledger.append_event(
            instance_id=instance_id,
            stage_run_id=None,
            event_type="workflow_started",
            actor=self.config.owner_id,
            payload={
                "workflow_id": self.workflow.id,
                "workflow_version": self.workflow.version,
                "first_stage_id": first_stage.id,
                "input_hash": input_hash,
                "workflow_definition_hash": workflow_definition_hash,
            },
            created_at=created_at,
        )
        return instance

    def run_once(self, *, now: Any = None) -> KernelStep:
        runner = WorkflowRunner(self.ledger, owner_id=self.config.owner_id)
        state: dict[str, Any] = {}
        step = runner.run_once(
            self._handle_stage(state, now=now),
            lease_seconds=self.config.default_lease_seconds,
            now=now,
        )
        if step.stage_run is None:
            return KernelStep(stage_run=None, decision="idle")

        stage = self._stage_by_id.get(step.stage_run.stage_id)
        if step.decision == "succeeded" and stage is not None:
            outcome = _outcome_for_stage_result(stage, state.get("adapter_result"))
            if self.workflow.transitions:
                self._advance_after_outcome(
                    step.stage_run,
                    stage,
                    outcome=outcome,
                    now=now,
                )
            else:
                self.ledger.update_workflow_instance(
                    instance_id=step.stage_run.instance_id,
                    status=WorkflowStatus.RUNNING,
                    current_stage_id=stage.id,
                    updated_at=now,
                    actor=self.config.owner_id,
                    event_type="workflow_stage_succeeded",
                    payload={
                        "stage_id": stage.id,
                        "stage_run_id": step.stage_run.stage_run_id,
                        "outcome": outcome,
                    },
                )
        elif step.decision == "waiting_on_human" and stage is not None:
            gate = self._human_gate(stage, step.stage_run)
            self.ledger.append_event(
                instance_id=step.stage_run.instance_id,
                stage_run_id=step.stage_run.stage_run_id,
                event_type="human_gate_waiting",
                actor=self.config.owner_id,
                payload={
                    "gate_id": gate.gate_id,
                    "requested_action": gate.requested_action,
                    "action_fingerprint": gate.action_fingerprint,
                    "stage_id": stage.id,
                    "stage_run_id": step.stage_run.stage_run_id,
                    "outcomes": list(stage.outcomes),
                },
                created_at=now,
            )
            self.ledger.update_workflow_instance(
                instance_id=step.stage_run.instance_id,
                status=WorkflowStatus.WAITING_ON_HUMAN,
                current_stage_id=stage.id,
                updated_at=now,
                actor=self.config.owner_id,
                event_type="workflow_waiting",
                payload={
                    "stage_id": stage.id,
                    "stage_run_id": step.stage_run.stage_run_id,
                    "gate_id": gate.gate_id,
                    "reason": state.get("failure_summary", step.decision),
                },
            )
        elif step.decision == "blocked" and stage is not None:
            status = (
                WorkflowStatus.WAITING_ON_HUMAN
                if stage.type == StageType.HUMAN_GATE
                else WorkflowStatus.BLOCKED
            )
            self.ledger.update_workflow_instance(
                instance_id=step.stage_run.instance_id,
                status=status,
                current_stage_id=stage.id,
                updated_at=now,
                actor=self.config.owner_id,
                event_type="workflow_waiting" if status == WorkflowStatus.WAITING_ON_HUMAN else "workflow_blocked",
                payload={
                    "stage_id": stage.id,
                    "stage_run_id": step.stage_run.stage_run_id,
                    "reason": state.get("failure_summary", step.decision),
                },
            )
        elif step.decision == "failed" and stage is not None:
            self.ledger.update_workflow_instance(
                instance_id=step.stage_run.instance_id,
                status=WorkflowStatus.BLOCKED,
                current_stage_id=stage.id,
                updated_at=now,
                actor=self.config.owner_id,
                event_type="workflow_blocked",
                payload={
                    "stage_id": stage.id,
                    "stage_run_id": step.stage_run.stage_run_id,
                    "reason": state.get("failure_summary", step.decision),
                },
            )
        elif step.decision == "retry" and stage is not None:
            self.ledger.update_workflow_instance(
                instance_id=step.stage_run.instance_id,
                status=WorkflowStatus.RETRYING,
                current_stage_id=stage.id,
                updated_at=now,
                actor=self.config.owner_id,
                event_type="workflow_retrying",
                payload={
                    "stage_id": stage.id,
                    "stage_run_id": step.stage_run.stage_run_id,
                    "reason": state.get("failure_summary", step.decision),
                },
            )

        return KernelStep(
            stage_run=step.stage_run,
            decision=step.decision,
            adapter_result=state.get("adapter_result"),
            receipt_id=state.get("receipt_id"),
            failure_summary=state.get("failure_summary"),
        )

    def ingest_human_decision(
        self,
        *,
        instance_id: str,
        decision: HumanApprovalReceipt | None,
        now: Any = None,
    ) -> KernelDecisionResult:
        waiting_run = self.ledger.find_waiting_human_stage_run(instance_id=instance_id)
        if waiting_run is None:
            return KernelDecisionResult(
                stage_run=None,
                decision="blocked",
                failure_summary=f"No human gate is waiting for instance {instance_id!r}.",
            )
        mismatch = self._definition_mismatch_summary(instance_id)
        if mismatch is not None:
            self._block_waiting_human_run(waiting_run, mismatch, now=now)
            return KernelDecisionResult(
                stage_run=waiting_run,
                decision="blocked",
                failure_summary=mismatch,
            )

        stage = self._stage_by_id.get(waiting_run.stage_id)
        if stage is None or stage.type != StageType.HUMAN_GATE:
            summary = "Waiting run does not match a human gate in this workflow."
            self._block_waiting_human_run(waiting_run, summary, now=now)
            return KernelDecisionResult(
                stage_run=waiting_run,
                decision="blocked",
                failure_summary=summary,
            )

        gate = self._human_gate(stage, waiting_run)
        validation_error = _human_decision_validation_error(
            decision,
            gate,
            now=now,
            allowed_decisions=stage.outcomes,
        )
        if validation_error is not None:
            self._block_waiting_human_run(waiting_run, validation_error, now=now)
            return KernelDecisionResult(
                stage_run=waiting_run,
                decision="blocked",
                failure_summary=validation_error,
            )

        assert decision is not None
        decision_text = _decision_text(decision.decision)
        outcome = self._outcome_for_human_decision(stage, decision_text)
        if outcome is None:
            summary = (
                f"No configured transition for human decision {decision_text!r} "
                f"from stage {stage.id!r}."
            )
            self.ledger.record_human_decision(
                decision,
                instance_id=instance_id,
                stage_run_id=waiting_run.stage_run_id,
                created_at=now,
                actor=self.config.owner_id,
            )
            self.ledger.complete_waiting_human_stage_run(
                stage_run_id=waiting_run.stage_run_id,
                status=StageRunStatus.BLOCKED,
                receipt_id=decision.approval_id,
                output_hash=digest_data(decision),
                failure_class=FailureClass.HUMAN_REJECTION,
                failure_summary=summary,
                now=now,
                actor=self.config.owner_id,
            )
            self.ledger.update_workflow_instance(
                instance_id=instance_id,
                status=WorkflowStatus.BLOCKED,
                current_stage_id=stage.id,
                updated_at=now,
                actor=self.config.owner_id,
                event_type="workflow_blocked",
                payload={
                    "stage_id": stage.id,
                    "stage_run_id": waiting_run.stage_run_id,
                    "decision": decision_text,
                    "reason": summary,
                },
            )
            return KernelDecisionResult(
                stage_run=waiting_run,
                decision="blocked",
                outcome=None,
                failure_summary=summary,
            )

        self.ledger.record_human_decision(
            decision,
            instance_id=instance_id,
            stage_run_id=waiting_run.stage_run_id,
            created_at=now,
            actor=self.config.owner_id,
        )
        self.ledger.complete_waiting_human_stage_run(
            stage_run_id=waiting_run.stage_run_id,
            status=StageRunStatus.SUCCEEDED,
            receipt_id=decision.approval_id,
            output_hash=digest_data(decision),
            now=now,
            actor=self.config.owner_id,
        )
        transition = self._advance_after_outcome(
            waiting_run,
            stage,
            outcome=outcome,
            now=now,
        )
        return KernelDecisionResult(
            stage_run=waiting_run,
            decision=transition.decision,
            outcome=outcome,
            queued_stage_id=transition.queued_stage_id,
            terminal_status=transition.terminal_status,
            failure_summary=transition.failure_summary,
        )

    def publish_waiting_human_gate(
        self,
        *,
        instance_id: str,
        surface_adapter_ref: str | None = None,
        allowed_decisions: tuple[str, ...] | None = None,
        evidence_refs: tuple[str, ...] | None = None,
        title: str | None = None,
        human_ask: str | None = None,
        human_ref: str | None = None,
        test_only: bool = False,
        non_live: bool = False,
        now: Any = None,
    ) -> HumanGateSurfaceResult:
        """Publish the current waiting human gate through its surface adapter."""

        waiting = self._waiting_human_gate_context(instance_id=instance_id)
        run, stage, gate = waiting
        registration = self._resolve_surface_registration(
            stage,
            operation="publish",
            surface_adapter_ref=surface_adapter_ref,
        )
        self._require_surface_policy_allows(
            run=run,
            stage=stage,
            gate=gate,
            registration=registration,
            operation="publish",
            now=now,
        )
        packet = self._human_gate_surface_packet(
            run=run,
            stage=stage,
            gate=gate,
            allowed_decisions=allowed_decisions,
            evidence_refs=evidence_refs,
            title=title,
            human_ask=human_ask,
            human_ref=human_ref,
            test_only=test_only,
            non_live=non_live,
        )
        created_at = iso_timestamp(now)
        invocation = self._surface_invocation(
            run=run,
            registration=registration,
            operation="publish",
            idempotency_key=f"{run.stage_run_id}:human_gate_surface:publish",
        )
        request_hash = digest_data({"invocation": invocation, "surface_packet": packet})
        self.ledger.record_adapter_invocation_started(
            invocation,
            request_hash=request_hash,
            actor=self.config.owner_id,
            side_effect_scope=_side_effect_scope(registration, stage, "publish", gate),
            started_at=created_at,
        )
        adapter_result = registration.adapter.publish(invocation, packet)
        response_hash = digest_data(adapter_result)
        self.ledger.complete_adapter_invocation(
            invocation_id=invocation.invocation_id,
            status=adapter_result.status,
            actor=self.config.owner_id,
            response_hash=response_hash,
            external_ref=_surface_external_ref(adapter_result.outputs),
            completed_at=created_at,
        )
        receipt = _make_kernel_adapter_receipt(
            invocation,
            status=adapter_result.status,
            summary=f"Kernel published waiting human gate through {registration.adapter_id}.",
            created_at=created_at,
            stage_id=stage.id,
            artifact_refs=adapter_result.artifact_refs,
            outputs=adapter_result.outputs,
            checks_run=("surface_adapter_registered", "waiting_gate_bound", "surface_packet_published"),
            policy_snapshot=to_plain_data(gate),
            residual_risk=adapter_result.residual_risk,
            next_action=adapter_result.next_hint,
            rendered_context=None,
        )
        self.ledger.record_receipt(receipt)
        surface_ref = _surface_ref_from_outputs(adapter_result.outputs)
        self.ledger.append_event(
            instance_id=run.instance_id,
            stage_run_id=run.stage_run_id,
            event_type="human_gate_surface_published",
            actor=self.config.owner_id,
            payload={
                "adapter_id": registration.adapter_id,
                "gate_id": gate.gate_id,
                "receipt_id": receipt.receipt_id,
                "status": adapter_result.status,
                "surface_ref": surface_ref,
            },
            created_at=created_at,
        )
        return HumanGateSurfaceResult(
            stage_run=run,
            adapter_id=registration.adapter_id,
            operation="publish",
            status=adapter_result.status,
            receipt_id=receipt.receipt_id,
            surface_ref=surface_ref,
            outputs=adapter_result.outputs,
            failure_summary=None if adapter_result.status == ADAPTER_STATUS_SUCCEEDED else adapter_result.residual_risk,
        )

    def readback_human_gate_surface(
        self,
        *,
        instance_id: str,
        surface_ref: Mapping[str, Any] | None = None,
        surface_adapter_ref: str | None = None,
        now: Any = None,
    ) -> HumanGateSurfaceResult:
        """Read back the published human-gate surface through its adapter."""

        waiting = self._waiting_human_gate_context(instance_id=instance_id)
        run, stage, gate = waiting
        registration = self._resolve_surface_registration(
            stage,
            operation="readback",
            surface_adapter_ref=surface_adapter_ref,
        )
        self._require_surface_policy_allows(
            run=run,
            stage=stage,
            gate=gate,
            registration=registration,
            operation="readback",
            now=now,
        )
        resolved_surface_ref = self._resolve_human_gate_surface_ref(run, surface_ref)
        created_at = iso_timestamp(now)
        invocation = self._surface_invocation(
            run=run,
            registration=registration,
            operation="readback",
            idempotency_key=f"{run.stage_run_id}:human_gate_surface:readback:{uuid.uuid4().hex[:8]}",
        )
        request_hash = digest_data({"invocation": invocation, "surface_ref": resolved_surface_ref})
        self.ledger.record_adapter_invocation_started(
            invocation,
            request_hash=request_hash,
            actor=self.config.owner_id,
            side_effect_scope=_side_effect_scope(registration, stage, "readback", gate),
            started_at=created_at,
        )
        adapter_receipt = registration.adapter.readback(resolved_surface_ref)
        outputs = {
            "surface_ref": resolved_surface_ref,
            "adapter_receipt": to_plain_data(adapter_receipt),
            "readback": _receipt_outputs(adapter_receipt),
        }
        response_hash = digest_data(outputs)
        self.ledger.complete_adapter_invocation(
            invocation_id=invocation.invocation_id,
            status=adapter_receipt.status,
            actor=self.config.owner_id,
            response_hash=response_hash,
            external_ref=_surface_external_ref({"surface_ref": resolved_surface_ref}),
            completed_at=created_at,
        )
        receipt = _make_kernel_adapter_receipt(
            invocation,
            status=adapter_receipt.status,
            summary=adapter_receipt.summary,
            created_at=created_at,
            stage_id=stage.id,
            artifact_refs=adapter_receipt.artifact_refs,
            outputs=outputs,
            checks_run=("surface_adapter_registered", "surface_ref_readback"),
            policy_snapshot=to_plain_data(gate),
            residual_risk=adapter_receipt.residual_risk,
            next_action=adapter_receipt.next_action,
            rendered_context=None,
        )
        self.ledger.record_receipt(receipt)
        self.ledger.append_event(
            instance_id=run.instance_id,
            stage_run_id=run.stage_run_id,
            event_type="human_gate_surface_readback",
            actor=self.config.owner_id,
            payload={
                "adapter_id": registration.adapter_id,
                "gate_id": gate.gate_id,
                "receipt_id": receipt.receipt_id,
                "status": adapter_receipt.status,
                "surface_ref": resolved_surface_ref,
            },
            created_at=created_at,
        )
        return HumanGateSurfaceResult(
            stage_run=run,
            adapter_id=registration.adapter_id,
            operation="readback",
            status=adapter_receipt.status,
            receipt_id=receipt.receipt_id,
            surface_ref=resolved_surface_ref,
            outputs=outputs,
            failure_summary=None if adapter_receipt.status == ADAPTER_STATUS_SUCCEEDED else adapter_receipt.summary,
        )

    def ingest_human_gate_surface_decision(
        self,
        *,
        instance_id: str,
        surface_ref: Mapping[str, Any] | None = None,
        surface_adapter_ref: str | None = None,
        allowed_decisions: tuple[str, ...] | None = None,
        evidence_refs: tuple[str, ...] | None = None,
        human_ref: str | None = None,
        now: Any = None,
    ) -> HumanGateSurfaceResult:
        """Ingest one structured surface decision and resume the waiting gate."""

        waiting = self._waiting_human_gate_context(instance_id=instance_id)
        run, stage, gate = waiting
        registration = self._resolve_surface_registration(
            stage,
            operation="ingest_decisions",
            surface_adapter_ref=surface_adapter_ref,
        )
        self._require_surface_policy_allows(
            run=run,
            stage=stage,
            gate=gate,
            registration=registration,
            operation="ingest_decisions",
            now=now,
        )
        resolved_surface_ref = self._resolve_human_gate_surface_ref(run, surface_ref)
        query = self._human_gate_surface_query(
            run=run,
            stage=stage,
            gate=gate,
            surface_ref=resolved_surface_ref,
            allowed_decisions=allowed_decisions,
            evidence_refs=evidence_refs,
            human_ref=human_ref,
        )
        created_at = iso_timestamp(now)
        invocation = self._surface_invocation(
            run=run,
            registration=registration,
            operation="ingest_decisions",
            idempotency_key=f"{run.stage_run_id}:human_gate_surface:ingest:{uuid.uuid4().hex[:8]}",
        )
        request_hash = digest_data({"invocation": invocation, "surface_query": query})
        self.ledger.record_adapter_invocation_started(
            invocation,
            request_hash=request_hash,
            actor=self.config.owner_id,
            side_effect_scope=_side_effect_scope(registration, stage, "ingest_decisions", gate),
            started_at=created_at,
        )
        decision_receipts = tuple(registration.adapter.ingest_decisions(query))
        candidate_receipts = tuple(
            receipt for receipt in decision_receipts if _is_surface_decision_receipt(receipt)
        )
        approval: HumanApprovalReceipt | None = None
        conversion_error: str | None = None
        if len(candidate_receipts) == 1 and candidate_receipts[0].status == ADAPTER_STATUS_SUCCEEDED:
            approval, conversion_error = _human_approval_from_surface_receipt(
                candidate_receipts[0],
                gate=gate,
                surface_adapter_id=registration.adapter_id,
            )
        ingest_status = (
            ADAPTER_STATUS_SUCCEEDED
            if (
                len(candidate_receipts) == 1
                and candidate_receipts[0].status == ADAPTER_STATUS_SUCCEEDED
                and conversion_error is None
            )
            else "blocked"
        )
        outputs = {
            "surface_ref": resolved_surface_ref,
            "surface_query": query,
            "candidate_decision_count": len(candidate_receipts),
            "adapter_receipts": [to_plain_data(receipt) for receipt in decision_receipts],
        }
        response_hash = digest_data(outputs)
        failure_summary = conversion_error or _surface_ingest_failure_summary(
            decision_receipts,
            candidate_receipts,
        )
        self.ledger.complete_adapter_invocation(
            invocation_id=invocation.invocation_id,
            status=ingest_status,
            actor=self.config.owner_id,
            response_hash=response_hash,
            external_ref=_surface_external_ref({"surface_ref": resolved_surface_ref}),
            error_class=None if ingest_status == ADAPTER_STATUS_SUCCEEDED else "human_decision_ingest_blocked",
            error_summary=None if ingest_status == ADAPTER_STATUS_SUCCEEDED else failure_summary,
            completed_at=created_at,
        )
        receipt = _make_kernel_adapter_receipt(
            invocation,
            status=ingest_status,
            summary=(
                "Kernel ingested one human-gate surface decision."
                if ingest_status == ADAPTER_STATUS_SUCCEEDED
                else failure_summary
            ),
            created_at=created_at,
            stage_id=stage.id,
            outputs=outputs,
            checks_run=("surface_adapter_registered", "exactly_one_decision_receipt"),
            policy_snapshot=to_plain_data(gate),
            residual_risk=None if ingest_status == ADAPTER_STATUS_SUCCEEDED else failure_summary,
            next_action=None if ingest_status == ADAPTER_STATUS_SUCCEEDED else "Fix the surface decision and retry from a fresh waiting gate.",
            rendered_context=None,
        )
        self.ledger.record_receipt(receipt)
        self.ledger.append_event(
            instance_id=run.instance_id,
            stage_run_id=run.stage_run_id,
            event_type="human_gate_surface_decision_ingested",
            actor=self.config.owner_id,
            payload={
                "adapter_id": registration.adapter_id,
                "gate_id": gate.gate_id,
                "receipt_id": receipt.receipt_id,
                "status": ingest_status,
                "candidate_decision_count": len(candidate_receipts),
            },
            created_at=created_at,
        )
        if ingest_status != ADAPTER_STATUS_SUCCEEDED:
            self._block_waiting_human_run(run, failure_summary, now=now)
            return HumanGateSurfaceResult(
                stage_run=run,
                adapter_id=registration.adapter_id,
                operation="ingest_decisions",
                status=ingest_status,
                receipt_id=receipt.receipt_id,
                surface_ref=resolved_surface_ref,
                outputs=outputs,
                decision_result=KernelDecisionResult(
                    stage_run=run,
                    decision="blocked",
                    failure_summary=failure_summary,
                ),
                failure_summary=failure_summary,
            )

        assert approval is not None
        decision_result = self.ingest_human_decision(
            instance_id=instance_id,
            decision=approval,
            now=now,
        )
        return HumanGateSurfaceResult(
            stage_run=run,
            adapter_id=registration.adapter_id,
            operation="ingest_decisions",
            status="succeeded" if decision_result.decision != "blocked" else "blocked",
            receipt_id=receipt.receipt_id,
            surface_ref=resolved_surface_ref,
            outputs=outputs,
            decision_result=decision_result,
            failure_summary=decision_result.failure_summary,
        )

    def _handle_stage(self, state: dict[str, Any], *, now: Any):
        def handler(run: StageRun) -> RunnerResult:
            stage = self._stage_by_id.get(run.stage_id)
            created_at = iso_timestamp(now)
            if stage is None:
                return self._blocked(state, "Unknown workflow stage.", FailureClass.DOMAIN_BLOCKED)
            mismatch = self._definition_mismatch_summary(run.instance_id)
            if mismatch is not None:
                return self._blocked(state, mismatch, FailureClass.DOMAIN_BLOCKED)
            if stage.type == StageType.HUMAN_GATE:
                summary = "Human gate reached; waiting for explicit decision ingestion."
                state["failure_summary"] = summary
                return RunnerResult(
                    decision="waiting_on_human",
                    failure_class=FailureClass.DOMAIN_BLOCKED,
                    failure_summary=summary,
                    approval_required=True,
                )

            operation = _operation_for_stage(stage)
            try:
                registration = self.config.adapter_registry.resolve(
                    stage.adapter,
                    stage_type=stage.type,
                )
            except AdapterRegistryError as exc:
                return self._blocked(state, str(exc), FailureClass.ADAPTER_UNAVAILABLE)
            if registration.family != AdapterFamily.RUNTIME:
                return self._blocked(
                    state,
                    "Only runtime adapter invocation is implemented in the initial kernel slice.",
                    FailureClass.ADAPTER_UNAVAILABLE,
                )
            if not registration.supports(operation):
                return self._blocked(
                    state,
                    f"{registration.adapter_id} does not support operation {operation!r}.",
                    FailureClass.ADAPTER_UNAVAILABLE,
                )

            gate = self.config.policy_engine.evaluate(
                ActionRequest(
                    action=operation,
                    target_ref=registration.adapter_id,
                    arguments={"stage_id": stage.id, "stage_type": stage.type.value},
                    risk_classes=registration.side_effects,
                    workflow_id=self.workflow.id,
                    instance_id=run.instance_id,
                    stage_id=stage.id,
                    actor_ref=run.actor_ref,
                    adapter_ref=registration.adapter_id,
                ),
                now=now,
            )
            if gate.decision == GateDecision.DENY.value:
                return self._blocked(state, gate.decision_reason or "Policy denied action.", FailureClass.POLICY_DENIAL)
            if gate.decision == GateDecision.REQUIRE_HUMAN.value:
                return self._blocked(
                    state,
                    gate.decision_reason or "Policy requires human approval.",
                    FailureClass.POLICY_DENIAL,
                    approval_required=True,
                )

            rendered_context: RenderedContext | None = None
            if stage.prompt_refs:
                try:
                    rendered_context = self._render_stage_context(
                        stage=stage,
                        run=run,
                        registration=registration,
                        gate=gate,
                    )
                except PromptRegistryError as exc:
                    failure_class = (
                        FailureClass.INVALID_OUTPUT
                        if isinstance(exc, PromptHashMismatchError)
                        else FailureClass.MISSING_DEPENDENCY
                    )
                    receipt = self._prompt_failure_receipt(
                        stage=stage,
                        run=run,
                        summary=str(exc),
                        created_at=created_at,
                        gate=gate,
                        failure_class=failure_class,
                    )
                    state["failure_summary"] = str(exc)
                    state["receipt_id"] = receipt.receipt_id
                    return RunnerResult(
                        decision="blocked",
                        receipt=receipt,
                        output_hash=digest_data(receipt),
                        failure_class=failure_class,
                        failure_summary=str(exc),
                    )
                self.ledger.record_stage_run_prompt_context(
                    stage_run_id=run.stage_run_id,
                    prompt_hash=rendered_context.prompt_bundle.prompt_bundle_digest,
                    context_packet_ref=rendered_context.packet.context_id,
                    context_packet_hash=rendered_context.packet_digest,
                    rendered_context_hash=rendered_context.rendered_input_digest,
                )

            invocation = AdapterInvocation(
                invocation_id=f"kernel:{run.stage_run_id}:{uuid.uuid4().hex[:12]}",
                workflow_id=self.workflow.id,
                instance_id=run.instance_id,
                stage_run_id=run.stage_run_id,
                adapter_family=registration.family,
                adapter_id=registration.adapter_id,
                operation=operation,
                input_ref=f"stage:{stage.id}:input",
                context_packet_ref=(
                    rendered_context.packet.context_id if rendered_context is not None else None
                ),
                idempotency_key=run.idempotency_key or f"{run.instance_id}:{stage.id}:{run.attempt}",
            )
            runtime_input = _runtime_input(self.workflow, stage, run, rendered_context)
            request_hash = digest_data(
                {
                    "invocation": invocation,
                    "runtime_input": runtime_input,
                    "policy_gate": gate,
                }
            )
            self.ledger.record_adapter_invocation_started(
                invocation,
                request_hash=request_hash,
                actor=self.config.owner_id,
                side_effect_scope=_side_effect_scope(registration, stage, operation, gate),
                started_at=created_at,
            )
            try:
                adapter_result = registration.adapter.invoke(invocation, runtime_input)
            except Exception as exc:
                self.ledger.complete_adapter_invocation(
                    invocation_id=invocation.invocation_id,
                    status="failed",
                    actor=self.config.owner_id,
                    response_hash=digest_data({"adapter_exception": str(exc)}),
                    error_class="adapter_exception",
                    error_summary=str(exc),
                    completed_at=created_at,
                )
                raise
            response_hash = digest_data(adapter_result)
            self.ledger.complete_adapter_invocation(
                invocation_id=invocation.invocation_id,
                status=adapter_result.status,
                actor=self.config.owner_id,
                response_hash=response_hash,
                error_class=None if adapter_result.status == ADAPTER_STATUS_SUCCEEDED else "adapter_result",
                error_summary=None
                if adapter_result.status == ADAPTER_STATUS_SUCCEEDED
                else f"Adapter returned {adapter_result.status}.",
                completed_at=created_at,
            )
            receipt = _make_kernel_adapter_receipt(
                invocation,
                status=adapter_result.status,
                summary=f"Kernel invoked {registration.adapter_id}.{operation}.",
                created_at=created_at,
                stage_id=stage.id,
                artifact_refs=adapter_result.artifact_refs,
                outputs=adapter_result.outputs,
                checks_run=("adapter_registered", "policy_preflight"),
                policy_snapshot=to_plain_data(gate),
                residual_risk=adapter_result.residual_risk,
                next_action=adapter_result.next_hint,
                rendered_context=rendered_context,
            )
            state["adapter_result"] = adapter_result
            state["receipt_id"] = receipt.receipt_id
            if adapter_result.status == ADAPTER_STATUS_SUCCEEDED:
                validation_errors = _stage_output_contract_errors(stage, adapter_result)
                if validation_errors:
                    summary = (
                        "Adapter output failed stage contract validation: "
                        + "; ".join(validation_errors)
                    )
                    invalid_receipt = _make_kernel_adapter_receipt(
                        invocation,
                        status=StageRunStatus.INVALID_OUTPUT.value,
                        summary=summary,
                        created_at=created_at,
                        stage_id=stage.id,
                        artifact_refs=adapter_result.artifact_refs,
                        outputs={
                            **to_plain_data(adapter_result.outputs),
                            "output_contract_errors": validation_errors,
                        },
                        checks_run=(
                            "adapter_registered",
                            "policy_preflight",
                            "output_contract_validation",
                        ),
                        policy_snapshot=to_plain_data(gate),
                        residual_risk=summary,
                        next_action="Fix adapter output or stage contract before retrying.",
                        rendered_context=rendered_context,
                    )
                    state["receipt_id"] = invalid_receipt.receipt_id
                    state["failure_summary"] = summary
                    return RunnerResult(
                        decision="failed",
                        receipt=invalid_receipt,
                        output_hash=response_hash,
                        failure_class=FailureClass.INVALID_OUTPUT,
                        failure_summary=summary,
                    )
                return RunnerResult(
                    decision="succeeded",
                    receipt=receipt,
                    output_hash=response_hash,
                )
            retry = _retry_result_for_adapter_failure(
                stage=stage,
                run=run,
                registration=registration,
                adapter_result=adapter_result,
                created_at=created_at,
            )
            if retry is not None:
                state["failure_summary"] = retry.failure_summary
                return replace(retry, receipt=receipt, output_hash=response_hash)
            state["failure_summary"] = f"Adapter returned {adapter_result.status}."
            return RunnerResult(
                decision="blocked",
                receipt=receipt,
                output_hash=response_hash,
                failure_class=FailureClass.DOMAIN_BLOCKED,
                failure_summary=f"Adapter returned {adapter_result.status}.",
            )

        return handler

    def _render_stage_context(
        self,
        *,
        stage: StageDef,
        run: StageRun,
        registration: AdapterRegistration,
        gate: Any,
    ) -> RenderedContext:
        registry = self.config.prompt_registry
        if registry is None:
            raise PromptRegistryError("Stage declares prompt_refs but no prompt registry is configured.")
        bundle = registry.resolve(stage.prompt_refs)
        instance = self.ledger.get_workflow_instance(run.instance_id)
        workflow_state = {
            "status": instance.status.value if instance is not None else None,
            "current_stage_id": instance.current_stage_id if instance is not None else None,
            "recovery_epoch": instance.recovery_epoch if instance is not None else None,
        }
        permissions = {
            "policy_gate": to_plain_data(gate),
            "adapter_side_effects": [risk.value for risk in registration.side_effects],
        }
        return render_context_packet(
            prompt_bundle=bundle,
            workflow_id=self.workflow.id,
            workflow_version=self.workflow.version,
            instance_id=run.instance_id,
            stage_id=stage.id,
            stage_run_id=run.stage_run_id,
            stage_type=stage.type.value,
            attempt=run.attempt,
            workflow_state=workflow_state,
            actor={
                "actor_ref": run.actor_ref,
                "runtime_target": registration.adapter_id,
                "adapter_family": registration.family.value,
            },
            inputs={
                "workflow": self.ledger.get_workflow_input_snapshot(run.instance_id)
                or self._instance_inputs.get(run.instance_id, {}),
                "stage": stage.inputs,
            },
            prior_receipts=_prior_receipts(self.ledger, run.instance_id),
            variables={
                "workflow_id": self.workflow.id,
                "instance_id": run.instance_id,
                "stage_id": stage.id,
                "stage_run_id": run.stage_run_id,
            },
            constraints={
                "stage_policy": stage.policy,
                "budget": stage.budget,
                "outputs": stage.outputs,
            },
            permissions=permissions,
        )

    def _prompt_failure_receipt(
        self,
        *,
        stage: StageDef,
        run: StageRun,
        summary: str,
        created_at: str,
        gate: Any,
        failure_class: FailureClass,
    ):
        return Receipt(
            receipt_id=f"receipt:kernel:{run.stage_run_id}:prompt_context_blocked",
            kind="kernel.prompt_context",
            workflow_id=self.workflow.id,
            instance_id=run.instance_id,
            stage_id=stage.id,
            stage_run_id=run.stage_run_id,
            status="blocked",
            summary=summary,
            created_at=created_at,
            prompt_provenance={
                "error": {
                    "class": failure_class.value,
                    "summary": summary,
                },
                "refs": [to_plain_data(ref) for ref in stage.prompt_refs],
            },
            runtime_provenance={
                "actor": self.config.owner_id,
                "checks_run": ["prompt_registry_resolution_failed"],
            },
            policy_snapshot=to_plain_data(gate),
            next_action="Fix prompt registry configuration before retrying the stage.",
        )

    def _definition_mismatch_summary(self, instance_id: str) -> str | None:
        provenance = self.ledger.get_workflow_instance_provenance(instance_id)
        if provenance is None:
            return f"Workflow instance {instance_id!r} is missing from the ledger."
        stored_hash = provenance.get("workflow_definition_hash")
        if not stored_hash:
            return None
        current_hash = digest_data(json.loads(workflow_to_canonical_json(self.workflow)))
        if stored_hash == current_hash:
            return None
        return (
            "Workflow definition hash mismatch for in-flight instance "
            f"{instance_id!r}: ledger has {stored_hash}, current kernel has {current_hash}."
        )

    def _waiting_human_gate_context(
        self,
        *,
        instance_id: str,
    ) -> tuple[StageRun, StageDef, Any]:
        mismatch = self._definition_mismatch_summary(instance_id)
        if mismatch is not None:
            raise ValueError(mismatch)
        waiting_run = self.ledger.find_waiting_human_stage_run(instance_id=instance_id)
        if waiting_run is None:
            raise ValueError(f"No human gate is waiting for instance {instance_id!r}.")
        stage = self._stage_by_id.get(waiting_run.stage_id)
        if stage is None or stage.type != StageType.HUMAN_GATE:
            raise ValueError("Waiting run does not match a human gate in this workflow.")
        return waiting_run, stage, self._human_gate(stage, waiting_run)

    def _resolve_surface_registration(
        self,
        stage: StageDef,
        *,
        operation: str,
        surface_adapter_ref: str | None,
    ) -> AdapterRegistration:
        adapter_ref = surface_adapter_ref or stage.adapter
        registration = self.config.adapter_registry.resolve(
            adapter_ref,
            stage_type=StageType.HUMAN_GATE,
        )
        if registration.family != AdapterFamily.SURFACE:
            raise AdapterRegistryError(
                "human gate surface lifecycle requires a surface adapter registration"
            )
        if not registration.supports(operation):
            raise AdapterRegistryError(
                f"{registration.adapter_id} does not support operation {operation!r}."
            )
        return registration

    def _surface_invocation(
        self,
        *,
        run: StageRun,
        registration: AdapterRegistration,
        operation: str,
        idempotency_key: str,
    ) -> AdapterInvocation:
        return AdapterInvocation(
            invocation_id=f"kernel:{run.stage_run_id}:human_gate_surface:{operation}:{uuid.uuid4().hex[:12]}",
            workflow_id=self.workflow.id,
            instance_id=run.instance_id,
            stage_run_id=run.stage_run_id,
            adapter_family=registration.family,
            adapter_id=registration.adapter_id,
            operation=operation,
            input_ref=f"stage:{run.stage_id}:human_gate_surface",
            idempotency_key=idempotency_key,
        )

    def _require_surface_policy_allows(
        self,
        *,
        run: StageRun,
        stage: StageDef,
        gate: Any,
        registration: AdapterRegistration,
        operation: str,
        now: Any,
    ) -> None:
        surface_gate = self.config.policy_engine.evaluate(
            ActionRequest(
                action=f"human_gate_surface.{operation}",
                target_ref=registration.adapter_id,
                arguments={
                    "stage_id": stage.id,
                    "stage_run_id": run.stage_run_id,
                    "stage_type": stage.type.value,
                    "operation": operation,
                    "waiting_gate_id": gate.gate_id,
                },
                risk_classes=registration.side_effects,
                workflow_id=self.workflow.id,
                instance_id=run.instance_id,
                stage_id=stage.id,
                actor_ref=run.actor_ref,
                adapter_ref=registration.adapter_id,
                evidence_refs=_human_gate_evidence_refs(stage, gate),
            ),
            now=now,
        )
        if surface_gate.decision in {GateDecision.DENY.value, GateDecision.REQUIRE_HUMAN.value}:
            raise AdapterRegistryError(
                "human gate surface adapter policy blocked "
                f"{registration.adapter_id}.{operation}: {surface_gate.decision_reason}"
            )

    def _human_gate_surface_packet(
        self,
        *,
        run: StageRun,
        stage: StageDef,
        gate: Any,
        allowed_decisions: tuple[str, ...] | None,
        evidence_refs: tuple[str, ...] | None,
        title: str | None,
        human_ask: str | None,
        human_ref: str | None,
        test_only: bool,
        non_live: bool,
    ) -> dict[str, Any]:
        decisions = allowed_decisions or _human_gate_allowed_decisions(stage)
        evidence = evidence_refs or _human_gate_evidence_refs(stage, gate)
        packet = dict(stage.surface)
        packet.update(
            {
                "schema": "human_gate_surface_packet.v1",
                "workflow_id": self.workflow.id,
                "workflow_version": self.workflow.version,
                "instance_id": run.instance_id,
                "stage_id": stage.id,
                "stage_run_id": run.stage_run_id,
                "gate_id": gate.gate_id,
                "requested_action": gate.requested_action,
                "exact_action": gate.requested_action,
                "exact_action_approved": gate.requested_action,
                "action_fingerprint": gate.action_fingerprint,
                "allowed_decisions": decisions,
                "evidence_refs": evidence,
                "policy_gate": to_plain_data(gate),
                "readback_required": True,
                "test_only": test_only,
                "non_live": non_live,
                "human_ref": human_ref or _human_ref(stage),
                "title": title or stage.surface.get("title") or f"Human gate: {stage.id}",
                "human_ask": human_ask
                or stage.surface.get("human_ask")
                or stage.surface.get("ask")
                or "Choose exactly one allowed decision.",
            }
        )
        return packet

    def _human_gate_surface_query(
        self,
        *,
        run: StageRun,
        stage: StageDef,
        gate: Any,
        surface_ref: Mapping[str, Any],
        allowed_decisions: tuple[str, ...] | None,
        evidence_refs: tuple[str, ...] | None,
        human_ref: str | None,
    ) -> dict[str, Any]:
        return {
            "query_id": f"{run.stage_run_id}:human_gate_surface_decision",
            "workflow_id": self.workflow.id,
            "workflow_version": self.workflow.version,
            "instance_id": run.instance_id,
            "stage_id": stage.id,
            "stage_run_id": run.stage_run_id,
            "gate_id": gate.gate_id,
            "requested_action": gate.requested_action,
            "exact_action": gate.requested_action,
            "exact_action_approved": gate.requested_action,
            "expected_action_fingerprint": gate.action_fingerprint,
            "action_fingerprint": gate.action_fingerprint,
            "allowed_decisions": allowed_decisions or _human_gate_allowed_decisions(stage),
            "evidence_refs": evidence_refs or _human_gate_evidence_refs(stage, gate),
            "human_ref": human_ref or _human_ref(stage),
            "surface_ref": dict(surface_ref),
            "policy_gate": to_plain_data(gate),
        }

    def _resolve_human_gate_surface_ref(
        self,
        run: StageRun,
        surface_ref: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        if surface_ref is not None:
            plain = to_plain_data(surface_ref)
            if not isinstance(plain, Mapping):
                raise ValueError("surface_ref must be a mapping or SurfaceRef-like value.")
            return dict(plain)
        for event in reversed(self.ledger.list_events(stage_run_id=run.stage_run_id)):
            if event["event_type"] != "human_gate_surface_published":
                continue
            payload = event["payload"]
            candidate = payload.get("surface_ref")
            if payload.get("status") == ADAPTER_STATUS_SUCCEEDED and isinstance(candidate, Mapping):
                return dict(candidate)
        raise ValueError(
            "No published human-gate surface reference is available; publish the waiting gate first."
        )

    def _human_gate(self, stage: StageDef, run: StageRun):
        return self.config.policy_engine.evaluate(
            ActionRequest(
                action=_human_decision_action(stage),
                target_ref=stage.adapter,
                arguments={
                    "stage_id": stage.id,
                    "stage_run_id": run.stage_run_id,
                    "stage_type": stage.type.value,
                    "outcomes": list(stage.outcomes),
                },
                risk_classes=(RiskClass.REVIEW_ONLY,),
                workflow_id=self.workflow.id,
                instance_id=run.instance_id,
                stage_id=stage.id,
                actor_ref=run.actor_ref,
                adapter_ref=stage.adapter,
                evidence_refs=_human_gate_evidence_refs(stage, None),
            )
        )

    def _outcome_for_human_decision(self, stage: StageDef, decision_text: str) -> str | None:
        for candidate in _human_decision_outcome_candidates(decision_text):
            if (stage.id, candidate) in self._transitions:
                return candidate
        return None

    def _advance_after_outcome(
        self,
        run: StageRun,
        stage: StageDef,
        *,
        outcome: str,
        now: Any,
    ) -> _TransitionResult:
        transition = self._transitions.get((stage.id, outcome))
        if transition is None:
            summary = f"No transition configured for stage {stage.id!r} outcome {outcome!r}."
            self.ledger.update_workflow_instance(
                instance_id=run.instance_id,
                status=WorkflowStatus.BLOCKED,
                current_stage_id=stage.id,
                updated_at=now,
                actor=self.config.owner_id,
                event_type="workflow_blocked",
                payload={
                    "stage_id": stage.id,
                    "stage_run_id": run.stage_run_id,
                    "outcome": outcome,
                    "reason": "missing_transition",
                },
            )
            return _TransitionResult(decision="blocked", failure_summary=summary)

        if transition.terminal is not None:
            status = _workflow_status_for_terminal(transition.terminal)
            self.ledger.update_workflow_instance(
                instance_id=run.instance_id,
                status=status,
                current_stage_id=None,
                updated_at=now,
                actor=self.config.owner_id,
                event_type="workflow_terminal",
                payload={
                    "from_stage": transition.from_stage,
                    "outcome": transition.on,
                    "terminal": transition.terminal,
                },
            )
            return _TransitionResult(decision="terminal", terminal_status=status)

        next_stage = self._next_stage_for_transition(transition)
        if next_stage is None:
            summary = (
                f"Transition from {transition.from_stage!r} on {transition.on!r} "
                "does not point to a known stage."
            )
            self.ledger.update_workflow_instance(
                instance_id=run.instance_id,
                status=WorkflowStatus.BLOCKED,
                current_stage_id=stage.id,
                updated_at=now,
                actor=self.config.owner_id,
                event_type="workflow_blocked",
                payload={
                    "stage_id": stage.id,
                    "stage_run_id": run.stage_run_id,
                    "outcome": outcome,
                    "reason": "invalid_transition",
                },
            )
            return _TransitionResult(decision="blocked", failure_summary=summary)

        created_at = iso_timestamp(now)
        attempt = self.ledger.next_stage_attempt(
            instance_id=run.instance_id,
            stage_id=next_stage.id,
        )
        self._queue_stage(
            next_stage,
            instance_id=run.instance_id,
            attempt=attempt,
            inputs={"from_stage_run_id": run.stage_run_id, "transition_outcome": outcome},
            created_at=created_at,
        )
        self.ledger.update_workflow_instance(
            instance_id=run.instance_id,
            status=WorkflowStatus.RUNNING,
            current_stage_id=next_stage.id,
            updated_at=now,
            actor=self.config.owner_id,
            event_type="workflow_transitioned",
            payload={
                "from_stage": transition.from_stage,
                "outcome": transition.on,
                "to_stage": next_stage.id,
                "queued_stage_run_id": f"{run.instance_id}:{next_stage.id}:{attempt}",
            },
        )
        return _TransitionResult(decision="queued", queued_stage_id=next_stage.id)

    def _next_stage_for_transition(self, transition: Transition) -> StageDef | None:
        if transition.to_stage is None:
            return None
        return self._stage_by_id.get(transition.to_stage)

    def _block_waiting_human_run(self, run: StageRun, summary: str, *, now: Any) -> None:
        self.ledger.complete_waiting_human_stage_run(
            stage_run_id=run.stage_run_id,
            status=StageRunStatus.BLOCKED,
            failure_class=FailureClass.DOMAIN_BLOCKED,
            failure_summary=summary,
            now=now,
            actor=self.config.owner_id,
        )
        self.ledger.update_workflow_instance(
            instance_id=run.instance_id,
            status=WorkflowStatus.BLOCKED,
            current_stage_id=run.stage_id,
            updated_at=now,
            actor=self.config.owner_id,
            event_type="workflow_blocked",
            payload={
                "stage_id": run.stage_id,
                "stage_run_id": run.stage_run_id,
                "reason": summary,
            },
        )

    def _blocked(
        self,
        state: dict[str, Any],
        summary: str,
        failure_class: FailureClass,
        *,
        approval_required: bool = False,
    ) -> RunnerResult:
        state["failure_summary"] = summary
        return RunnerResult(
            decision="blocked",
            failure_class=failure_class,
            failure_summary=summary,
            approval_required=approval_required,
        )

    def _queue_stage(
        self,
        stage: StageDef,
        *,
        instance_id: str,
        attempt: int,
        inputs: Mapping[str, Any],
        created_at: str,
    ) -> None:
        run = StageRun(
            stage_run_id=f"{instance_id}:{stage.id}:{attempt}",
            instance_id=instance_id,
            stage_id=stage.id,
            status=StageRunStatus.QUEUED,
            attempt=attempt,
            adapter_id=stage.adapter,
            actor_ref=_actor_ref(stage),
        )
        self.ledger.insert_stage_run(
            run,
            input_hash=digest_data({"stage": stage, "inputs": inputs, "attempt": attempt}),
            idempotency_key=f"{instance_id}:{stage.id}:{attempt}",
            created_at=created_at,
        )


def _operation_for_stage(stage: StageDef) -> str:
    operation = stage.inputs.get("operation")
    if operation is not None:
        return str(operation)
    return "invoke"


def _human_decision_action(stage: StageDef) -> str:
    operation = stage.inputs.get("decision_action") or stage.inputs.get("operation")
    if operation is not None:
        return str(operation)
    return "human_decision"


def _human_gate_allowed_decisions(stage: StageDef) -> tuple[str, ...]:
    configured = stage.surface.get("allowed_decisions", stage.inputs.get("allowed_decisions"))
    if configured is not None:
        return _string_tuple(configured)
    if stage.outcomes:
        return tuple(stage.outcomes)
    return (
        ApprovalDecision.APPROVED.value,
        ApprovalDecision.REJECTED.value,
        ApprovalDecision.REVISE.value,
        ApprovalDecision.PARK.value,
    )


def _human_gate_evidence_refs(stage: StageDef, gate: Any | None) -> tuple[str, ...]:
    configured = stage.surface.get("evidence_refs", stage.inputs.get("evidence_refs"))
    if configured is not None:
        return _string_tuple(configured)
    if gate is not None:
        return _string_tuple(getattr(gate, "evidence_refs", ()))
    return ()


def _human_ref(stage: StageDef) -> str:
    configured = stage.surface.get("human_ref", stage.inputs.get("human_ref"))
    if configured is not None:
        return str(configured)
    return _actor_ref(stage) or "human"


def _outcome_for_stage_result(stage: StageDef, adapter_result: AdapterResult | None) -> str:
    if adapter_result is not None:
        outcome = adapter_result.outputs.get("outcome")
        if isinstance(outcome, str) and outcome:
            return outcome
        if adapter_result.next_hint in stage.outcomes:
            return str(adapter_result.next_hint)
        if adapter_result.status in stage.outcomes:
            return adapter_result.status
    if len(stage.outcomes) == 1:
        return stage.outcomes[0]
    return "succeeded"


def _workflow_status_for_terminal(terminal: str) -> WorkflowStatus:
    mapping = {
        "done": WorkflowStatus.DONE,
        "blocked": WorkflowStatus.BLOCKED,
        "policy_denied": WorkflowStatus.POLICY_DENIED,
        "waiting_on_schedule": WorkflowStatus.WAITING_ON_SCHEDULE,
        "final_approval_required": WorkflowStatus.FINAL_APPROVAL_REQUIRED,
        "cancelled": WorkflowStatus.CANCELLED,
    }
    return mapping.get(terminal, WorkflowStatus.BLOCKED)


def _side_effect_scope(
    registration: AdapterRegistration,
    stage: StageDef,
    operation: str,
    gate: Any,
) -> dict[str, Any]:
    return {
        "workflow_adapter_ref": stage.adapter,
        "adapter_id": registration.adapter_id,
        "adapter_family": registration.family.value,
        "operation": operation,
        "stage_id": stage.id,
        "stage_type": stage.type.value,
        "side_effects": [risk.value for risk in registration.side_effects],
        "replay_safe": registration.replay_safe,
        "requires_idempotency_key": registration.requires_idempotency_key,
        "policy_decision": getattr(gate, "decision", None),
        "policy_reason": getattr(gate, "decision_reason", None),
    }


def _stage_output_contract_errors(stage: StageDef, adapter_result: AdapterResult) -> list[str]:
    errors: list[str] = []
    required_artifact_roles = _required_artifact_roles(stage.outputs)
    produced_roles = {artifact.role for artifact in adapter_result.artifact_refs}
    for role in required_artifact_roles:
        if role not in produced_roles:
            errors.append(f"missing required artifact role {role!r}")
    for artifact in adapter_result.artifact_refs:
        if artifact.role in required_artifact_roles:
            if not artifact.uri:
                errors.append(f"required artifact role {artifact.role!r} is missing a uri")
            if not artifact.content_hash:
                errors.append(f"required artifact role {artifact.role!r} is missing a content_hash")

    for field_path in _required_output_fields(stage.outputs):
        if not _output_path_exists(adapter_result.outputs, field_path):
            errors.append(f"missing required output field {field_path!r}")
    return errors


def _required_artifact_roles(outputs: Mapping[str, Any]) -> tuple[str, ...]:
    artifacts = outputs.get("artifacts") or ()
    roles: list[str] = []
    if isinstance(artifacts, Mapping):
        artifacts = artifacts.values()
    for index, artifact in enumerate(artifacts, start=1):
        if not isinstance(artifact, Mapping):
            continue
        if bool(artifact.get("required", False)):
            roles.append(str(artifact.get("role") or f"artifact_{index}"))
    return tuple(roles)


def _required_output_fields(outputs: Mapping[str, Any]) -> tuple[str, ...]:
    fields: list[str] = []
    for key in ("required_fields", "required_outputs"):
        configured = outputs.get(key)
        if configured is not None:
            fields.extend(_string_tuple(configured))

    field_specs = outputs.get("fields")
    if isinstance(field_specs, Mapping):
        field_specs = [
            {"name": name, **(spec if isinstance(spec, Mapping) else {})}
            for name, spec in field_specs.items()
        ]
    if isinstance(field_specs, (list, tuple)):
        for spec in field_specs:
            if isinstance(spec, Mapping) and bool(spec.get("required", False)):
                name = spec.get("name") or spec.get("field")
                if name:
                    fields.append(str(name))

    schema = outputs.get("outcome_schema")
    if isinstance(schema, Mapping):
        fields.extend(_string_tuple(schema.get("required")))
    return tuple(dict.fromkeys(fields))


def _output_path_exists(outputs: Mapping[str, Any], field_path: str) -> bool:
    current: Any = outputs
    for part in field_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return current not in (None, "")


def _retry_result_for_adapter_failure(
    *,
    stage: StageDef,
    run: StageRun,
    registration: AdapterRegistration,
    adapter_result: AdapterResult,
    created_at: str,
) -> RunnerResult | None:
    if not _retry_enabled(stage.retry):
        return None
    max_attempts = _retry_max_attempts(stage.retry)
    if run.attempt >= max_attempts:
        return None
    if not _retry_is_safe(registration, run):
        return RunnerResult(
            decision="blocked",
            failure_class=FailureClass.UNKNOWN_SIDE_EFFECT_STATE,
            failure_summary=(
                f"Adapter returned {adapter_result.status}; retry policy is configured, "
                "but replay is not proven safe for this adapter side-effect scope."
            ),
            approval_required=True,
        )
    return RunnerResult(
        decision="retry",
        failure_class=FailureClass.RUNTIME_FAILURE,
        failure_summary=f"Adapter returned {adapter_result.status}; queued append-only retry.",
        retry_after_at=_retry_after_at(created_at, stage.retry),
    )


def _retry_enabled(retry: Mapping[str, Any]) -> bool:
    if bool(retry.get("enabled", False)):
        return True
    return _retry_max_attempts(retry) > 1


def _retry_max_attempts(retry: Mapping[str, Any]) -> int:
    try:
        return max(1, int(retry.get("max_attempts", 1)))
    except (TypeError, ValueError):
        return 1


def _retry_is_safe(registration: AdapterRegistration, run: StageRun) -> bool:
    safe_side_effects = {
        RiskClass.READ_ONLY,
        RiskClass.LOCAL_DRAFT,
        RiskClass.REVIEW_ONLY,
        RiskClass.INTERNAL_STATE,
    }
    if registration.replay_safe:
        return True
    if all(effect in safe_side_effects for effect in registration.side_effects):
        return True
    return bool(run.idempotency_key) and not registration.requires_idempotency_key


def _retry_after_at(created_at: str, retry: Mapping[str, Any]) -> str:
    try:
        backoff_seconds = int(retry.get("backoff_seconds", 0))
    except (TypeError, ValueError):
        backoff_seconds = 0
    base = _coerce_datetime(created_at) or datetime.now(UTC)
    return iso_timestamp(base + timedelta(seconds=max(0, backoff_seconds)))


def _human_decision_validation_error(
    decision: HumanApprovalReceipt | None,
    gate: Any,
    *,
    now: Any,
    allowed_decisions: tuple[str, ...] = (),
) -> str | None:
    if decision is None:
        return "Missing human decision receipt."
    decision_text = _decision_text(decision.decision)
    if not decision.approval_id:
        return "Human decision receipt is missing an approval_id."
    if not decision.human_ref:
        return "Human decision receipt is missing a human_ref."
    if not decision.canonical_surface:
        return "Human decision receipt is missing a canonical_surface."
    if decision_text not in _KNOWN_HUMAN_DECISIONS and decision_text not in allowed_decisions:
        return f"Unsupported human decision {decision_text!r}."
    if decision.gate_id != gate.gate_id:
        return "Human decision receipt does not match the waiting gate."
    if decision.exact_action_approved != gate.requested_action:
        return "Human decision receipt does not name the exact waiting action."
    if decision.action_fingerprint != gate.action_fingerprint:
        return "Human decision receipt fingerprint does not match the waiting gate."
    current_time = _coerce_datetime(now) or datetime.now(UTC)
    revoked_at = _coerce_datetime(decision.revoked_at)
    if revoked_at is not None and revoked_at <= current_time:
        return "Human decision receipt has been revoked."
    expires_at = _coerce_datetime(decision.expires_at)
    if expires_at is not None and expires_at <= current_time:
        return "Human decision receipt has expired."
    return None


def _human_decision_outcome_candidates(decision_text: str) -> tuple[str, ...]:
    aliases = {
        "approved": ("approved", "approval_granted", "read_clear", "clear", "done", "succeeded"),
        "approve": ("approve", "approved", "approval_granted", "read_clear", "clear", "done", "succeeded"),
        "approval_granted": ("approval_granted", "approved", "read_clear", "clear", "done", "succeeded"),
        "read_clear": ("read_clear", "approved", "approval_granted", "clear", "done", "succeeded"),
        "clear": ("clear", "read_clear", "approved", "approval_granted", "done", "succeeded"),
        "rejected": ("rejected", "reject", "denied", "approval_denied", "blocked"),
        "reject": ("reject", "rejected", "denied", "approval_denied", "blocked"),
        "denied": ("denied", "rejected", "reject", "approval_denied", "blocked"),
        "revise": ("revise", "revision_requested", "revise_plan", "needs_revision"),
        "revision_requested": ("revision_requested", "revise", "revise_plan", "needs_revision"),
        "park": ("park", "parked", "defer", "blocked"),
        "parked": ("parked", "park", "defer", "blocked"),
        "defer": ("defer", "park", "parked", "blocked"),
        "blocked": ("blocked", "park", "defer"),
    }
    return aliases.get(decision_text, (decision_text,))


def _decision_text(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_KNOWN_HUMAN_DECISIONS = frozenset(
    {
        ApprovalDecision.APPROVED.value,
        ApprovalDecision.REJECTED.value,
        ApprovalDecision.REVISE.value,
        ApprovalDecision.PARK.value,
        "approve",
        "approval_granted",
        "read_clear",
        "clear",
        "reject",
        "denied",
        "approval_denied",
        "revision_requested",
        "revise_plan",
        "needs_revision",
        "parked",
        "defer",
        "blocked",
    }
)


def _runtime_input(
    workflow: WorkflowDef,
    stage: StageDef,
    run: StageRun,
    rendered_context: RenderedContext | None = None,
) -> dict[str, Any]:
    payload = {
        "workflow": {"id": workflow.id, "version": workflow.version},
        "stage": to_plain_data(stage),
        "stage_run": to_plain_data(run),
    }
    if rendered_context is not None:
        payload["context_packet"] = rendered_context.packet_data
        payload["rendered_input"] = rendered_context.rendered_input
        payload["rendered_input_digest"] = rendered_context.rendered_input_digest
    return payload


def _actor_ref(stage: StageDef) -> str | None:
    if not stage.actors:
        return None
    return str(stage.actors[next(iter(stage.actors))])


def _surface_ref_from_outputs(outputs: Mapping[str, Any]) -> Mapping[str, Any] | None:
    surface_ref = outputs.get("surface_ref")
    if isinstance(surface_ref, Mapping):
        return dict(surface_ref)
    return None


def _surface_external_ref(outputs: Mapping[str, Any]) -> str | None:
    surface_ref = _surface_ref_from_outputs(outputs)
    if surface_ref is None:
        return None
    for key in ("external_id", "surface_id", "note_path"):
        value = surface_ref.get(key)
        if value:
            return str(value)
    return None


def _receipt_outputs(receipt: Receipt) -> Mapping[str, Any]:
    outputs = receipt.runtime_provenance.get("outputs", {})
    return outputs if isinstance(outputs, Mapping) else {}


def _is_surface_decision_receipt(receipt: Receipt) -> bool:
    outputs = _receipt_outputs(receipt)
    decision = outputs.get("decision")
    return decision is not None and str(decision).strip() != ""


def _surface_ingest_failure_summary(
    decision_receipts: tuple[Receipt, ...],
    candidate_receipts: tuple[Receipt, ...],
) -> str:
    if not decision_receipts:
        return "Surface adapter returned no human decision receipts."
    if len(candidate_receipts) != 1:
        blocked = next((receipt for receipt in decision_receipts if receipt.status != ADAPTER_STATUS_SUCCEEDED), None)
        if blocked is not None:
            return blocked.summary
        return (
            "Surface decision ingest must return exactly one structured human decision "
            f"receipt; got {len(candidate_receipts)}."
        )
    if candidate_receipts[0].status != ADAPTER_STATUS_SUCCEEDED:
        return candidate_receipts[0].summary
    return ""


def _human_approval_from_surface_receipt(
    receipt: Receipt,
    *,
    gate: Any,
    surface_adapter_id: str,
) -> tuple[HumanApprovalReceipt | None, str | None]:
    outputs = _receipt_outputs(receipt)
    required_fields = (
        "gate_id",
        "human_ref",
        "canonical_surface",
        "decision",
        "exact_action_approved",
        "action_fingerprint",
    )
    missing = tuple(field for field in required_fields if not str(outputs.get(field, "")).strip())
    if missing:
        return None, (
            "Surface decision receipt is missing required approval fields: "
            + ", ".join(missing)
        )
    constraints = {
        "surface_adapter_id": surface_adapter_id,
        "surface_receipt_ref": receipt.receipt_id,
    }
    for flag in ("test_only", "non_live"):
        if flag in outputs:
            constraints[flag] = bool(outputs[flag])
    return (
        HumanApprovalReceipt(
            approval_id=str(outputs.get("approval_id") or receipt.receipt_id),
            gate_id=str(outputs["gate_id"]),
            human_ref=str(outputs["human_ref"]),
            canonical_surface=str(outputs["canonical_surface"]),
            decision=_approval_decision_value(outputs["decision"]),
            exact_action_approved=str(outputs["exact_action_approved"]),
            action_fingerprint=str(outputs["action_fingerprint"]),
            evidence_refs=_string_tuple(outputs.get("evidence_refs", ())),
            constraints=constraints,
            created_at=receipt.created_at,
            transcript_or_message_ref=str(
                outputs.get("transcript_or_message_ref")
                or outputs.get("source_note_path")
                or receipt.receipt_id
            ),
        ),
        None,
    )


def _approval_decision_value(value: Any) -> ApprovalDecision | str:
    text = _decision_text(value)
    try:
        return ApprovalDecision(text)
    except ValueError:
        return text


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _make_kernel_adapter_receipt(
    invocation: AdapterInvocation,
    *,
    rendered_context: RenderedContext | None,
    **kwargs: Any,
):
    receipt = make_adapter_receipt(invocation, **kwargs)
    if rendered_context is None:
        return receipt
    return replace(
        receipt,
        context_packet_ref=rendered_context.packet.context_id,
        prompt_provenance=build_prompt_provenance(rendered_context),
    )


def _prior_receipts(ledger: WorkflowLedger, instance_id: str) -> tuple[Mapping[str, Any], ...]:
    rows = ledger.connection.execute(
        """
        SELECT receipt_json FROM receipts
        WHERE instance_id = ?
        ORDER BY created_at, receipt_id
        """,
        (instance_id,),
    ).fetchall()
    receipts = []
    for row in rows:
        try:
            receipts.append(json.loads(row["receipt_json"]))
        except (TypeError, json.JSONDecodeError):
            receipts.append({"unparseable_receipt": True})
    return tuple(receipts)


__all__ = [
    "HumanGateSurfaceResult",
    "KernelDecisionResult",
    "KernelRuntimeConfig",
    "KernelStep",
    "WorkflowKernel",
]
