"""Native AWK A2A review-loop runtime adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_workflow_kernel import (
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    ArtifactRef,
    CapabilitySet,
    Receipt,
    RuntimeRef,
    ensure_invocation_family,
    make_adapter_receipt,
    result_from_receipt,
    to_plain_data,
    unsupported_operation_result,
)
from agent_workflow_kernel.adapters import ADAPTER_STATUS_CANCELLED, ADAPTER_STATUS_SUCCEEDED
from agent_workflow_kernel.prompts import digest_data


A2A_REVIEW_SCHEMA = "awk.a2a_review.v1"
A2ATurnProvider = Callable[[AdapterInvocation, Mapping[str, Any]], Sequence[Mapping[str, Any]]]


@dataclass(slots=True)
class A2ASessionState:
    session_key: str
    turn_count: int = 0
    last_outcome: str | None = None
    last_invocation_id: str | None = None


class A2AReviewRuntimeAdapter:
    """Broker one bounded producer/reviewer review exchange inside AWK."""

    adapter_id = "runtime.a2a"
    family = AdapterFamily.RUNTIME
    operations = ("invoke", "execute", "poll", "cancel", "collect_proof", "recover")

    def __init__(
        self,
        *,
        turn_provider: A2ATurnProvider | None = None,
        scripted_turn_batches: Sequence[Sequence[Mapping[str, Any]]] = (),
        created_at: str | None = None,
    ) -> None:
        self.turn_provider = turn_provider
        self.scripted_turn_batches = [tuple(batch) for batch in scripted_turn_batches]
        self.created_at = created_at
        self.sessions: dict[str, A2ASessionState] = {}
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=(
                "native_a2a_review",
                "producer_reviewer_transcript",
                "budget_enforced",
                "prompt_registry_bound_input",
            ),
            metadata={"schema": A2A_REVIEW_SCHEMA},
        )

    def invoke(
        self,
        invocation: AdapterInvocation,
        runtime_input: Mapping[str, Any],
    ) -> AdapterResult:
        ensure_invocation_family(invocation, self.family)
        if not self.capabilities().supports(invocation.operation):
            return unsupported_operation_result(
                invocation,
                created_at=self._now(),
                supported_operations=self.operations,
            )

        created_at = self._now()
        stage = _mapping(runtime_input.get("stage"))
        budget = _mapping(stage.get("budget"))
        max_ping_pong = _int_or_none(budget.get("max_ping_pong_turns"))
        max_questions = _int_or_none(budget.get("max_questions"))
        max_revision_turns = _int_or_none(budget.get("max_revision_turns"))
        turns = tuple(_plain_turn(turn) for turn in self._turns(invocation, runtime_input))
        budget_block = _budget_block_reason(
            turns,
            max_ping_pong=max_ping_pong,
            max_questions=max_questions,
        )
        requested_outcome = _requested_outcome(turns)
        stage_run = _mapping(runtime_input.get("stage_run"))
        attempt = _int_or_none(stage_run.get("attempt")) or 1
        if (
            budget_block is None
            and requested_outcome == "needs_revision"
            and max_revision_turns is not None
            and attempt > max_revision_turns + 1
        ):
            budget_block = (
                f"max_revision_turns exceeded: review attempt {attempt} "
                f"would request revision beyond {max_revision_turns} allowed turn(s)"
            )

        current_draft = _current_draft_artifact(runtime_input)
        reviewed_draft_hash = _string(current_draft.get("content_hash"))
        outcome = "block" if budget_block is not None else requested_outcome
        verdict = _verdict(turns, outcome=outcome, reviewed_draft_hash=reviewed_draft_hash)
        transcript = {
            "schema": A2A_REVIEW_SCHEMA,
            "producer": _mapping(stage.get("actors")).get("producer"),
            "reviewer": _mapping(stage.get("actors")).get("reviewer"),
            "turns": list(turns),
            "budget": {
                "max_ping_pong_turns": max_ping_pong,
                "max_questions": max_questions,
                "max_revision_turns": max_revision_turns,
                "used_ping_pong_turns": len(turns),
                "used_questions": _question_count(turns),
                "exceeded": budget_block is not None,
                "reason": budget_block,
            },
            "verdict": verdict,
        }
        artifact_refs = (
            ArtifactRef(
                artifact_id=f"{invocation.stage_run_id}:editor_transcript",
                role="editor_transcript",
                uri=f"awk://{invocation.instance_id}/{invocation.stage_run_id}/editor_transcript",
                content_hash=digest_data(transcript),
                mime_type="application/json",
                created_by=invocation.adapter_id,
            ),
            ArtifactRef(
                artifact_id=f"{invocation.stage_run_id}:editor_verdict",
                role="editor_verdict",
                uri=f"awk://{invocation.instance_id}/{invocation.stage_run_id}/editor_verdict",
                content_hash=digest_data(verdict),
                mime_type="application/json",
                created_by=invocation.adapter_id,
            ),
        )
        session_key = _session_key(invocation, runtime_input)
        state = self.sessions.get(session_key) or A2ASessionState(session_key=session_key)
        state.turn_count += len(turns)
        state.last_outcome = outcome
        state.last_invocation_id = invocation.invocation_id
        self.sessions[session_key] = state
        outputs = {
            "schema": A2A_REVIEW_SCHEMA,
            "outcome": outcome,
            "session": {
                "session_key": session_key,
                "turn_count": state.turn_count,
                "last_outcome": state.last_outcome,
            },
            "budget": transcript["budget"],
            "verdict": verdict,
            "transcript": transcript,
            "reviewed_draft_hash": reviewed_draft_hash,
            "prompt_binding": {
                "context_packet_ref": invocation.context_packet_ref,
                "rendered_input_digest": runtime_input.get("rendered_input_digest"),
                "prompt_bundle_digest": _prompt_bundle_digest(runtime_input),
            },
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary=(
                "A2A review loop blocked on budget."
                if budget_block is not None
                else f"A2A review loop completed with outcome {outcome}."
            ),
            created_at=created_at,
            artifact_refs=artifact_refs,
            outputs=outputs,
            checks_run=(
                "operation_supported",
                "prompt_registry_context_bound",
                "producer_reviewer_turns_recorded",
                "a2a_budget_enforced",
            ),
            residual_risk=budget_block,
            next_action="Inspect transcript and revise budget or draft packet."
            if budget_block is not None
            else None,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs, artifact_refs=artifact_refs)

    def poll(self, runtime_ref: RuntimeRef | Mapping[str, Any]) -> AdapterResult:
        ref = to_plain_data(runtime_ref)
        session_key = _string(_mapping(ref).get("session_key")) or _string(_mapping(ref).get("external_id"))
        state = self.sessions.get(session_key or "")
        invocation = _synthetic_invocation("poll", session_key or "unknown")
        outputs = {"runtime_ref": ref, "state": to_plain_data(state) if state else None}
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="A2A session state read.",
            created_at=self._now(),
            outputs=outputs,
        )
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def cancel(self, runtime_ref: RuntimeRef | Mapping[str, Any], reason: str) -> Receipt:
        ref = _mapping(to_plain_data(runtime_ref))
        session_key = _string(ref.get("session_key")) or _string(ref.get("external_id"))
        if session_key:
            self.sessions.pop(session_key, None)
        invocation = _synthetic_invocation("cancel", session_key or "unknown")
        return make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_CANCELLED,
            summary=f"A2A session cancelled: {reason}",
            created_at=self._now(),
            outputs={"runtime_ref": ref, "reason": reason},
        )

    def collect_proof(
        self,
        runtime_ref: RuntimeRef | Mapping[str, Any],
        proof_request: Mapping[str, Any],
    ) -> Receipt:
        invocation = _synthetic_invocation("collect_proof", str(proof_request.get("id", "proof")))
        return make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="A2A proof request recorded.",
            created_at=self._now(),
            outputs={"runtime_ref": to_plain_data(runtime_ref), "proof_request": dict(proof_request)},
        )

    def recover(self, idempotency_key: str) -> AdapterResult:
        invocation = _synthetic_invocation("recover", idempotency_key)
        outputs = {"idempotency_key": idempotency_key, "recovered": True}
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="A2A adapter recovery completed.",
            created_at=self._now(),
            outputs=outputs,
        )
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def _turns(
        self,
        invocation: AdapterInvocation,
        runtime_input: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]:
        if self.turn_provider is not None:
            return self.turn_provider(invocation, runtime_input)
        configured = _mapping(runtime_input.get("a2a")).get("turns")
        if isinstance(configured, Sequence) and not isinstance(configured, (str, bytes)):
            return tuple(turn for turn in configured if isinstance(turn, Mapping))
        if self.scripted_turn_batches:
            return self.scripted_turn_batches.pop(0)
        return (
            {
                "actor": "reviewer",
                "message": "Accepted.",
                "outcome": "accepted",
            },
        )

    def _now(self) -> str:
        return self.created_at or datetime.now(UTC).isoformat(timespec="microseconds")


def _plain_turn(turn: Mapping[str, Any]) -> dict[str, Any]:
    plain = to_plain_data(turn)
    return dict(plain) if isinstance(plain, Mapping) else {}


def _requested_outcome(turns: Sequence[Mapping[str, Any]]) -> str:
    for turn in reversed(turns):
        outcome = _string(turn.get("outcome") or turn.get("verdict"))
        if outcome:
            return "block" if outcome == "blocked" else outcome
    return "accepted"


def _verdict(
    turns: Sequence[Mapping[str, Any]],
    *,
    outcome: str,
    reviewed_draft_hash: str,
) -> dict[str, Any]:
    verdict_turn = next((turn for turn in reversed(turns) if turn.get("verdict_packet")), None)
    raw_packet = verdict_turn.get("verdict_packet") if isinstance(verdict_turn, Mapping) else None
    packet = dict(raw_packet) if isinstance(raw_packet, Mapping) else {}
    packet.setdefault("schema", "editorial_verdict.v1")
    packet["outcome"] = outcome
    packet.setdefault("reviewed_draft_hash", reviewed_draft_hash)
    packet.setdefault("summary", _string(turns[-1].get("message")) if turns else outcome)
    return packet


def _budget_block_reason(
    turns: Sequence[Mapping[str, Any]],
    *,
    max_ping_pong: int | None,
    max_questions: int | None,
) -> str | None:
    if max_ping_pong is not None and len(turns) > max_ping_pong:
        return f"max_ping_pong_turns exceeded: {len(turns)}/{max_ping_pong}"
    question_count = _question_count(turns)
    if max_questions is not None and question_count > max_questions:
        return f"max_questions exceeded: {question_count}/{max_questions}"
    return None


def _question_count(turns: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for turn in turns:
        kind = _string(turn.get("kind") or turn.get("type")).lower()
        message = _string(turn.get("message"))
        if kind == "question" or message.strip().endswith("?"):
            count += 1
    return count


def _current_draft_artifact(runtime_input: Mapping[str, Any]) -> Mapping[str, Any]:
    artifacts_by_stage = _mapping(runtime_input.get("artifacts_by_stage"))
    revised = _mapping(_mapping(artifacts_by_stage.get("revise_draft")).get("revised_draft_package"))
    if revised:
        return revised
    return _mapping(_mapping(artifacts_by_stage.get("build_draft_package")).get("draft_package"))


def _prompt_bundle_digest(runtime_input: Mapping[str, Any]) -> str | None:
    packet = _mapping(runtime_input.get("context_packet"))
    rendering = _mapping(packet.get("rendering"))
    return _string(rendering.get("canonical_bundle_digest")) or None


def _session_key(invocation: AdapterInvocation, runtime_input: Mapping[str, Any]) -> str:
    configured = _string(_mapping(runtime_input.get("a2a")).get("session_key"))
    return configured or f"{invocation.workflow_id}:{invocation.instance_id}:a2a"


def _synthetic_invocation(operation: str, key: str) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"runtime.a2a:{operation}:{key}",
        workflow_id="workflow",
        instance_id="instance",
        stage_run_id="stage-run",
        adapter_family=AdapterFamily.RUNTIME,
        adapter_id="runtime.a2a",
        operation=operation,
        idempotency_key=key,
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _int_or_none(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
