"""Local workflow execution through deterministic fake adapters."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import ADAPTER_STATUS_SUCCEEDED
from .contracts import (
    AdapterFamily,
    AdapterInvocation,
    ArtifactRef,
    FailureClass,
    Receipt,
    StageDef,
    StageRun,
    StageRunStatus,
    StageType,
    WorkflowDef,
    WorkflowInstance,
    WorkflowStatus,
    to_plain_data,
)
from .dsl import canonical_json, load_workflow_file, workflow_to_canonical_json
from .local_adapters import (
    DETERMINISTIC_CREATED_AT,
    LocalFakeHostAdapter,
    LocalFakeLaneAdapter,
    LocalFakeRuntimeAdapter,
    LocalFakeSurfaceAdapter,
)
from .runner import RunnerResult, WorkflowRunner
from .storage import WorkflowLedger, iso_timestamp


LOCAL_RUNNER_ACTOR = "local-runner"


@dataclass(slots=True, frozen=True)
class LocalRunSummary:
    workflow_id: str
    workflow_version: str
    instance_id: str
    ledger_path: str
    status: str
    stop_reason: str
    current_stage_id: str | None
    stages_run: int
    receipts_written: int
    events_written: int
    terminal: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_plain_data(self)


@dataclass(slots=True)
class _StageOutcome:
    outcome: str
    output_hash: str
    stop_reason: str | None = None


class LocalWorkflowExecutor:
    """Run a workflow definition against only local deterministic adapters."""

    def __init__(
        self,
        workflow: WorkflowDef,
        ledger: WorkflowLedger,
        *,
        instance_id: str | None = None,
        created_at: str = DETERMINISTIC_CREATED_AT,
        max_steps: int = 50,
    ) -> None:
        self.workflow = workflow
        self.ledger = ledger
        self.instance_id = instance_id or f"local-{uuid.uuid4().hex}"
        self.created_at = created_at
        self.max_steps = max_steps
        self.runtime_adapter = LocalFakeRuntimeAdapter(created_at=created_at)
        self.surface_adapter = LocalFakeSurfaceAdapter(created_at=created_at)
        self.host_adapter = LocalFakeHostAdapter(created_at=created_at)
        self.lane_adapter = LocalFakeLaneAdapter(created_at=created_at)
        self._stage_by_id = {stage.id: stage for stage in workflow.stages}
        self._transitions = {
            (transition.from_stage, transition.on): transition
            for transition in workflow.transitions
        }
        self._outcomes: dict[str, _StageOutcome] = {}
        self._attempts_by_stage: dict[str, int] = {}

    def run(self) -> LocalRunSummary:
        self.ledger.initialize()
        first_stage = self._first_stage()
        input_hash = _digest_json(
            {
                "workflow": workflow_to_canonical_json(self.workflow),
                "local_inputs": {},
            }
        )
        self.ledger.insert_workflow_instance(
            WorkflowInstance(
                instance_id=self.instance_id,
                workflow_def_id=self.workflow.id,
                workflow_version=self.workflow.version,
                status=WorkflowStatus.RUNNING,
                current_stage_id=first_stage.id,
                input_hash=input_hash,
            ),
            created_at=self.created_at,
            input_snapshot={},
            workflow_definition_json=workflow_to_canonical_json(self.workflow),
            workflow_definition_hash=_digest_json(json.loads(workflow_to_canonical_json(self.workflow))),
            workflow_source_uri="local-runner",
        )
        self._append_workflow_event(
            "workflow_started",
            {"first_stage_id": first_stage.id, "input_hash": input_hash},
        )
        self._queue_stage(first_stage)

        runner = WorkflowRunner(self.ledger, owner_id=LOCAL_RUNNER_ACTOR)
        stages_run = 0
        stop_reason = "idle"
        terminal: str | None = None
        current_stage_id: str | None = first_stage.id
        status = WorkflowStatus.RUNNING

        while stages_run < self.max_steps:
            step = runner.run_once(self._handle_stage, now=self.created_at)
            if step.decision == "idle" or step.stage_run is None:
                stop_reason = "idle"
                break
            stages_run += 1
            stage_result = self._outcomes[step.stage_run.stage_run_id]
            current_stage_id = step.stage_run.stage_id

            if stage_result.stop_reason == "human_gate":
                status = WorkflowStatus.WAITING_ON_HUMAN
                stop_reason = "human_gate"
                self._update_instance(status=status, current_stage_id=current_stage_id)
                break

            if step.decision != "succeeded":
                status = WorkflowStatus.BLOCKED
                stop_reason = step.decision
                self._update_instance(status=status, current_stage_id=current_stage_id)
                break

            transition = self._transitions.get((step.stage_run.stage_id, stage_result.outcome))
            if transition is None:
                status = WorkflowStatus.BLOCKED
                stop_reason = "missing_transition"
                self._update_instance(status=status, current_stage_id=current_stage_id)
                self._append_workflow_event(
                    "workflow_blocked",
                    {
                        "stage_id": step.stage_run.stage_id,
                        "outcome": stage_result.outcome,
                        "reason": "missing_transition",
                    },
                    stage_run_id=step.stage_run.stage_run_id,
                )
                break

            if transition.terminal is not None:
                terminal = transition.terminal
                status = _workflow_status_for_terminal(transition.terminal)
                stop_reason = "terminal"
                current_stage_id = None
                self._update_instance(status=status, current_stage_id=None)
                self._append_workflow_event(
                    "workflow_terminal",
                    {
                        "from_stage": transition.from_stage,
                        "outcome": transition.on,
                        "terminal": transition.terminal,
                    },
                    stage_run_id=step.stage_run.stage_run_id,
                )
                break

            if transition.to_stage is None:
                status = WorkflowStatus.BLOCKED
                stop_reason = "invalid_transition"
                self._update_instance(status=status, current_stage_id=current_stage_id)
                break

            next_stage = self._stage_by_id[transition.to_stage]
            current_stage_id = next_stage.id
            self._queue_stage(next_stage)
            self._update_instance(status=WorkflowStatus.RUNNING, current_stage_id=next_stage.id)
            self._append_workflow_event(
                "workflow_transitioned",
                {
                    "from_stage": transition.from_stage,
                    "outcome": transition.on,
                    "to_stage": next_stage.id,
                },
                stage_run_id=step.stage_run.stage_run_id,
            )
        else:
            status = WorkflowStatus.BLOCKED
            stop_reason = "max_steps_exceeded"
            self._update_instance(status=status, current_stage_id=current_stage_id)

        return self._summary(
            status=status,
            stop_reason=stop_reason,
            current_stage_id=current_stage_id,
            stages_run=stages_run,
            terminal=terminal,
        )

    def _handle_stage(self, run: StageRun) -> RunnerResult:
        stage = self._stage_by_id[run.stage_id]
        outcome = _choose_local_outcome(stage)
        family = _adapter_family_for_stage(stage)
        invocation = AdapterInvocation(
            invocation_id=f"local:{self.instance_id}:{run.stage_id}:{run.attempt}",
            workflow_id=self.workflow.id,
            instance_id=self.instance_id,
            stage_run_id=run.stage_run_id,
            adapter_family=family,
            adapter_id=_local_adapter_id(family),
            operation=_operation_for_stage(stage, family),
            input_ref=f"stage:{stage.id}",
            idempotency_key=f"{self.instance_id}:{stage.id}:{run.attempt}",
        )
        request = {
            "workflow_id": self.workflow.id,
            "stage": to_plain_data(stage),
            "requested_adapter": stage.adapter,
            "local_outcome": outcome,
        }
        request_hash = _digest_json(request)
        self.ledger.record_adapter_invocation_started(
            invocation,
            request_hash=request_hash,
            actor=LOCAL_RUNNER_ACTOR,
            side_effect_scope={
                "adapter_family": family.value,
                "adapter_id": invocation.adapter_id,
                "operation": invocation.operation,
                "local_only": True,
                "stage_id": stage.id,
            },
            started_at=self.created_at,
        )
        adapter_outputs = self._invoke_local_adapter(invocation, stage, run, request)
        artifact_refs = _artifact_refs_for_stage(
            workflow_id=self.workflow.id,
            instance_id=self.instance_id,
            stage=stage,
            outcome=outcome,
        )
        response = {
            "adapter_outputs": adapter_outputs,
            "artifact_refs": to_plain_data(artifact_refs),
            "outcome": outcome,
        }
        response_hash = _digest_json(response)
        self.ledger.complete_adapter_invocation(
            invocation_id=invocation.invocation_id,
            status=ADAPTER_STATUS_SUCCEEDED,
            actor=LOCAL_RUNNER_ACTOR,
            response_hash=response_hash,
            completed_at=self.created_at,
        )

        is_human_gate = stage.type == StageType.HUMAN_GATE
        receipt = Receipt(
            receipt_id=f"receipt:{self.instance_id}:{run.stage_id}:{run.attempt}",
            kind=str(stage.outputs.get("receipt_kind") or f"local.{stage.type.value}"),
            workflow_id=self.workflow.id,
            instance_id=self.instance_id,
            stage_id=stage.id,
            stage_run_id=run.stage_run_id,
            status="approval_required" if is_human_gate else "succeeded",
            summary=_stage_summary(stage, outcome, is_human_gate=is_human_gate),
            created_at=self.created_at,
            artifact_refs=artifact_refs,
            runtime_provenance={
                "actor": LOCAL_RUNNER_ACTOR,
                "adapter_family": family.value,
                "adapter_id": invocation.adapter_id,
                "requested_adapter": stage.adapter,
                "invocation_id": invocation.invocation_id,
                "local_only": True,
            },
            policy_snapshot=dict(stage.policy),
            residual_risk="Requires human decision before advancing." if is_human_gate else None,
            next_action="Wait for explicit human decision." if is_human_gate else None,
        )
        self._outcomes[run.stage_run_id] = _StageOutcome(
            outcome=outcome,
            output_hash=response_hash,
            stop_reason="human_gate" if is_human_gate else None,
        )
        if is_human_gate:
            return RunnerResult(
                decision="blocked",
                receipt=receipt,
                output_hash=response_hash,
                failure_class=FailureClass.DOMAIN_BLOCKED,
                failure_summary="Local execution stopped at a human gate.",
                approval_required=True,
            )
        return RunnerResult(
            decision="succeeded",
            receipt=receipt,
            output_hash=response_hash,
        )

    def _invoke_local_adapter(
        self,
        invocation: AdapterInvocation,
        stage: StageDef,
        run: StageRun,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if invocation.adapter_family == AdapterFamily.RUNTIME:
            return self.runtime_adapter.invoke(invocation, request).outputs
        if invocation.adapter_family == AdapterFamily.SURFACE:
            return self.surface_adapter.publish(
                invocation,
                {
                    "title": f"Local gate: {stage.id}",
                    "stage_id": stage.id,
                    "requested_adapter": stage.adapter,
                    "readback_required": stage.type == StageType.HUMAN_GATE,
                    "allowed_decisions": list(stage.outcomes),
                },
            ).outputs
        if invocation.adapter_family == AdapterFamily.HOST:
            return {
                "host": to_plain_data(self.host_adapter.describe()),
                "resolved": dict(self.host_adapter.resolve(stage.adapter)),
            }
        return dict(self.lane_adapter.build_stage_input(run, request))

    def _first_stage(self) -> StageDef:
        if not self.workflow.stages:
            raise ValueError("workflow has no stages")
        return self.workflow.stages[0]

    def _queue_stage(self, stage: StageDef) -> None:
        attempt = self._attempts_by_stage.get(stage.id, 0) + 1
        self._attempts_by_stage[stage.id] = attempt
        run = StageRun(
            stage_run_id=f"{self.instance_id}:{stage.id}:{attempt}",
            instance_id=self.instance_id,
            stage_id=stage.id,
            status=StageRunStatus.QUEUED,
            attempt=attempt,
            adapter_id=stage.adapter,
            actor_ref=_actor_ref(stage),
        )
        self.ledger.insert_stage_run(
            run,
            input_hash=_digest_json({"stage": to_plain_data(stage), "attempt": attempt}),
            idempotency_key=f"{self.instance_id}:{stage.id}:{attempt}",
            created_at=self.created_at,
        )

    def _update_instance(
        self,
        *,
        status: WorkflowStatus,
        current_stage_id: str | None,
    ) -> None:
        updated_at = iso_timestamp(self.created_at)
        self.ledger.connection.execute(
            """
            UPDATE workflow_instances
            SET status = ?, current_stage_id = ?, updated_at = ?
            WHERE instance_id = ?
            """,
            (status.value, current_stage_id, updated_at, self.instance_id),
        )
        self.ledger.connection.commit()

    def _append_workflow_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        stage_run_id: str | None = None,
    ) -> None:
        self.ledger.connection.execute(
            """
            INSERT INTO events (
              event_id, instance_id, stage_run_id, event_type,
              actor, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                self.instance_id,
                stage_run_id,
                event_type,
                LOCAL_RUNNER_ACTOR,
                canonical_json(payload),
                iso_timestamp(self.created_at),
            ),
        )
        self.ledger.connection.commit()

    def _summary(
        self,
        *,
        status: WorkflowStatus,
        stop_reason: str,
        current_stage_id: str | None,
        stages_run: int,
        terminal: str | None,
    ) -> LocalRunSummary:
        receipt_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM receipts"
        ).fetchone()["count"]
        event_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM events"
        ).fetchone()["count"]
        return LocalRunSummary(
            workflow_id=self.workflow.id,
            workflow_version=self.workflow.version,
            instance_id=self.instance_id,
            ledger_path=self.ledger.database,
            status=status.value,
            stop_reason=stop_reason,
            current_stage_id=current_stage_id,
            stages_run=stages_run,
            receipts_written=int(receipt_count),
            events_written=int(event_count),
            terminal=terminal,
        )


def run_local_workflow(
    workflow_path: str | Path,
    *,
    ledger_path: str | Path | None = None,
    instance_id: str | None = None,
    max_steps: int = 50,
) -> LocalRunSummary:
    """Load and locally execute a workflow file, returning a JSON-safe summary."""

    workflow = load_workflow_file(workflow_path)
    database = Path(ledger_path) if ledger_path is not None else _temporary_ledger_path()
    ledger = WorkflowLedger(database)
    try:
        executor = LocalWorkflowExecutor(
            workflow,
            ledger,
            instance_id=instance_id,
            max_steps=max_steps,
        )
        return executor.run()
    finally:
        ledger.close()


def _temporary_ledger_path() -> Path:
    handle = tempfile.NamedTemporaryFile(
        prefix="agent-workflow-kernel-",
        suffix=".sqlite3",
        delete=False,
    )
    handle.close()
    return Path(handle.name)


def _choose_local_outcome(stage: StageDef) -> str:
    preferred_by_type = {
        StageType.AGENT_WORK: ("ready", "revised"),
        StageType.AGENT_GATE: ("approved_for_generation", "support", "accepted", "pass"),
        StageType.A2A_REVIEW_LOOP: ("pass", "accepted"),
        StageType.SYSTEM_ACTION: (
            "ready",
            "valid",
            "ready_for_approval",
            "package_ready",
            "approval_needed",
            "surfaced",
            "verified",
            "applied",
            "done_without_publish",
        ),
        StageType.WAIT_SCHEDULE: ("ready", "skipped"),
        StageType.RECOVERY: ("still_running", "resumed"),
        StageType.HUMAN_GATE: tuple(stage.outcomes),
    }
    for outcome in preferred_by_type.get(stage.type, ()):
        if outcome in stage.outcomes:
            return outcome
    if not stage.outcomes:
        raise ValueError(f"stage {stage.id!r} declares no outcomes")
    return stage.outcomes[0]


def _adapter_family_for_stage(stage: StageDef) -> AdapterFamily:
    if stage.type == StageType.HUMAN_GATE or stage.adapter.startswith("surface."):
        return AdapterFamily.SURFACE
    if stage.adapter.startswith("host."):
        return AdapterFamily.HOST
    if stage.adapter.startswith("runtime.") or stage.type in {
        StageType.AGENT_WORK,
        StageType.AGENT_GATE,
        StageType.A2A_REVIEW_LOOP,
    }:
        return AdapterFamily.RUNTIME
    return AdapterFamily.LANE


def _local_adapter_id(family: AdapterFamily) -> str:
    return {
        AdapterFamily.RUNTIME: LocalFakeRuntimeAdapter.adapter_id,
        AdapterFamily.SURFACE: LocalFakeSurfaceAdapter.adapter_id,
        AdapterFamily.HOST: LocalFakeHostAdapter.adapter_id,
        AdapterFamily.LANE: LocalFakeLaneAdapter.adapter_id,
    }[family]


def _operation_for_stage(stage: StageDef, family: AdapterFamily) -> str:
    if family == AdapterFamily.RUNTIME:
        return "invoke"
    if family == AdapterFamily.SURFACE:
        return "publish"
    if family == AdapterFamily.HOST:
        return "resolve"
    return "build_stage_input"


def _artifact_refs_for_stage(
    *,
    workflow_id: str,
    instance_id: str,
    stage: StageDef,
    outcome: str,
) -> tuple[ArtifactRef, ...]:
    artifacts = stage.outputs.get("artifacts") or ()
    refs: list[ArtifactRef] = []
    for index, artifact in enumerate(artifacts, start=1):
        role = str(artifact.get("role", f"artifact_{index}"))
        content_hash = _digest_json(
            {
                "workflow_id": workflow_id,
                "instance_id": instance_id,
                "stage_id": stage.id,
                "role": role,
                "outcome": outcome,
            }
        )
        refs.append(
            ArtifactRef(
                artifact_id=f"{instance_id}:{stage.id}:{role}",
                role=role,
                uri=f"memory://{instance_id}/{stage.id}/{role}",
                content_hash=content_hash,
                mime_type="application/json",
                created_by=LOCAL_RUNNER_ACTOR,
            )
        )
    return tuple(refs)


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


def _stage_summary(stage: StageDef, outcome: str, *, is_human_gate: bool) -> str:
    if is_human_gate:
        return f"Local execution stopped at human gate {stage.id!r}."
    return f"Local fake adapter completed stage {stage.id!r} with outcome {outcome!r}."


def _actor_ref(stage: StageDef) -> str | None:
    if not stage.actors:
        return None
    first_key = next(iter(stage.actors))
    return str(stage.actors[first_key])


def _digest_json(value: Any) -> str:
    payload = json.dumps(to_plain_data(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "LOCAL_RUNNER_ACTOR",
    "LocalRunSummary",
    "LocalWorkflowExecutor",
    "run_local_workflow",
]
