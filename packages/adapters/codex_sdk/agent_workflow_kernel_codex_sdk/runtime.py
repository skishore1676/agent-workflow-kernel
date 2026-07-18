"""Runtime adapter for the official Codex Python SDK."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

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
    unsupported_operation_result,
)
from agent_workflow_kernel import (
    ADAPTER_STATUS_CANCELLED,
    ADAPTER_STATUS_FAILED,
    ADAPTER_STATUS_SUCCEEDED,
)


CODEX_SDK_ADAPTER_VERSION = "codex_sdk_session_runtime.v1"


@dataclass(slots=True)
class CodexSdkSessionState:
    session_key: str
    thread_id: str
    turn_count: int = 0
    last_turn_id: str | None = None
    last_invocation_id: str | None = None
    last_stage_run_id: str | None = None
    last_updated_at: str | None = None


@dataclass(slots=True, frozen=True)
class _CodexSdkConfig:
    cwd: str | None
    model: str | None
    sandbox: str | None
    approval_mode: str
    developer_instructions: str | None
    base_instructions: str | None
    service_tier: str | None
    output_schema: dict[str, Any] | None
    sdk_config: dict[str, Any] | None
    timeout_seconds: int
    artifact_dir: str | None
    max_session_turns: int
    ephemeral: bool | None


class CodexSdkSessionRuntimeAdapter:
    """AWK runtime adapter backed by ``openai_codex.Codex`` threads."""

    adapter_id = "runtime.codex_sdk_session"
    family = AdapterFamily.RUNTIME
    operations = ("invoke", "execute", "poll", "cancel", "collect_proof", "recover")

    def __init__(
        self,
        *,
        default_cwd: str | None = None,
        default_model: str | None = None,
        default_sandbox: str | None = "read-only",
        default_approval_mode: str = "deny_all",
        timeout_seconds: int = 900,
        max_session_turns: int = 20,
        client_factory: Callable[[], Any] | None = None,
        sdk_module: Mapping[str, Any] | None = None,
    ) -> None:
        self.default_cwd = default_cwd
        self.default_model = default_model
        self.default_sandbox = default_sandbox
        self.default_approval_mode = default_approval_mode
        self.timeout_seconds = timeout_seconds
        self.max_session_turns = max_session_turns
        self._client_factory = client_factory
        self._sdk_module = dict(sdk_module) if sdk_module is not None else None
        self.sessions: dict[str, CodexSdkSessionState] = {}
        self.receipts: list[Receipt] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=(
                "codex_python_sdk",
                "official_openai_codex",
                "thread_start",
                "thread_resume",
                "thread_run",
                "bounded_session",
            ),
            metadata={
                "schema": CODEX_SDK_ADAPTER_VERSION,
                "preferred": True,
                "fallback_adapter": "runtime.codex_cli_session",
                "session_resume": "explicit_thread_id",
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

        created_at = _now_iso()
        config = self._config(runtime_input)
        prompt = self._render_prompt(invocation, runtime_input)
        session_key = self._session_key(invocation, runtime_input)
        prior_session = self.sessions.get(session_key)
        requested_thread_id = _string(runtime_input.get("thread_id")) or _string(
            _nested(runtime_input, "codex_sdk", "thread_id")
        )
        resumed_thread_id = requested_thread_id or (prior_session.thread_id if prior_session else None)
        if prior_session and prior_session.turn_count >= config.max_session_turns:
            resumed_thread_id = None

        with _output_paths(config, invocation) as paths:
            try:
                sdk = self._load_sdk()
                client = self._build_client(sdk)
                try:
                    thread = self._start_or_resume_thread(
                        client=client,
                        sdk=sdk,
                        config=config,
                        thread_id=resumed_thread_id,
                    )
                    turn_result = thread.run(
                        prompt,
                        approval_mode=_enum_value(sdk.get("ApprovalMode"), config.approval_mode),
                        cwd=config.cwd,
                        model=config.model,
                        output_schema=config.output_schema,
                        sandbox=_enum_value(sdk.get("Sandbox"), config.sandbox),
                        service_tier=config.service_tier,
                    )
                finally:
                    close = getattr(client, "close", None)
                    if callable(close):
                        close()
            except Exception as exc:
                return self._failed_result(
                    invocation=invocation,
                    created_at=created_at,
                    paths=paths,
                    session_key=session_key,
                    requested_thread_id=resumed_thread_id,
                    error=exc,
                )

            final_response = _string(getattr(turn_result, "final_response", None)) or ""
            turn_payload = _plain_sdk_data(turn_result)
            thread_id = _string(getattr(thread, "id", None)) or resumed_thread_id
            turn_id = _string(getattr(turn_result, "id", None))
            status_value = _status_value(getattr(turn_result, "status", None))
            usage = _collect_usage(getattr(turn_result, "usage", None))
            structured_result = _parse_structured_last_message(final_response)

            paths["last_message"].write_text(final_response, encoding="utf-8")
            paths["turn_result"].write_text(
                json.dumps(turn_payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            paths["metadata"].write_text(
                json.dumps(
                    {
                        "schema": CODEX_SDK_ADAPTER_VERSION,
                        "adapter_id": self.adapter_id,
                        "sdk_version": sdk.get("version"),
                        "session_key": session_key,
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "resumed": bool(resumed_thread_id),
                        "status": status_value,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            session_trackable = bool(thread_id)
            sdk_completed = status_value in {"completed", "succeeded", "success", "done"}
            status = (
                ADAPTER_STATUS_SUCCEEDED
                if session_trackable and sdk_completed
                else ADAPTER_STATUS_FAILED
            )
            if session_trackable and status == ADAPTER_STATUS_SUCCEEDED:
                state = self.sessions.get(session_key) or CodexSdkSessionState(
                    session_key=session_key,
                    thread_id=thread_id or "",
                )
                state.thread_id = thread_id or state.thread_id
                state.turn_count += 1
                state.last_turn_id = turn_id
                state.last_invocation_id = invocation.invocation_id
                state.last_stage_run_id = invocation.stage_run_id
                state.last_updated_at = created_at
                self.sessions[session_key] = state

            output_uris = {role: _uri(path) for role, path in paths.items()}
            outputs: dict[str, Any] = {
                "schema": CODEX_SDK_ADAPTER_VERSION,
                "adapter_id": self.adapter_id,
                "mode": "bounded_session",
                "sdk": {
                    "package": "openai-codex",
                    "module": "openai_codex",
                    "version": sdk.get("version"),
                },
                "cwd": config.cwd,
                "session": {
                    "session_key": session_key,
                    "thread_id": thread_id,
                    "session_id": thread_id,
                    "session_reused": bool(resumed_thread_id),
                    "session_trackable": session_trackable,
                    "turn_id": turn_id,
                    "turn_count": self.sessions[session_key].turn_count
                    if session_key in self.sessions
                    else 0,
                    "max_session_turns": config.max_session_turns,
                },
                "usage": usage,
                "last_message": final_response,
                "structured_result": structured_result,
                "turn_result": turn_payload,
                "artifacts": output_uris,
            }
            artifact_refs = _artifact_refs(
                invocation=invocation,
                paths=paths,
                adapter_id=self.adapter_id,
            )
            receipt = make_adapter_receipt(
                invocation,
                status=status,
                summary=_summary_for_result(
                    status=status,
                    reused=bool(resumed_thread_id),
                    session_trackable=session_trackable,
                    sdk_status=status_value,
                ),
                created_at=created_at,
                artifact_refs=artifact_refs,
                outputs=outputs,
                checks_run=(
                    "openai_codex_importable",
                    "codex_sdk_thread_resumed" if resumed_thread_id else "codex_sdk_thread_started",
                    "codex_sdk_thread_run",
                    "thread_id_captured",
                ),
                residual_risk=None
                if status == ADAPTER_STATUS_SUCCEEDED
                else "Codex SDK invocation did not produce a reusable successful thread result.",
                next_action=None
                if status == ADAPTER_STATUS_SUCCEEDED
                else "Inspect SDK artifacts and verify openai-codex auth/runtime before retrying.",
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
            summary="Codex SDK session state found." if state else "Codex SDK session state not found.",
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
            summary=f"Codex SDK bounded session cancelled: {reason}",
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
            summary="Codex SDK proof collected." if state else "No Codex SDK session proof found.",
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
                state.thread_id,
                state.last_turn_id,
                state.last_invocation_id,
                state.last_stage_run_id,
            }
        ]
        outputs = {"idempotency_key": idempotency_key, "sessions": matched}
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if matched else ADAPTER_STATUS_FAILED,
            summary="Codex SDK recovery found session state."
            if matched
            else "Codex SDK recovery found no session state.",
            created_at=_now_iso(),
            outputs=outputs,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def _config(self, runtime_input: Mapping[str, Any]) -> _CodexSdkConfig:
        raw = runtime_input.get("codex_sdk")
        config = raw if isinstance(raw, Mapping) else {}
        timeout = _int(config.get("timeout_seconds"), self.timeout_seconds)
        max_turns = _int(config.get("max_session_turns"), self.max_session_turns)
        output_schema = config.get("output_schema")
        sdk_config = config.get("config")
        return _CodexSdkConfig(
            cwd=_string(config.get("cwd")) or self.default_cwd,
            model=_string(config.get("model")) or self.default_model,
            sandbox=_string(config.get("sandbox")) or self.default_sandbox,
            approval_mode=_approval_mode(
                _string(config.get("approval_mode")) or self.default_approval_mode
            ),
            developer_instructions=_string(config.get("developer_instructions")),
            base_instructions=_string(config.get("base_instructions")),
            service_tier=_string(config.get("service_tier")),
            output_schema=dict(output_schema) if isinstance(output_schema, Mapping) else None,
            sdk_config=dict(sdk_config) if isinstance(sdk_config, Mapping) else None,
            timeout_seconds=max(timeout, 1),
            artifact_dir=_string(config.get("artifact_dir")),
            max_session_turns=max(max_turns, 1),
            ephemeral=config.get("ephemeral") if isinstance(config.get("ephemeral"), bool) else None,
        )

    def _render_prompt(
        self,
        invocation: AdapterInvocation,
        runtime_input: Mapping[str, Any],
    ) -> str:
        for key in ("prompt", "rendered_prompt", "objective", "task"):
            value = runtime_input.get(key)
            if isinstance(value, str) and value.strip():
                return value
        codex_sdk = runtime_input.get("codex_sdk")
        if isinstance(codex_sdk, Mapping):
            prompt = codex_sdk.get("prompt")
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
            _nested(runtime_input, "codex_sdk", "session_key")
        )
        if explicit:
            return explicit
        actor_ref = _string(runtime_input.get("actor_ref")) or _string(
            _nested(runtime_input, "stage", "actors", "primary")
        )
        actor_part = actor_ref or "default_actor"
        return f"{invocation.workflow_id}:{invocation.instance_id}:{actor_part}"

    def _load_sdk(self) -> dict[str, Any]:
        if self._sdk_module is not None:
            return self._sdk_module
        try:
            from openai_codex import Codex, Sandbox, __version__  # type: ignore
            from openai_codex.api import ApprovalMode  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "openai-codex is not importable. Install it in a local venv, "
                "for example: python3 -m pip install openai-codex"
            ) from exc
        return {
            "Codex": Codex,
            "Sandbox": Sandbox,
            "ApprovalMode": ApprovalMode,
            "version": __version__,
        }

    def _build_client(self, sdk: Mapping[str, Any]) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        codex = sdk.get("Codex")
        if codex is None:
            raise RuntimeError("openai-codex SDK mapping did not provide Codex.")
        return codex()

    def _start_or_resume_thread(
        self,
        *,
        client: Any,
        sdk: Mapping[str, Any],
        config: _CodexSdkConfig,
        thread_id: str | None,
    ) -> Any:
        common = {
            "approval_mode": _enum_value(sdk.get("ApprovalMode"), config.approval_mode),
            "base_instructions": config.base_instructions,
            "config": config.sdk_config,
            "cwd": config.cwd,
            "developer_instructions": config.developer_instructions,
            "model": config.model,
            "sandbox": _enum_value(sdk.get("Sandbox"), config.sandbox),
            "service_tier": config.service_tier,
        }
        kwargs = {key: value for key, value in common.items() if value is not None}
        if thread_id:
            return client.thread_resume(thread_id, **kwargs)
        if config.ephemeral is not None:
            kwargs["ephemeral"] = config.ephemeral
        return client.thread_start(**kwargs)

    def _failed_result(
        self,
        *,
        invocation: AdapterInvocation,
        created_at: str,
        paths: Mapping[str, Path],
        session_key: str,
        requested_thread_id: str | None,
        error: Exception,
    ) -> AdapterResult:
        paths["last_message"].write_text("", encoding="utf-8")
        error_payload = {
            "schema": CODEX_SDK_ADAPTER_VERSION,
            "adapter_id": self.adapter_id,
            "error": {
                "class": error.__class__.__name__,
                "message": str(error),
            },
            "session_key": session_key,
            "requested_thread_id": requested_thread_id,
        }
        paths["metadata"].write_text(
            json.dumps(error_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        paths["turn_result"].write_text(
            json.dumps(error_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        artifact_refs = _artifact_refs(
            invocation=invocation,
            paths=paths,
            adapter_id=self.adapter_id,
        )
        outputs = {
            "schema": CODEX_SDK_ADAPTER_VERSION,
            "adapter_id": self.adapter_id,
            "mode": "bounded_session",
            "session": {
                "session_key": session_key,
                "thread_id": requested_thread_id,
                "session_id": requested_thread_id,
                "session_reused": bool(requested_thread_id),
                "session_trackable": False,
                "turn_count": 0,
            },
            "error": error_payload["error"],
            "artifacts": {role: _uri(path) for role, path in paths.items()},
        }
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_FAILED,
            summary="Codex SDK invocation failed before a reusable successful turn was captured.",
            created_at=created_at,
            artifact_refs=artifact_refs,
            outputs=outputs,
            checks_run=("openai_codex_importable", "codex_sdk_thread_run"),
            residual_risk="Codex SDK dependency, auth, or runtime failed.",
            next_action="Install/import openai-codex in a local venv and verify Codex auth before retrying.",
        )
        self.receipts.append(receipt)
        return result_from_receipt(
            invocation,
            receipt,
            outputs=outputs,
            artifact_refs=artifact_refs,
        )


def codex_sdk_runtime_registrations(**kwargs: Any) -> tuple[AdapterRegistration, ...]:
    """Build the preferred AWK registration for the official Codex SDK runtime."""

    return (
        AdapterRegistration.from_runtime_adapter(
            CodexSdkSessionRuntimeAdapter(**kwargs),
            side_effects=(RiskClass.READ_ONLY, RiskClass.LOCAL_DRAFT),
            replay_safe=False,
        ),
    )


def _output_paths(config: _CodexSdkConfig, invocation: AdapterInvocation) -> Any:
    class _OutputPathContext:
        def __enter__(self) -> dict[str, Path]:
            if config.artifact_dir:
                root = Path(config.artifact_dir).expanduser().resolve()
            else:
                root = Path(config.cwd or os.getcwd()).resolve() / ".awk-live" / "codex-sdk"
            root.mkdir(parents=True, exist_ok=True)
            safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", invocation.invocation_id)
            self.paths = {
                "last_message": root / f"{safe_id}.last.md",
                "turn_result": root / f"{safe_id}.turn.json",
                "metadata": root / f"{safe_id}.metadata.json",
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
        workflow_id="codex-sdk-runtime",
        instance_id="codex-sdk-runtime",
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


def _nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _approval_mode(value: str | None) -> str:
    normalized = (value or "deny_all").strip().lower().replace("-", "_")
    aliases = {
        "never": "deny_all",
        "none": "deny_all",
        "deny": "deny_all",
        "auto": "auto_review",
        "review": "auto_review",
    }
    return aliases.get(normalized, normalized)


def _enum_value(enum_type: Any, value: str | None) -> Any:
    if value is None:
        return None
    normalized = value.strip().replace("-", "_")
    if enum_type is None:
        return value
    try:
        return enum_type[normalized]
    except Exception:
        pass
    try:
        return enum_type(value)
    except Exception:
        return value


def _status_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, str):
        return value
    return str(value) if value is not None else ""


def _plain_sdk_data(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return _plain_sdk_data(value.model_dump(mode="json", by_alias=False))
    if hasattr(value, "dict") and callable(value.dict):
        return _plain_sdk_data(value.dict())
    if hasattr(value, "__dataclass_fields__"):
        return _plain_sdk_data(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _plain_sdk_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_sdk_data(item) for item in value]
    return repr(value)


def _collect_usage(value: Any) -> dict[str, int]:
    plain = _plain_sdk_data(value)
    usage: dict[str, int] = {}
    if isinstance(plain, Mapping):
        total = plain.get("total")
        if isinstance(total, Mapping):
            _merge_usage(usage, total)
        last = plain.get("last")
        if isinstance(last, Mapping):
            for key, item in last.items():
                if isinstance(item, int):
                    usage[f"last_{_snake(key)}"] = item
        _merge_usage(usage, plain)
    aliases = {
        "input_tokens": ("input", "prompt_tokens"),
        "output_tokens": ("output", "completion_tokens"),
    }
    for canonical, alternatives in aliases.items():
        if canonical not in usage:
            for alternative in alternatives:
                if alternative in usage:
                    usage[canonical] = usage[alternative]
                    break
    if "total_tokens" not in usage:
        total_tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        if total_tokens:
            usage["total_tokens"] = total_tokens
    return usage


def _merge_usage(usage: dict[str, int], value: Mapping[str, Any]) -> None:
    for key, item in value.items():
        if isinstance(item, int):
            usage[_snake(str(key))] = item


def _snake(value: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", value).lower()


def _parse_structured_last_message(last_message: str) -> Any:
    text = last_message.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _uri(path: Path) -> str:
    return f"file://{path}"


def _artifact_refs(
    *,
    invocation: AdapterInvocation,
    paths: Mapping[str, Path],
    adapter_id: str,
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
                mime_type="application/json" if role != "last_message" else "text/plain",
                size_bytes=len(content),
                created_by=adapter_id,
                visibility="internal",
            )
        )
    return tuple(refs)


def _summary_for_result(
    *,
    status: str,
    reused: bool,
    session_trackable: bool,
    sdk_status: str,
) -> str:
    if status == ADAPTER_STATUS_FAILED:
        if not session_trackable:
            return "Codex SDK invocation completed but no reusable thread id was captured."
        return f"Codex SDK invocation did not complete successfully: {sdk_status or 'unknown'}."
    return "Codex SDK bounded session resumed." if reused else "Codex SDK bounded session started."
