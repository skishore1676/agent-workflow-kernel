"""Runtime adapter for native Codex CLI execution."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent_workflow_kernel import (
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    ArtifactRef,
    CapabilitySet,
    Receipt,
    RuntimeRef,
    AdapterRegistration,
    RiskClass,
    ensure_invocation_family,
    make_adapter_receipt,
    result_from_receipt,
    to_plain_data,
    unsupported_operation_result,
)
from agent_workflow_kernel.adapters import (
    ADAPTER_STATUS_CANCELLED,
    ADAPTER_STATUS_FAILED,
    ADAPTER_STATUS_SUCCEEDED,
    ADAPTER_STATUS_TIMED_OUT,
)


CODEX_CLI_ADAPTER_VERSION = "codex_cli_runtime.v1"
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


@dataclass(slots=True)
class CodexCliSessionState:
    session_key: str
    session_id: str
    turn_count: int = 0
    last_invocation_id: str | None = None
    last_stage_run_id: str | None = None
    last_updated_at: str | None = None


@dataclass(slots=True, frozen=True)
class _CodexCliConfig:
    executable: str
    cwd: str | None
    model: str | None
    profile: str | None
    sandbox: str | None
    ask_for_approval: str | None
    ignore_rules: bool
    ignore_user_config: bool
    skip_git_repo_check: bool
    output_schema: str | None
    timeout_seconds: int
    artifact_dir: str | None
    allow_last_resume: bool
    max_session_turns: int


class CodexCliRuntimeAdapter:
    family = AdapterFamily.RUNTIME
    operations = ("invoke", "execute", "poll", "cancel", "collect_proof", "recover")

    def __init__(
        self,
        *,
        adapter_id: str,
        session_mode: bool,
        executable: str = "codex",
        default_cwd: str | None = None,
        default_model: str | None = None,
        default_profile: str | None = None,
        default_sandbox: str | None = "read-only",
        default_ask_for_approval: str | None = "never",
        ignore_rules: bool = True,
        ignore_user_config: bool = False,
        skip_git_repo_check: bool = False,
        timeout_seconds: int = 900,
        max_session_turns: int = 20,
    ) -> None:
        self.adapter_id = adapter_id
        self.session_mode = session_mode
        self.executable = executable
        self.default_cwd = default_cwd
        self.default_model = default_model
        self.default_profile = default_profile
        self.default_sandbox = default_sandbox
        self.default_ask_for_approval = default_ask_for_approval
        self.ignore_rules = ignore_rules
        self.ignore_user_config = ignore_user_config
        self.skip_git_repo_check = skip_git_repo_check
        self.timeout_seconds = timeout_seconds
        self.max_session_turns = max_session_turns
        self.sessions: dict[str, CodexCliSessionState] = {}
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=(
                "codex_cli",
                "native_codex_auth",
                "jsonl_events",
                "last_message_capture",
                "bounded_session" if self.session_mode else "one_shot",
            ),
            metadata={
                "schema": CODEX_CLI_ADAPTER_VERSION,
                "session_mode": self.session_mode,
                "session_resume": "explicit_session_id",
                "default_timeout_seconds": self.timeout_seconds,
                "default_sandbox": self.default_sandbox,
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
                created_at=_now_iso(),
                supported_operations=self.operations,
            )

        config = self._config(runtime_input)
        executable = shutil.which(config.executable) or config.executable
        prompt = self._render_prompt(invocation, runtime_input)
        session_key = self._session_key(invocation, runtime_input)
        prior_session = self.sessions.get(session_key) if self.session_mode else None
        requested_session_id = _string(runtime_input.get("session_id")) or _string(
            _nested(runtime_input, "codex_cli", "session_id")
        )
        resumed_session_id = requested_session_id or (prior_session.session_id if prior_session else None)
        use_resume = self.session_mode and bool(resumed_session_id)

        if self.session_mode and prior_session and prior_session.turn_count >= config.max_session_turns:
            use_resume = False
            resumed_session_id = None

        created_at = _now_iso()
        timeout = False
        with _output_paths(config, invocation) as paths:
            command = self._build_command(
                executable=executable,
                config=config,
                resume_session_id=resumed_session_id if use_resume else None,
            )
            command = [
                str(paths["last_message"]) if item == "{LAST_MESSAGE}" else item
                for item in command
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=config.cwd or None,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=config.timeout_seconds,
                    check=False,
                )
                returncode = completed.returncode
                stdout = completed.stdout
                stderr = completed.stderr
            except subprocess.TimeoutExpired as exc:
                timeout = True
                returncode = -1
                stdout = exc.stdout if isinstance(exc.stdout, str) else ""
                stderr = exc.stderr if isinstance(exc.stderr, str) else ""

            last_message = _read_text(paths["last_message"])
            events = _parse_jsonl(stdout)
            session_id = _find_session_id(events, stdout) or resumed_session_id
            usage = _collect_usage(events)
            structured_result = _parse_structured_last_message(last_message)
            session_trackable = bool(session_id)
            if self.session_mode and session_id:
                state = self.sessions.get(session_key) or CodexCliSessionState(
                    session_key=session_key,
                    session_id=session_id,
                )
                state.session_id = session_id
                state.turn_count += 1
                state.last_invocation_id = invocation.invocation_id
                state.last_stage_run_id = invocation.stage_run_id
                state.last_updated_at = created_at
                self.sessions[session_key] = state

            paths["events"].write_text(stdout, encoding="utf-8")
            paths["stderr"].write_text(stderr, encoding="utf-8")

            status = ADAPTER_STATUS_SUCCEEDED
            if timeout:
                status = ADAPTER_STATUS_TIMED_OUT
            elif returncode != 0:
                status = ADAPTER_STATUS_FAILED
            elif self.session_mode and not session_trackable:
                status = ADAPTER_STATUS_FAILED

            output_uris = {
                "last_message": _uri(paths["last_message"]),
                "events_jsonl": _uri(paths["events"]),
                "stderr": _uri(paths["stderr"]),
            }
            outputs: dict[str, Any] = {
                "schema": CODEX_CLI_ADAPTER_VERSION,
                "adapter_id": self.adapter_id,
                "mode": "bounded_session" if self.session_mode else "one_shot",
                "command": _redacted_command(command),
                "cwd": config.cwd,
                "returncode": returncode,
                "timed_out": timeout,
                "session": {
                    "session_key": session_key if self.session_mode else None,
                    "session_id": session_id,
                    "session_reused": bool(use_resume),
                    "session_trackable": session_trackable,
                    "turn_count": self.sessions[session_key].turn_count
                    if self.session_mode and session_key in self.sessions
                    else 0,
                    "max_session_turns": config.max_session_turns,
                },
                "usage": usage,
                "last_message": last_message,
                "structured_result": structured_result,
                "artifacts": output_uris,
            }
            summary = _summary_for_result(
                status=status,
                session_mode=self.session_mode,
                reused=bool(use_resume),
                session_trackable=session_trackable,
            )
            artifact_refs = _artifact_refs(
                invocation=invocation,
                paths=paths,
                adapter_id=self.adapter_id,
                created_at=created_at,
            )
            receipt = make_adapter_receipt(
                invocation,
                status=status,
                summary=summary,
                created_at=created_at,
                artifact_refs=artifact_refs,
                outputs=outputs,
                checks_run=(
                    "codex_cli_available",
                    "codex_cli_invoked",
                    "last_message_captured",
                    "session_id_captured" if self.session_mode else "one_shot_no_session_required",
                ),
                residual_risk=None
                if status == ADAPTER_STATUS_SUCCEEDED
                else "Codex CLI invocation did not produce a reusable successful result.",
                next_action=None
                if status == ADAPTER_STATUS_SUCCEEDED
                else "Inspect stderr/events artifact and retry or repair the Codex CLI runtime.",
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
        invocation = _synthetic_invocation(self.adapter_id, "poll", session_key or "unknown")
        outputs = {
            "runtime_ref": ref,
            "session": to_plain_data(asdict(state)) if state else None,
            "state": "running" if state else "unknown",
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if state else ADAPTER_STATUS_FAILED,
            summary="Codex CLI session state found." if state else "Codex CLI session state not found.",
            created_at=_now_iso(),
            outputs=outputs,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def cancel(
        self,
        runtime_ref: RuntimeRef | Mapping[str, Any],
        reason: str,
    ) -> Receipt:
        ref = _plain_runtime_ref(runtime_ref)
        session_key = _string(ref.get("session_key")) or _string(ref.get("external_id"))
        if session_key:
            self.sessions.pop(session_key, None)
        invocation = _synthetic_invocation(self.adapter_id, "cancel", session_key or "unknown")
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_CANCELLED,
            summary=f"Codex CLI bounded session cancelled: {reason}",
            created_at=_now_iso(),
            outputs={"runtime_ref": ref, "reason": reason},
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
        invocation = _synthetic_invocation(self.adapter_id, "collect_proof", session_key or "unknown")
        outputs = {
            "runtime_ref": ref,
            "proof_request": dict(proof_request),
            "session": to_plain_data(asdict(state)) if state else None,
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if state else ADAPTER_STATUS_FAILED,
            summary="Codex CLI proof collected." if state else "No Codex CLI session proof found.",
            created_at=_now_iso(),
            outputs=outputs,
        )
        self.receipts.append(receipt)
        return receipt

    def recover(self, idempotency_key: str) -> AdapterResult:
        invocation = _synthetic_invocation(self.adapter_id, "recover", idempotency_key)
        matched = [
            to_plain_data(asdict(state))
            for state in self.sessions.values()
            if idempotency_key in {
                state.session_key,
                state.session_id,
                state.last_invocation_id,
                state.last_stage_run_id,
            }
        ]
        outputs = {"idempotency_key": idempotency_key, "sessions": matched}
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if matched else ADAPTER_STATUS_FAILED,
            summary="Codex CLI recovery found session state."
            if matched
            else "Codex CLI recovery found no session state.",
            created_at=_now_iso(),
            outputs=outputs,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def _config(self, runtime_input: Mapping[str, Any]) -> _CodexCliConfig:
        raw = runtime_input.get("codex_cli")
        config = raw if isinstance(raw, Mapping) else {}
        timeout = _int(config.get("timeout_seconds"), self.timeout_seconds)
        max_turns = _int(config.get("max_session_turns"), self.max_session_turns)
        return _CodexCliConfig(
            executable=_string(config.get("executable")) or self.executable,
            cwd=_string(config.get("cwd")) or self.default_cwd,
            model=_string(config.get("model")) or self.default_model,
            profile=_string(config.get("profile")) or self.default_profile,
            sandbox=_string(config.get("sandbox")) or self.default_sandbox,
            ask_for_approval=_string(config.get("ask_for_approval"))
            or self.default_ask_for_approval,
            ignore_rules=_bool(config.get("ignore_rules"), self.ignore_rules),
            ignore_user_config=_bool(config.get("ignore_user_config"), self.ignore_user_config),
            skip_git_repo_check=_bool(config.get("skip_git_repo_check"), self.skip_git_repo_check),
            output_schema=_string(config.get("output_schema")),
            timeout_seconds=max(timeout, 1),
            artifact_dir=_string(config.get("artifact_dir")),
            allow_last_resume=_bool(config.get("allow_last_resume"), False),
            max_session_turns=max(max_turns, 1),
        )

    def _build_command(
        self,
        *,
        executable: str,
        config: _CodexCliConfig,
        resume_session_id: str | None,
    ) -> list[str]:
        if resume_session_id:
            command = [executable, "exec", "resume"]
            if config.ask_for_approval:
                command.extend(["--config", f"approval_policy={json.dumps(config.ask_for_approval)}"])
            if config.ignore_user_config:
                command.append("--ignore-user-config")
            if config.ignore_rules:
                command.append("--ignore-rules")
            if config.model:
                command.extend(["--model", config.model])
            command.extend(["--json", "--output-last-message", "{LAST_MESSAGE}"])
            command.extend([resume_session_id, "-"])
            return command

        command = [executable, "exec"]
        if config.cwd:
            command.extend(["--cd", config.cwd])
        if config.model:
            command.extend(["--model", config.model])
        if config.profile:
            command.extend(["--profile", config.profile])
        if config.sandbox:
            command.extend(["--sandbox", config.sandbox])
        if config.ask_for_approval:
            command.extend(["--config", f"approval_policy={json.dumps(config.ask_for_approval)}"])
        if config.skip_git_repo_check:
            command.append("--skip-git-repo-check")
        if config.ignore_user_config:
            command.append("--ignore-user-config")
        if config.ignore_rules:
            command.append("--ignore-rules")
        if config.output_schema:
            command.extend(["--output-schema", config.output_schema])
        command.extend(["--json", "--output-last-message", "{LAST_MESSAGE}", "-"])
        return command

    def _render_prompt(
        self,
        invocation: AdapterInvocation,
        runtime_input: Mapping[str, Any],
    ) -> str:
        for key in ("prompt", "rendered_prompt", "objective", "task"):
            value = runtime_input.get(key)
            if isinstance(value, str) and value.strip():
                return value
        codex_cli = runtime_input.get("codex_cli")
        if isinstance(codex_cli, Mapping):
            prompt = codex_cli.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                return prompt

        stage = runtime_input.get("stage")
        stage_packet = stage if isinstance(stage, Mapping) else {}
        compact = {
            "workflow_id": invocation.workflow_id,
            "instance_id": invocation.instance_id,
            "stage_run_id": invocation.stage_run_id,
            "stage_id": stage_packet.get("id"),
            "stage_type": stage_packet.get("type"),
            "stage_inputs": stage_packet.get("inputs"),
            "prompt_refs": stage_packet.get("prompt_refs"),
        }
        return (
            "Complete the AWK stage described below. Return a concise result and "
            "do not perform external side effects unless the prompt explicitly "
            "authorizes them.\n\n"
            f"{json.dumps(to_plain_data(compact), indent=2, sort_keys=True)}"
        )

    def _session_key(
        self,
        invocation: AdapterInvocation,
        runtime_input: Mapping[str, Any],
    ) -> str:
        explicit = _string(runtime_input.get("session_key")) or _string(
            _nested(runtime_input, "codex_cli", "session_key")
        )
        if explicit:
            return explicit
        actor_ref = _string(runtime_input.get("actor_ref")) or _string(
            _nested(runtime_input, "stage", "actors", "primary")
        )
        actor_part = actor_ref or "default_actor"
        return f"{invocation.workflow_id}:{invocation.instance_id}:{actor_part}"


class CodexCliExecRuntimeAdapter(CodexCliRuntimeAdapter):
    adapter_id = "runtime.codex_cli_exec"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(adapter_id=self.adapter_id, session_mode=False, **kwargs)


class CodexCliSessionRuntimeAdapter(CodexCliRuntimeAdapter):
    adapter_id = "runtime.codex_cli_session"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(adapter_id=self.adapter_id, session_mode=True, **kwargs)


def codex_cli_runtime_registrations(**kwargs: Any) -> tuple[AdapterRegistration, ...]:
    """Build standard AWK registrations for native Codex CLI runtime adapters."""

    side_effects = (RiskClass.READ_ONLY, RiskClass.LOCAL_DRAFT)
    return (
        AdapterRegistration.from_runtime_adapter(
            CodexCliExecRuntimeAdapter(**kwargs),
            side_effects=side_effects,
            replay_safe=False,
        ),
        AdapterRegistration.from_runtime_adapter(
            CodexCliSessionRuntimeAdapter(**kwargs),
            side_effects=side_effects,
            replay_safe=False,
        ),
    )


def _output_paths(config: _CodexCliConfig, invocation: AdapterInvocation) -> Any:
    class _OutputPathContext:
        def __enter__(self) -> dict[str, Path]:
            if config.artifact_dir:
                root = Path(config.artifact_dir).expanduser().resolve()
            else:
                root = Path(config.cwd or os.getcwd()).resolve() / ".awk-live" / "codex-cli"
            root.mkdir(parents=True, exist_ok=True)
            safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", invocation.invocation_id)
            self.paths = {
                "last_message": root / f"{safe_id}.last.md",
                "events": root / f"{safe_id}.events.jsonl",
                "stderr": root / f"{safe_id}.stderr.txt",
            }
            return self.paths

        def __exit__(self, *_exc: Any) -> None:
            return None

    return _OutputPathContext()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _plain_runtime_ref(value: RuntimeRef | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, RuntimeRef):
        return to_plain_data(asdict(value))
    return dict(value)


def _synthetic_invocation(adapter_id: str, operation: str, key: str) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"{adapter_id}:{operation}:{key}",
        workflow_id="codex-cli-runtime",
        instance_id="codex-cli-runtime",
        stage_run_id=key,
        adapter_family=AdapterFamily.RUNTIME,
        adapter_id=adapter_id,
        operation=operation,
        idempotency_key=key,
    )


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _int(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_jsonl(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _find_session_id(events: Iterable[Mapping[str, Any]], stdout: str) -> str | None:
    for event in events:
        found = _find_session_id_in_value(event)
        if found:
            return found
    match = _UUID_RE.search(stdout)
    return match.group(0) if match else None


def _find_session_id_in_value(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("session_id", "conversation_id", "thread_id"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        for key, item in value.items():
            if key in {"session", "conversation", "thread"}:
                found = _find_session_id_in_value(item)
                if found:
                    return found
        for item in value.values():
            found = _find_session_id_in_value(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_session_id_in_value(item)
            if found:
                return found
    return None


def _collect_usage(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for event in events:
        for candidate in _usage_candidates(event):
            for key, value in candidate.items():
                if isinstance(value, int):
                    usage[key] = usage.get(key, 0) + value
    aliases = {
        "input_tokens": ("prompt_tokens",),
        "output_tokens": ("completion_tokens",),
    }
    for canonical, alternatives in aliases.items():
        if canonical not in usage:
            for alternative in alternatives:
                if alternative in usage:
                    usage[canonical] = usage[alternative]
                    break
    if "total_tokens" not in usage:
        total = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        if total:
            usage["total_tokens"] = total
    return usage


def _usage_candidates(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        usage = value.get("usage")
        if isinstance(usage, Mapping):
            yield usage
        for item in value.values():
            yield from _usage_candidates(item)
    elif isinstance(value, list):
        for item in value:
            yield from _usage_candidates(item)


def _parse_structured_last_message(last_message: str) -> Any:
    text = last_message.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _redacted_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for item in command:
        if skip_next:
            redacted.append("<redacted-path>" if item != "-" else "-")
            skip_next = False
            continue
        redacted.append(item)
        if item in {"--output-last-message", "--output-schema"}:
            skip_next = True
    return redacted


def _uri(path: Path) -> str:
    return f"file://{path}"


def _artifact_refs(
    *,
    invocation: AdapterInvocation,
    paths: Mapping[str, Path],
    adapter_id: str,
    created_at: str,
) -> tuple[ArtifactRef, ...]:
    refs: list[ArtifactRef] = []
    for role, path in paths.items():
        if not path.exists():
            continue
        content = path.read_bytes()
        refs.append(
            ArtifactRef(
                artifact_id=f"{adapter_id}:{invocation.invocation_id}:{role}",
                role=role,
                uri=_uri(path),
                content_hash=f"sha256:{hashlib.sha256(content).hexdigest()}",
                mime_type="application/jsonl" if role == "events" else "text/plain",
                size_bytes=len(content),
                created_by=adapter_id,
                visibility="internal",
            )
        )
    return tuple(refs)


def _summary_for_result(
    *,
    status: str,
    session_mode: bool,
    reused: bool,
    session_trackable: bool,
) -> str:
    if status == ADAPTER_STATUS_TIMED_OUT:
        return "Codex CLI invocation timed out."
    if status == ADAPTER_STATUS_FAILED:
        if session_mode and not session_trackable:
            return "Codex CLI session invocation completed but no reusable session id was captured."
        return "Codex CLI invocation failed."
    if session_mode:
        return "Codex CLI bounded session resumed." if reused else "Codex CLI bounded session started."
    return "Codex CLI one-shot invocation completed."
