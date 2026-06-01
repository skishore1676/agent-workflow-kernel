"""Adapter-neutral runner skeleton for workflow stage runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Literal, Mapping, Protocol

from .contracts import FailureClass, Receipt, StageRun, StageRunStatus, WorkflowStatus
from .storage import WorkflowLedger


RunDecision = Literal["succeeded", "failed", "retry", "blocked", "waiting_on_human"]
OwnedRunStatus = Literal["idle", "waiting_on_human", "blocked", "done", "max_steps"]


@dataclass(slots=True, frozen=True)
class RunnerResult:
    decision: RunDecision
    receipt: Receipt | None = None
    output_hash: str | None = None
    failure_class: FailureClass | str | None = None
    failure_summary: str | None = None
    retry_after_at: datetime | str | None = None
    approval_required: bool = False


@dataclass(slots=True, frozen=True)
class RunnerStep:
    stage_run: StageRun | None
    decision: RunDecision | Literal["idle"]


@dataclass(slots=True, frozen=True)
class OwnedRunSummary:
    """Summary of a runner-owned kernel execution pass."""

    status: OwnedRunStatus
    instance_id: str | None
    stop_reason: str
    kernel_steps: tuple[Any, ...] = field(default_factory=tuple)
    surface_results: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def stages_run(self) -> int:
        return len(self.kernel_steps)


StageHandler = Callable[[StageRun], RunnerResult]


class KernelExecutionFacade(Protocol):
    """Subset of WorkflowKernel used by the owned runner loop."""

    ledger: WorkflowLedger
    workflow: Any

    def run_once(self, *, now: datetime | str | None = None) -> Any: ...

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
        now: datetime | str | None = None,
    ) -> Any: ...

    def readback_human_gate_surface(
        self,
        *,
        instance_id: str,
        surface_ref: Mapping[str, Any] | None = None,
        surface_adapter_ref: str | None = None,
        now: datetime | str | None = None,
    ) -> Any: ...

    def ingest_human_gate_surface_decision(
        self,
        *,
        instance_id: str,
        surface_ref: Mapping[str, Any] | None = None,
        surface_adapter_ref: str | None = None,
        allowed_decisions: tuple[str, ...] | None = None,
        evidence_refs: tuple[str, ...] | None = None,
        human_ref: str | None = None,
        now: datetime | str | None = None,
    ) -> Any: ...


class WorkflowRunner:
    """Small runner loop around the ledger and an injected stage handler.

    The handler is the adapter boundary. This runner does not call host,
    browser, shell, OpenClaw, Telegram, Obsidian, or remote APIs.
    """

    def __init__(self, ledger: WorkflowLedger, *, owner_id: str):
        self.ledger = ledger
        self.owner_id = owner_id

    def run_once(
        self,
        handler: StageHandler,
        *,
        lease_seconds: int = 300,
        now: datetime | str | None = None,
    ) -> RunnerStep:
        self.ledger.sweep_stale_leases(now=now, actor=self.owner_id)
        run = self.ledger.claim_next_queued_run(
            owner_id=self.owner_id, lease_seconds=lease_seconds, now=now
        )
        if run is None:
            return RunnerStep(stage_run=None, decision="idle")
        if run.lease_token is None:
            raise RuntimeError(f"claimed stage run {run.stage_run_id!r} has no lease token")
        self.ledger.mark_stage_run_started(
            stage_run_id=run.stage_run_id,
            lease_token=run.lease_token,
            actor=self.owner_id,
            idempotency_key=run.idempotency_key,
            side_effect_scope={
                "boundary": "stage_handler",
                "adapter_invocation_may_start": True,
                "adapter_id": run.adapter_id,
                "stage_id": run.stage_id,
            },
            now=now,
        )
        refreshed_run = self.ledger.get_stage_run(run.stage_run_id)
        if refreshed_run is not None:
            run = refreshed_run

        try:
            result = handler(run)
        except Exception as exc:
            self.ledger.fail_stage_run(
                stage_run_id=run.stage_run_id,
                lease_token=run.lease_token,
                failure_class=FailureClass.RUNTIME_FAILURE,
                failure_summary=str(exc),
                now=now,
                actor=self.owner_id,
            )
            return RunnerStep(stage_run=run, decision="failed")

        if result.receipt is not None:
            self.ledger.record_receipt(result.receipt)

        if result.decision == "succeeded":
            self.ledger.complete_stage_run(
                stage_run_id=run.stage_run_id,
                lease_token=run.lease_token,
                receipt_id=result.receipt.receipt_id if result.receipt else None,
                output_hash=result.output_hash,
                now=now,
                actor=self.owner_id,
            )
        elif result.decision == "retry":
            if result.retry_after_at is None:
                raise ValueError("retry decisions must include retry_after_at")
            self.ledger.schedule_retry(
                stage_run_id=run.stage_run_id,
                lease_token=run.lease_token,
                failure_class=result.failure_class or FailureClass.RUNTIME_FAILURE,
                failure_summary=result.failure_summary or "Stage scheduled for retry.",
                retry_after_at=result.retry_after_at,
                now=now,
                actor=self.owner_id,
            )
        elif result.decision == "blocked":
            self.ledger.block_stage_run(
                stage_run_id=run.stage_run_id,
                lease_token=run.lease_token,
                failure_class=result.failure_class or FailureClass.DOMAIN_BLOCKED,
                failure_summary=result.failure_summary or "Stage blocked by handler.",
                approval_required=result.approval_required,
                now=now,
                actor=self.owner_id,
            )
        elif result.decision == "waiting_on_human":
            self.ledger.wait_stage_run_for_human_decision(
                stage_run_id=run.stage_run_id,
                lease_token=run.lease_token,
                failure_class=result.failure_class or FailureClass.DOMAIN_BLOCKED,
                failure_summary=result.failure_summary or "Stage is waiting on a human decision.",
                now=now,
                actor=self.owner_id,
            )
        elif result.decision == "failed":
            self.ledger.fail_stage_run(
                stage_run_id=run.stage_run_id,
                lease_token=run.lease_token,
                failure_class=result.failure_class or FailureClass.RUNTIME_FAILURE,
                failure_summary=result.failure_summary or "Stage failed.",
                status=_status_for_failure(result.failure_class),
                now=now,
                actor=self.owner_id,
            )
        else:
            raise ValueError(f"unknown runner decision: {result.decision!r}")

        return RunnerStep(stage_run=run, decision=result.decision)

    def run_kernel_until_idle(
        self,
        kernel: KernelExecutionFacade,
        *,
        instance_id: str | None = None,
        max_steps: int = 50,
        publish_human_gate: bool = False,
        ingest_human_decision: bool = False,
        surface_adapter_ref: str | None = None,
        allowed_decisions: tuple[str, ...] | None = None,
        evidence_refs: tuple[str, ...] | None = None,
        title: str | None = None,
        human_ask: str | None = None,
        human_ref: str | None = None,
        test_only: bool = True,
        non_live: bool = True,
        now: datetime | str | None = None,
    ) -> OwnedRunSummary:
        """Drive a WorkflowKernel until no automatic work remains.

        This is the owned runner path: it discovers queued or waiting work from
        the ledger, lets ``WorkflowKernel`` execute stage semantics, and uses the
        kernel's human-gate surface lifecycle for publish/readback/ingest. A
        previously published waiting-gate surface is reused on rerun so recovery
        after an interruption does not create duplicate local review notes.
        """

        if kernel.ledger is not self.ledger:
            raise ValueError("WorkflowRunner and WorkflowKernel must share the same ledger")

        resolved_instance_id = instance_id or self._next_owned_instance_id(
            kernel,
            include_waiting_human=publish_human_gate or ingest_human_decision,
            now=now,
        )
        if resolved_instance_id is None:
            return OwnedRunSummary(status="idle", instance_id=None, stop_reason="no_work")

        kernel_steps: list[Any] = []
        surface_results: list[Any] = []

        for _ in range(max_steps):
            waiting_run = self.ledger.find_waiting_human_stage_run(
                instance_id=resolved_instance_id
            )
            if waiting_run is not None:
                surface_ref = _latest_successful_surface_ref(
                    self.ledger,
                    stage_run_id=waiting_run.stage_run_id,
                )
                if publish_human_gate and surface_ref is None:
                    publish = kernel.publish_waiting_human_gate(
                        instance_id=resolved_instance_id,
                        surface_adapter_ref=surface_adapter_ref,
                        allowed_decisions=allowed_decisions,
                        evidence_refs=evidence_refs,
                        title=title,
                        human_ask=human_ask,
                        human_ref=human_ref,
                        test_only=test_only,
                        non_live=non_live,
                        now=now,
                    )
                    surface_results.append(publish)
                    surface_ref = getattr(publish, "surface_ref", None)
                    if getattr(publish, "status", None) != "succeeded":
                        return OwnedRunSummary(
                            status="blocked",
                            instance_id=resolved_instance_id,
                            stop_reason="human_gate_publish_blocked",
                            kernel_steps=tuple(kernel_steps),
                            surface_results=tuple(surface_results),
                        )

                if publish_human_gate and surface_ref is not None:
                    readback = kernel.readback_human_gate_surface(
                        instance_id=resolved_instance_id,
                        surface_ref=surface_ref,
                        surface_adapter_ref=surface_adapter_ref,
                        now=now,
                    )
                    surface_results.append(readback)
                    if getattr(readback, "status", None) != "succeeded":
                        return OwnedRunSummary(
                            status="blocked",
                            instance_id=resolved_instance_id,
                            stop_reason="human_gate_readback_blocked",
                            kernel_steps=tuple(kernel_steps),
                            surface_results=tuple(surface_results),
                        )

                if ingest_human_decision:
                    if surface_ref is None:
                        return OwnedRunSummary(
                            status="blocked",
                            instance_id=resolved_instance_id,
                            stop_reason="human_gate_surface_missing",
                            kernel_steps=tuple(kernel_steps),
                            surface_results=tuple(surface_results),
                        )
                    ingest = kernel.ingest_human_gate_surface_decision(
                        instance_id=resolved_instance_id,
                        surface_ref=surface_ref,
                        surface_adapter_ref=surface_adapter_ref,
                        allowed_decisions=allowed_decisions,
                        evidence_refs=evidence_refs,
                        human_ref=human_ref,
                        now=now,
                    )
                    surface_results.append(ingest)
                    if getattr(ingest, "status", None) != "succeeded":
                        return OwnedRunSummary(
                            status="blocked",
                            instance_id=resolved_instance_id,
                            stop_reason="human_gate_decision_blocked",
                            kernel_steps=tuple(kernel_steps),
                            surface_results=tuple(surface_results),
                        )
                    continue

                return OwnedRunSummary(
                    status="waiting_on_human",
                    instance_id=resolved_instance_id,
                    stop_reason="waiting_on_human",
                    kernel_steps=tuple(kernel_steps),
                    surface_results=tuple(surface_results),
                )

            step = kernel.run_once(now=now)
            if getattr(step, "decision", None) == "idle" or getattr(step, "stage_run", None) is None:
                status, stop_reason = self._idle_status(resolved_instance_id)
                return OwnedRunSummary(
                    status=status,
                    instance_id=resolved_instance_id,
                    stop_reason=stop_reason,
                    kernel_steps=tuple(kernel_steps),
                    surface_results=tuple(surface_results),
                )

            kernel_steps.append(step)
            decision = getattr(step, "decision", None)
            if decision in {"blocked", "failed"}:
                return OwnedRunSummary(
                    status="blocked",
                    instance_id=resolved_instance_id,
                    stop_reason=str(decision),
                    kernel_steps=tuple(kernel_steps),
                    surface_results=tuple(surface_results),
                )

        return OwnedRunSummary(
            status="max_steps",
            instance_id=resolved_instance_id,
            stop_reason="max_steps_exceeded",
            kernel_steps=tuple(kernel_steps),
            surface_results=tuple(surface_results),
        )

    def _next_owned_instance_id(
        self,
        kernel: KernelExecutionFacade,
        *,
        include_waiting_human: bool,
        now: datetime | str | None,
    ) -> str | None:
        instance = self.ledger.find_next_workflow_instance_for_work(
            workflow_def_id=str(kernel.workflow.id),
            workflow_version=str(kernel.workflow.version),
            include_waiting_human=include_waiting_human,
            now=now,
        )
        return instance.instance_id if instance is not None else None

    def _idle_status(self, instance_id: str) -> tuple[OwnedRunStatus, str]:
        instance = self.ledger.get_workflow_instance(instance_id)
        if instance is None:
            return "idle", "instance_missing"
        if instance.status == WorkflowStatus.WAITING_ON_HUMAN:
            return "waiting_on_human", "waiting_on_human"
        if instance.status in {
            WorkflowStatus.BLOCKED,
            WorkflowStatus.POLICY_DENIED,
            WorkflowStatus.FINAL_APPROVAL_REQUIRED,
            WorkflowStatus.CANCELLED,
        }:
            return "blocked", instance.status.value
        if instance.status == WorkflowStatus.DONE:
            return "done", "terminal"
        return "idle", "no_queued_work"


def _status_for_failure(failure_class: FailureClass | str | None) -> StageRunStatus:
    if failure_class == FailureClass.INVALID_OUTPUT or failure_class == "invalid_output":
        return StageRunStatus.INVALID_OUTPUT
    return StageRunStatus.FAILED


def _latest_successful_surface_ref(
    ledger: WorkflowLedger,
    *,
    stage_run_id: str,
) -> Mapping[str, Any] | None:
    for event in reversed(ledger.list_events(stage_run_id=stage_run_id)):
        if event["event_type"] != "human_gate_surface_published":
            continue
        payload = event["payload"]
        candidate = payload.get("surface_ref")
        if payload.get("status") == "succeeded" and isinstance(candidate, Mapping):
            return dict(candidate)
    return None


__all__ = [
    "OwnedRunSummary",
    "RunnerResult",
    "RunnerStep",
    "StageHandler",
    "WorkflowRunner",
]
