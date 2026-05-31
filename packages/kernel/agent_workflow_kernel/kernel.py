"""Initial generic workflow kernel facade."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    Transition,
    WorkflowDef,
    WorkflowInstance,
    WorkflowStatus,
    to_plain_data,
)
from .policy import ActionRequest, ApprovalDecision, GateDecision, HumanApprovalReceipt, PolicyEngine
from .prompts import digest_data
from .runner import RunnerResult, WorkflowRunner
from .storage import WorkflowLedger, iso_timestamp


KernelDecision = Literal["idle", "succeeded", "failed", "blocked", "waiting_on_human"]
KernelTransitionDecision = Literal["queued", "terminal", "blocked"]


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


@dataclass(frozen=True, slots=True)
class KernelDecisionResult:
    stage_run: StageRun | None
    decision: KernelTransitionDecision
    outcome: str | None = None
    queued_stage_id: str | None = None
    terminal_status: WorkflowStatus | None = None
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
        validation_error = _human_decision_validation_error(decision, gate, now=now)
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

    def _handle_stage(self, state: dict[str, Any], *, now: Any):
        def handler(run: StageRun) -> RunnerResult:
            stage = self._stage_by_id.get(run.stage_id)
            created_at = iso_timestamp(now)
            if stage is None:
                return self._blocked(state, "Unknown workflow stage.", FailureClass.DOMAIN_BLOCKED)
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


def _human_decision_validation_error(
    decision: HumanApprovalReceipt | None,
    gate: Any,
    *,
    now: Any,
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
    if decision_text not in _KNOWN_HUMAN_DECISIONS:
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


__all__ = ["KernelDecisionResult", "KernelRuntimeConfig", "KernelStep", "WorkflowKernel"]
