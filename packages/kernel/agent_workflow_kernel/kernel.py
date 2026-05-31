"""Initial generic workflow kernel facade."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
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
    RiskClass,
    StageDef,
    StageRun,
    StageRunStatus,
    StageType,
    WorkflowDef,
    WorkflowInstance,
    WorkflowStatus,
    to_plain_data,
)
from .policy import ActionRequest, GateDecision, PolicyEngine
from .prompts import digest_data
from .runner import RunnerResult, WorkflowRunner
from .storage import WorkflowLedger, iso_timestamp


KernelDecision = Literal["idle", "succeeded", "failed", "blocked"]


@dataclass(frozen=True, slots=True)
class KernelRuntimeConfig:
    """Runtime dependencies for a portable kernel instance."""

    owner_id: str
    adapter_registry: AdapterRegistry
    policy_engine: PolicyEngine = field(default_factory=PolicyEngine)
    default_lease_seconds: int = 300


@dataclass(frozen=True, slots=True)
class KernelStep:
    stage_run: StageRun | None
    decision: KernelDecision
    adapter_result: AdapterResult | None = None
    receipt_id: str | None = None
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
            self.ledger.insert_workflow_instance(instance, created_at=created_at)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"workflow instance already exists: {instance_id}") from exc
        self._queue_stage(
            first_stage,
            instance_id=instance_id,
            attempt=1,
            inputs=inputs,
            created_at=created_at,
        )
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
            self.ledger.update_workflow_instance(
                instance_id=step.stage_run.instance_id,
                status=WorkflowStatus.RUNNING,
                current_stage_id=stage.id,
                updated_at=now,
                actor=self.config.owner_id,
                event_type="workflow_stage_succeeded",
                payload={"stage_id": stage.id, "stage_run_id": step.stage_run.stage_run_id},
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

        return KernelStep(
            stage_run=step.stage_run,
            decision=step.decision,
            adapter_result=state.get("adapter_result"),
            receipt_id=state.get("receipt_id"),
            failure_summary=state.get("failure_summary"),
        )

    def _handle_stage(self, state: dict[str, Any], *, now: Any):
        def handler(run: StageRun) -> RunnerResult:
            stage = self._stage_by_id.get(run.stage_id)
            created_at = iso_timestamp(now)
            if stage is None:
                return self._blocked(state, "Unknown workflow stage.", FailureClass.DOMAIN_BLOCKED)
            if stage.type == StageType.HUMAN_GATE:
                return self._blocked(
                    state,
                    "Human gate reached; explicit decision ingestion is not implemented in this slice.",
                    FailureClass.DOMAIN_BLOCKED,
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

            invocation = AdapterInvocation(
                invocation_id=f"kernel:{run.stage_run_id}:{uuid.uuid4().hex[:12]}",
                workflow_id=self.workflow.id,
                instance_id=run.instance_id,
                stage_run_id=run.stage_run_id,
                adapter_family=registration.family,
                adapter_id=registration.adapter_id,
                operation=operation,
                input_ref=f"stage:{stage.id}:input",
                context_packet_ref=None,
                idempotency_key=f"{run.instance_id}:{stage.id}:{run.attempt}",
            )
            runtime_input = _runtime_input(self.workflow, stage, run)
            request_hash = digest_data(
                {
                    "invocation": invocation,
                    "runtime_input": runtime_input,
                    "policy_gate": gate,
                }
            )
            adapter_result = registration.adapter.invoke(invocation, runtime_input)
            response_hash = digest_data(adapter_result)
            self.ledger.record_adapter_invocation(
                invocation,
                status=adapter_result.status,
                request_hash=request_hash,
                response_hash=response_hash,
                started_at=created_at,
                completed_at=created_at,
            )
            receipt = make_adapter_receipt(
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
            )
            state["adapter_result"] = adapter_result
            state["receipt_id"] = receipt.receipt_id
            if adapter_result.status == ADAPTER_STATUS_SUCCEEDED:
                return RunnerResult(
                    decision="succeeded",
                    receipt=receipt,
                    output_hash=response_hash,
                )
            return RunnerResult(
                decision="blocked",
                receipt=receipt,
                output_hash=response_hash,
                failure_class=FailureClass.DOMAIN_BLOCKED,
                failure_summary=f"Adapter returned {adapter_result.status}.",
            )

        return handler

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


def _runtime_input(workflow: WorkflowDef, stage: StageDef, run: StageRun) -> dict[str, Any]:
    return {
        "workflow": {"id": workflow.id, "version": workflow.version},
        "stage": to_plain_data(stage),
        "stage_run": to_plain_data(run),
    }


def _actor_ref(stage: StageDef) -> str | None:
    if not stage.actors:
        return None
    return str(stage.actors[next(iter(stage.actors))])


__all__ = ["KernelRuntimeConfig", "KernelStep", "WorkflowKernel"]
