"""OpenClaw execution adapter for AWK control-plane handoff."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

from agent_workflow_kernel import (
    AdapterFamily,
    AdapterInvocation,
    AdapterRegistration,
    AdapterResult,
    ArtifactRef,
    CapabilitySet,
    Receipt,
    RiskClass,
    RuntimeRef,
    ensure_invocation_family,
    make_adapter_receipt,
    result_from_receipt,
    to_plain_data,
)
from agent_workflow_kernel.adapters import (
    ADAPTER_STATUS_CANCELLED,
    ADAPTER_STATUS_FAILED,
    ADAPTER_STATUS_SUCCEEDED,
)
from agent_workflow_kernel.adapters import unsupported_operation_result
from agent_workflow_kernel import digest_data
from agent_workflow_kernel.prompts import hash_bytes


OPENCLAW_AGENT_RUNTIME_SCHEMA = "openclaw.agent_runtime.v1"
OPENCLAW_AGENT_INPUT_PACKET_SCHEMA = "openclaw.agent_input_packet.v1"
OPENCLAW_AGENT_COMMAND_MODE_AGENT = "agent"
OPENCLAW_AGENT_COMMAND_MODE_SESSION = "session_start"


class CommandRunner(Protocol):
    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        text: bool,
        capture_output: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        ...


@dataclass(slots=True, frozen=True)
class OpenClawSessionState:
    session_key: str
    session_id: str | None
    agent: str | None
    command_mode: str
    status: str
    last_invocation_id: str | None = None
    session_ref: str | None = None
    updated_at: str | None = None


class OpenClawAgentRuntimeAdapter:
    """Start and monitor an OpenClaw agent session from AWK.

    This adapter keeps all execution outside AWK. AWK prepares a hashed packet,
    starts the session through the OpenClaw CLI, and records runtime IDs + proof
    metadata back as a normal adapter receipt.
    """

    adapter_id = "runtime.openclaw_agent"
    family = AdapterFamily.RUNTIME
    operations = ("invoke", "execute", "poll", "cancel", "collect_proof", "recover")

    def __init__(
        self,
        *,
        openclaw_cli: str = "openclaw",
        default_timeout_seconds: int = 1200,
        default_command_mode: str = OPENCLAW_AGENT_COMMAND_MODE_AGENT,
        default_agent: str | None = None,
        artifact_root: str | Path | None = None,
        openclaw_env: Mapping[str, str] | None = None,
        runner: CommandRunner | None = None,
        created_at: str | None = None,
    ) -> None:
        self.openclaw_cli = openclaw_cli
        self.default_timeout_seconds = max(default_timeout_seconds, 1)
        self.default_command_mode = self._resolve_command_mode(default_command_mode)
        self.default_agent = _string(default_agent)
        self.artifact_root = Path(artifact_root).expanduser().resolve() if artifact_root else None
        self.openclaw_env = {k: v for k, v in (openclaw_env or {}).items() if v is not None}
        self.created_at = created_at or _now_iso()
        self._runner = runner or subprocess.run
        self.sessions: dict[str, OpenClawSessionState] = {}
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=(
                "openclaw_cli_bridge",
                "agent_session_start",
                "session_poll",
                "proof_collection",
                "bounded_session_key",
            ),
            metadata={
                "schema": OPENCLAW_AGENT_RUNTIME_SCHEMA,
                "cli": self.openclaw_cli,
                "default_timeout_seconds": self.default_timeout_seconds,
                "default_command_mode": self.default_command_mode,
                "environment_keys": tuple(sorted(self.openclaw_env.keys())),
            },
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
        packet = self._build_packet(invocation=invocation, runtime_input=runtime_input)
        packet_digest = digest_data(packet)
        command_mode = self._command_mode(runtime_input)
        agent = self._resolve_agent(runtime_input) or self.default_agent or "agent"
        payload_message = json.dumps(packet, sort_keys=True)
        packet_path = self._write_artifact(
            invocation=invocation,
            role="agent_input_packet",
            content=packet,
            suffix=".json",
        )
        session_key = _string(
            _nested(runtime_input, "openclaw_agent", "session_key")
        ) or _string(_nested(runtime_input, "openclaw", "session_key"))
        if not session_key:
            session_key = self._session_key(invocation, runtime_input)

        cmd = self._start_command(
            invocation=invocation,
            agent=agent,
            session_key=session_key,
            packet_path=packet_path,
            message=payload_message,
            command_mode=command_mode,
        )
        env = self._environment()
        try:
            completed = self._runner(
                cmd,
                cwd=str(self._work_root()),
                env=env,
                text=True,
                capture_output=True,
                timeout=self._timeout_seconds(runtime_input),
                check=False,
            )
        except Exception as exc:
            return self._failed_result(
                invocation=invocation,
                created_at=created_at,
                session_key=session_key,
                session_id=None,
                packet_uri=packet_uri(packet_path, invocation),
                status="error",
                error=exc,
                packet_digest=packet_digest,
            )

        raw_output = completed.stdout.strip() if isinstance(completed.stdout, str) else ""
        output = _safe_json(raw_output)
        session_id = (
            _string(_nested(output, "session", "session_id"))
            or _string(_nested(output, "session", "id"))
            or _string(_nested(output, "sessionId"))
            or _string(_nested(output, "result", "session_id"))
            or _string(_nested(output, "result", "sessionId"))
            or _string(_nested(output, "result", "session", "session_id"))
            or _string(_nested(output, "result", "session", "sessionId"))
            or _string(output.get("session_id"))
            or _string(output.get("sessionId"))
        )
        outcome = _string(_nested(output, "outcome")) or _string(_nested(output, "result", "outcome"))
        if not outcome:
            if completed.returncode == 0:
                outcome = "ready"
            else:
                outcome = "blocked"

        status = _status_to_adapter_status(completed.returncode, _string(_nested(output, "status")))
        if status == ADAPTER_STATUS_SUCCEEDED and session_key:
            self.sessions[session_key] = OpenClawSessionState(
                session_key=session_key,
                session_id=session_id,
                agent=agent,
                command_mode=command_mode,
                status=_string(_nested(output, "status")) or "running",
                last_invocation_id=invocation.invocation_id,
                session_ref=_string(_nested(output, "session_ref")),
                updated_at=created_at,
            )

        proof_path = self._write_artifact(
            invocation=invocation,
            role="agent_session_output",
            content={
                "status_code": completed.returncode,
                "raw_output": raw_output,
                "parsed_output": output,
            },
            suffix=".json",
        )
        outputs = {
            "schema": OPENCLAW_AGENT_RUNTIME_SCHEMA,
            "outcome": outcome,
            "cli": self.openclaw_cli,
            "session": {
                "session_key": session_key,
                "session_id": session_id,
                "agent": agent,
                "command_mode": command_mode,
                "status": _string(_nested(output, "status")) or status,
                "status_code": completed.returncode,
            },
            "prompt_binding": _prompt_binding(runtime_input),
            "command": cmd,
            "command_mode": command_mode,
            "return_code": completed.returncode,
            "packet_digest": packet_digest,
            "packet_path": str(packet_path),
            "proof_path": str(proof_path),
            "raw_output": raw_output,
        }
        artifact_refs = (
            _artifact_from_path(
                invocation=invocation,
                role="agent_input_packet",
                path=packet_path,
                created_by=self.adapter_id,
                namespace="openclaw",
            ),
            _artifact_from_path(
                invocation=invocation,
                role="agent_session_output",
                path=proof_path,
                created_by=self.adapter_id,
                namespace="openclaw",
            ),
        )
        summary = (
            "OpenClaw agent session started."
            if status == ADAPTER_STATUS_SUCCEEDED
            else "OpenClaw agent session start failed."
        )
        receipt = make_adapter_receipt(
            invocation,
            status=status,
            summary=summary,
            created_at=created_at,
            artifact_refs=artifact_refs,
            outputs=outputs,
            checks_run=("resolve_prompt_packet", "openclaw_cli_invocation", "session_output_recorded"),
            residual_risk=None
            if status == ADAPTER_STATUS_SUCCEEDED
            else "OpenClaw CLI invocation failed or returned non-zero status code.",
            next_action=None
            if status == ADAPTER_STATUS_SUCCEEDED
            else "Inspect CLI output and local openclaw CLI availability before retrying.",
        )
        self.receipts.append(receipt)
        return result_from_receipt(
            invocation,
            receipt,
            outputs=outputs,
            artifact_refs=artifact_refs,
        )

    def poll(self, runtime_ref: RuntimeRef | Mapping[str, Any]) -> AdapterResult:
        ref = _plain_runtime_ref(runtime_ref)
        session_key = _string(ref.get("session_key")) or _string(ref.get("external_id"))
        state = self.sessions.get(session_key or "")
        mode = state.command_mode if state else self.default_command_mode
        agent = state.agent if state else None
        invocation = _synthetic_invocation(self.adapter_id, "poll", session_key or "unknown")
        if session_key:
            cmd = self._poll_command(agent=agent, session_key=session_key, command_mode=mode)
            try:
                poll_output = _safe_json(
                    str(
                        self._runner(
                            cmd,
                            cwd=str(self._work_root()),
                            env=self._environment(),
                            text=True,
                            capture_output=True,
                            timeout=self.default_timeout_seconds,
                            check=False,
                        ).stdout
                    )
                )
                output = _resolve_session_status(poll_output, session_key=session_key, command_mode=mode)
            except Exception:
                output = {}
        else:
            output = {}
        outputs = {
            "runtime_ref": ref,
            "session": to_plain_data(state) if state else None,
            "session_key": session_key,
            "status_output": output,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="OpenClaw session status retrieved." if state else "OpenClaw session status unavailable.",
            created_at=self._now(),
            outputs=outputs,
            checks_run=("resolve_session_state",) if session_key else ("session_key_missing",),
        )
        self.receipts.append(receipt)
        return result_from_receipt(
            invocation,
            receipt,
            outputs=outputs,
            artifact_refs=(),
        )

    def cancel(self, runtime_ref: RuntimeRef | Mapping[str, Any], reason: str) -> Receipt:
        ref = _plain_runtime_ref(runtime_ref)
        session_key = _string(ref.get("session_key")) or _string(ref.get("external_id"))
        state = self.sessions.get(session_key or "")
        mode = state.command_mode if state else self.default_command_mode
        agent = state.agent if state else None
        if session_key:
            self.sessions.pop(session_key, None)
            cmd = self._cancel_command(agent=agent, session_key=session_key, command_mode=mode)
        else:
            cmd = None
        try:
            if cmd is None:
                outputs = {"runtime_ref": ref, "reason": reason, "status": "cancel_skipped"}
            else:
                self._runner(
                    cmd,
                    cwd=str(self._work_root()),
                    env=self._environment(),
                    text=True,
                    capture_output=True,
                    timeout=self.default_timeout_seconds,
                    check=False,
                )
                outputs = {"runtime_ref": ref, "reason": reason, "status": "cancel_requested"}
        except Exception as exc:
            outputs = {"runtime_ref": ref, "reason": reason, "status": "cancel_failed", "error": str(exc)}
        invocation = _synthetic_invocation(self.adapter_id, "cancel", session_key or "unknown")
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_CANCELLED if "status" not in outputs else ADAPTER_STATUS_CANCELLED,
            summary="OpenClaw session cancellation recorded.",
            created_at=self._now(),
            outputs=outputs,
            checks_run=("session_cancel",),
        )
        self.receipts.append(receipt)
        return receipt

    def collect_proof(
        self,
        runtime_ref: RuntimeRef | Mapping[str, Any],
        proof_request: Mapping[str, Any],
    ) -> Receipt:
        ref = _plain_runtime_ref(runtime_ref)
        session_key = _string(ref.get("session_key")) or _string(ref.get("external_id"))
        state = self.sessions.get(session_key or "")
        outputs = {"runtime_ref": ref, "proof_request": dict(proof_request), "proof": None}
        if not session_key:
            receipt_status = ADAPTER_STATUS_FAILED
            outputs["error"] = "session_key is required to collect proof"
            outputs["proof"] = None
        else:
            mode = state.command_mode if state else self.default_command_mode
            cmd = self._proof_command(session_key=session_key, command_mode=mode)
            try:
                completed = self._runner(
                    cmd,
                    cwd=str(self._work_root()),
                    env=self._environment(),
                    text=True,
                    capture_output=True,
                    timeout=self.default_timeout_seconds,
                    check=False,
                )
                outputs["proof"] = _safe_json(completed.stdout)
                receipt_status = ADAPTER_STATUS_SUCCEEDED if completed.returncode == 0 else ADAPTER_STATUS_FAILED
            except Exception as exc:
                outputs["error"] = str(exc)
                receipt_status = ADAPTER_STATUS_FAILED
            if _status_from_session(state) in {"failed", "cancelled"}:
                receipt_status = ADAPTER_STATUS_FAILED
            if mode == OPENCLAW_AGENT_COMMAND_MODE_AGENT:
                proof_output = outputs["proof"]
                if isinstance(proof_output, Mapping):
                    outputs["proof"] = {
                        "session_key": session_key,
                        "trajectory": proof_output,
                        "command_mode": mode,
                    }
        invocation = _synthetic_invocation(
            self.adapter_id,
            "collect_proof",
            session_key or "unknown",
        )
        receipt = make_adapter_receipt(
            invocation,
            status=receipt_status,
            summary="OpenClaw session proof collected."
            if receipt_status == ADAPTER_STATUS_SUCCEEDED
            else "OpenClaw session proof collection failed.",
            created_at=self._now(),
            outputs=outputs,
            checks_run=("collect_session_proof",),
        )
        self.receipts.append(receipt)
        return receipt

    def recover(self, idempotency_key: str) -> AdapterResult:
        matches = []
        for state in self.sessions.values():
            if idempotency_key in {
                state.session_key,
                state.session_id,
                state.agent or "",
                state.last_invocation_id or "",
            }:
                matches.append(to_plain_data(state))
        invocation = _synthetic_invocation(self.adapter_id, "recover", idempotency_key)
        outputs = {"idempotency_key": idempotency_key, "sessions": matches}
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if matches else ADAPTER_STATUS_FAILED,
            summary="OpenClaw session recovery found matching session state."
            if matches
            else "OpenClaw session recovery found no matching session state.",
            created_at=self._now(),
            outputs=outputs,
            checks_run=("recover_session_state",),
        )
        self.receipts.append(receipt)
        return result_from_receipt(
            invocation,
            receipt,
            outputs=outputs,
            artifact_refs=(),
        )

    def _build_packet(self, invocation: AdapterInvocation, runtime_input: Mapping[str, Any]) -> dict[str, Any]:
        binding = _prompt_binding(runtime_input)
        stage = _mapping(runtime_input.get("stage"))
        return {
            "schema": OPENCLAW_AGENT_INPUT_PACKET_SCHEMA,
            "workflow_id": invocation.workflow_id,
            "instance_id": invocation.instance_id,
            "stage_run_id": invocation.stage_run_id,
            "stage_id": stage.get("id"),
            "stage_type": stage.get("type"),
            "invocation_id": invocation.invocation_id,
            "prompt_binding": binding,
            "runtime_input": {
                "stage": stage,
                "inputs": _mapping(runtime_input.get("inputs")),
                "artifact_refs": _mapping(runtime_input.get("artifacts_by_stage")),
                "prior_receipts": _mapping(runtime_input.get("prior_receipts")),
            },
            "payload": {
                "context_packet_ref": invocation.context_packet_ref,
                "rendered_input": _string(runtime_input.get("rendered_input")),
                "rendered_input_digest": _string(runtime_input.get("rendered_input_digest")),
            },
        }

    def _write_artifact(
        self,
        *,
        invocation: AdapterInvocation,
        role: str,
        content: Mapping[str, Any],
        suffix: str,
    ) -> Path:
        root = self._work_root()
        safe_id = _safe_identifier(invocation.invocation_id)
        path = root / f"{safe_id}.{role}{suffix}"
        path.write_text(json.dumps(to_plain_data(content), indent=2, sort_keys=True), encoding="utf-8")
        return path

    def _start_command(
        self,
        *,
        invocation: AdapterInvocation,
        agent: str | None,
        session_key: str,
        packet_path: Path,
        message: str,
        command_mode: str,
    ) -> list[str]:
        agent_arg = _string(agent) or "agent"
        if command_mode == OPENCLAW_AGENT_COMMAND_MODE_AGENT:
            command = [self.openclaw_cli, "agent", "--agent", agent_arg, "--session-key", session_key]
            if message:
                command.extend(["--message", message])
            command.append("--json")
            return command
        if command_mode == OPENCLAW_AGENT_COMMAND_MODE_SESSION:
            return [
                self.openclaw_cli,
                "session",
                "start",
                "--agent",
                agent_arg,
                "--session-key",
                session_key,
                "--packet",
                str(packet_path),
                "--json",
            ]
        return [
            self.openclaw_cli,
            "agent",
            "--agent",
            agent_arg,
            "--session-key",
            session_key,
            "--json",
        ]

    def _poll_command(
        self, *, agent: str | None, session_key: str, command_mode: str
    ) -> list[str]:
        if command_mode == OPENCLAW_AGENT_COMMAND_MODE_AGENT:
            cmd = [self.openclaw_cli, "sessions", "--json"]
            if agent:
                cmd.extend(["--agent", agent])
            return cmd
        return [self.openclaw_cli, "session", "status", "--session-key", session_key, "--json"]

    def _proof_command(self, *, session_key: str, command_mode: str) -> list[str]:
        if command_mode == OPENCLAW_AGENT_COMMAND_MODE_AGENT:
            path = self._work_root() / f"{_safe_identifier(session_key)}-trajectory"
            return [
                self.openclaw_cli,
                "sessions",
                "export-trajectory",
                "--session-key",
                session_key,
                "--output",
                str(path),
                "--json",
            ]
        return [self.openclaw_cli, "session", "proof", "--session-key", session_key, "--json"]

    def _cancel_command(
        self, *, agent: str | None, session_key: str, command_mode: str
    ) -> list[str] | None:
        if command_mode == OPENCLAW_AGENT_COMMAND_MODE_AGENT:
            return None
        return [self.openclaw_cli, "session", "cancel", "--session-key", session_key]

    def _resolve_agent(self, runtime_input: Mapping[str, Any]) -> str:
        agent = _string(_nested(runtime_input, "openclaw_agent", "agent"))
        if not agent:
            agent = _string(_nested(runtime_input, "openclaw", "agent"))
        if not agent:
            agent = _string(_nested(runtime_input, "agent"))
        if not agent:
            return "agent"
        return agent

    def _command_mode(self, runtime_input: Mapping[str, Any]) -> str:
        configured = _string(_nested(runtime_input, "openclaw_agent", "command_mode"))
        if not configured:
            configured = _string(_nested(runtime_input, "openclaw_agent", "mode"))
        if not configured:
            configured = _string(_nested(runtime_input, "openclaw", "command_mode"))
        if not configured:
            configured = self.default_command_mode
        return self._resolve_command_mode(configured)

    def _resolve_command_mode(self, value: str) -> str:
        normalized = value.lower()
        if normalized in {"agent", "agent_turn", "agent_turn_command"}:
            return OPENCLAW_AGENT_COMMAND_MODE_AGENT
        if normalized in {"session", "session_start", "session-start"}:
            return OPENCLAW_AGENT_COMMAND_MODE_SESSION
        return OPENCLAW_AGENT_COMMAND_MODE_AGENT

    def _session_key(self, invocation: AdapterInvocation, runtime_input: Mapping[str, Any]) -> str:
        attempt = str(invocation.input_ref or "").strip()
        base = f"{invocation.workflow_id}:{invocation.instance_id}:{invocation.stage_run_id}:{invocation.stage_run_id}"
        if attempt:
            return f"{base}:{attempt}"
        return base

    def _timeout_seconds(self, runtime_input: Mapping[str, Any]) -> int:
        configured = _int(_nested(runtime_input, "openclaw_agent", "timeout_seconds"))
        if configured is not None:
            return max(configured, 1)
        return self.default_timeout_seconds

    def _work_root(self) -> Path:
        root = self.artifact_root
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
            return root
        fallback = Path(os.getcwd()) / ".awk-openclaw" / "runtime"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def _environment(self) -> dict[str, str]:
        return {**os.environ, **self.openclaw_env}

    def _now(self) -> str:
        return self.created_at or datetime.now(UTC).isoformat(timespec="microseconds")

    def _failed_result(
        self,
        *,
        invocation: AdapterInvocation,
        created_at: str,
        session_key: str,
        session_id: str | None,
        packet_uri: str,
        status: str,
        error: Exception,
        packet_digest: str,
    ) -> AdapterResult:
        outputs = {
            "schema": OPENCLAW_AGENT_RUNTIME_SCHEMA,
            "outcome": "blocked",
            "cli": self.openclaw_cli,
            "session": {
                "session_key": session_key,
                "session_id": session_id,
                "agent": self._resolve_agent({}),
                "status": status,
            },
            "packet_digest": packet_digest,
            "error": str(error),
            "packet_uri": packet_uri,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_FAILED,
            summary="OpenClaw agent invocation could not start.",
            created_at=created_at,
            outputs=outputs,
            checks_run=("openclaw_cli_invocation", "artifact_packet_prepared"),
            residual_risk="OpenClaw adapter execution could not reach the CLI.",
            next_action=str(error),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)


def openclaw_agent_runtime_registrations(**kwargs: Any) -> tuple[AdapterRegistration, ...]:
    adapter = OpenClawAgentRuntimeAdapter(**kwargs)
    return (
        AdapterRegistration.from_runtime_adapter(
            adapter,
            side_effects=(RiskClass.REVIEW_ONLY, RiskClass.LOCAL_DRAFT),
            replay_safe=True,
        ),
    )


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:128]


def _artifact_from_path(
    *,
    invocation: AdapterInvocation,
    role: str,
    path: Path,
    created_by: str,
    namespace: str,
) -> ArtifactRef:
    try:
        content = path.read_bytes()
        if content:
            content_hash = hash_bytes(content)
        else:
            content_hash = digest_data({"artifact_path": str(path), "size_bytes": path.stat().st_size})
    except OSError:
        content_hash = digest_data({"artifact_path": str(path)})
    return ArtifactRef(
        artifact_id=f"{invocation.stage_run_id}:{role}",
        role=role,
        uri=f"{namespace}://{invocation.instance_id}/{invocation.stage_run_id}/{role}",
        content_hash=content_hash,
        mime_type="application/json",
        size_bytes=path.stat().st_size if path.exists() else None,
        created_by=created_by,
    )


def _status_to_adapter_status(return_code: int, status: str | None) -> str:
    if return_code == 0:
        return ADAPTER_STATUS_SUCCEEDED
    if _string(status) in {"blocked", "invalid", "failed"}:
        return ADAPTER_STATUS_FAILED
    return ADAPTER_STATUS_FAILED


def _status_from_session(state: OpenClawSessionState | None) -> str:
    return state.status if state else "unknown"


def _prompt_binding(runtime_input: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        "context_packet_ref": _string(runtime_input.get("context_packet_ref")),
        "rendered_input_digest": _string(runtime_input.get("rendered_input_digest")),
        "prompt_bundle_digest": _string(
            _nested(runtime_input, "context_packet", "rendering", "canonical_bundle_digest")
        ),
        "prompt_packet_digest": _string(_nested(runtime_input, "context_packet", "packet_digest")),
    }


def _safe_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return to_plain_data(parsed)
    except json.JSONDecodeError:
        return {}
    return {}


def _resolve_session_status(
    poll_output: Mapping[str, Any],
    *,
    session_key: str,
    command_mode: str,
) -> dict[str, Any]:
    if command_mode != OPENCLAW_AGENT_COMMAND_MODE_AGENT:
        return to_plain_data(poll_output)

    sessions_value = poll_output.get("sessions")
    if isinstance(sessions_value, list):
        for candidate in sessions_value:
            if not isinstance(candidate, Mapping):
                continue
            candidate_key = _string(_string(candidate.get("session_key")) or _string(candidate.get("key")))
            if candidate_key == session_key:
                return dict(candidate)
    for key in ("session", "data", "result"):
        candidate = _nested(poll_output, key, "session_key")
        if _string(candidate) == session_key:
            return _mapping(_nested(poll_output, key))
    return {}


def packet_uri(path: Path, invocation: AdapterInvocation) -> str:
    return f"openclaw://{invocation.instance_id}/{invocation.stage_run_id}/{path.name}"


def _plain_runtime_ref(value: RuntimeRef | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, RuntimeRef):
        return to_plain_data(value.__dict__)
    return dict(value)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _synthetic_invocation(adapter_id: str, operation: str, key: str) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"{adapter_id}:{operation}:{key}",
        workflow_id="openclaw-runtime",
        instance_id="openclaw-runtime",
        stage_run_id=key,
        adapter_family=AdapterFamily.RUNTIME,
        adapter_id=adapter_id,
        operation=operation,
        idempotency_key=key,
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _status_from_returncode(return_code: int) -> str:
    if return_code == 0:
        return "success"
    if return_code == 124:
        return "timeout"
    return "error"
