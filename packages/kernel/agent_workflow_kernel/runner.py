"""Adapter-neutral runner skeleton for workflow stage runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal

from .contracts import FailureClass, Receipt, StageRun, StageRunStatus
from .storage import WorkflowLedger


RunDecision = Literal["succeeded", "failed", "retry", "blocked", "waiting_on_human"]


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


StageHandler = Callable[[StageRun], RunnerResult]


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


def _status_for_failure(failure_class: FailureClass | str | None) -> StageRunStatus:
    if failure_class == FailureClass.INVALID_OUTPUT or failure_class == "invalid_output":
        return StageRunStatus.INVALID_OUTPUT
    return StageRunStatus.FAILED


__all__ = ["RunnerResult", "RunnerStep", "StageHandler", "WorkflowRunner"]
