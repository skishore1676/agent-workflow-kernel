"""Initial generic workflow kernel facade."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field, replace
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
    WorkflowDef,
    WorkflowInstance,
    WorkflowStatus,
    to_plain_data,
)
from .policy import ActionRequest, GateDecision, PolicyEngine
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


KernelDecision = Literal["idle", "succeeded", "failed", "blocked"]


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
                idempotency_key=f"{run.instance_id}:{stage.id}:{run.attempt}",
            )
            runtime_input = _runtime_input(self.workflow, stage, run, rendered_context)
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
                "workflow": self._instance_inputs.get(run.instance_id, {}),
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


__all__ = ["KernelRuntimeConfig", "KernelStep", "WorkflowKernel"]
