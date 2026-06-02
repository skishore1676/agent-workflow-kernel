"""Guarded OpenClaw Telegram surface adapter."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from agent_workflow_kernel import (
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    Receipt,
    SurfaceRef,
    to_plain_data,
)
from agent_workflow_kernel.adapters import (
    ADAPTER_STATUS_BLOCKED,
    ADAPTER_STATUS_SUCCEEDED,
    CapabilitySet,
    SurfaceCapabilityContract,
    ensure_invocation_family,
    make_adapter_receipt,
    result_from_receipt,
    unsupported_operation_result,
)


DETERMINISTIC_CREATED_AT = "2000-01-01T00:00:00Z"
OPENCLAW_TELEGRAM_MESSAGE_SCHEMA = "openclaw_telegram_message.v1"
LIVE_OPERATOR_SURFACE_DECISION_SCHEMA = "live_operator_surface_decision.v1"


class OpenClawTelegramSurfaceAdapter:
    """Guarded live Telegram delivery via the OpenClaw CLI."""

    adapter_id = "surface.openclaw_telegram"
    family = AdapterFamily.SURFACE
    operations = ("publish", "readback", "ingest_decisions", "clear", "validate")

    def __init__(
        self,
        *,
        account: str,
        target: str,
        allow_live_send: bool = False,
        created_at: str = DETERMINISTIC_CREATED_AT,
        canonical_surface: str = "openclaw_telegram",
        receipt_dir: str | Path | None = None,
        command: Sequence[str] = ("openclaw",),
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self.account = account
        self.target = target
        self.allow_live_send = allow_live_send
        self.created_at = created_at
        self.canonical_surface = canonical_surface
        self.receipt_dir = Path(receipt_dir).resolve() if receipt_dir is not None else None
        self.command = tuple(command)
        self.runner = runner or subprocess.run
        self.receipts: list[Receipt] = []
        self.sent_messages: dict[str, dict[str, Any]] = {}
        if self.receipt_dir is not None:
            self.receipt_dir.mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> CapabilitySet:
        contract = SurfaceCapabilityContract(
            surface_kind="telegram_message",
            mode="live",
            live_mutation_allowed=self.allow_live_send,
            dry_run_only=False,
            readback_required=True,
            decision_ingest_supported=False,
            clear_requires_live_mutation=True,
            external_effects=("telegram_send",),
            receipt_schema=OPENCLAW_TELEGRAM_MESSAGE_SCHEMA,
            metadata={
                "delivery": "openclaw_cli",
                "channel": "telegram",
                "account": self.account,
                "target": self.target,
                "requires_packet_live_operator_surface_allowed": True,
                "public_publish_blocked": True,
            },
        )
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=("live", "telegram", "openclaw_cli", "readback", "fail_closed"),
            metadata={
                "schema": OPENCLAW_TELEGRAM_MESSAGE_SCHEMA,
                "mutation_mode": "live",
                "write_class": "live_operator_surface",
                "live": self.allow_live_send,
                "live_mutation_allowed": self.allow_live_send,
                "network_calls_allowed": self.allow_live_send,
                "risk_policy": {
                    "side_effect": "external_effect",
                    "external_send": True,
                    "public_publish_blocked": True,
                    "production_effect": False,
                    "fail_closed_on_unknown_or_unsafe": True,
                },
                "surface_contract": contract.as_metadata(),
            },
        )

    def publish(
        self,
        invocation: AdapterInvocation,
        surface_packet: Mapping[str, Any],
    ) -> AdapterResult:
        ensure_invocation_family(invocation, self.family)
        if not self.capabilities().supports(invocation.operation):
            return unsupported_operation_result(
                invocation,
                created_at=self.created_at,
                supported_operations=self.operations,
            )
        packet = dict(surface_packet)
        stage_id = str(packet.get("stage_id") or invocation.stage_run_id)
        safety_error = None
        if not self.allow_live_send:
            safety_error = _live_mutation_error("OpenClaw Telegram send requires allow_live_send=True.")
        if safety_error is None:
            safety_error = _live_operator_surface_safety_error(packet, effect_name="OpenClaw Telegram send")
        if safety_error is not None:
            return self._blocked_result(
                invocation,
                status_summary=safety_error["message"],
                outputs={
                    "schema": OPENCLAW_TELEGRAM_MESSAGE_SCHEMA,
                    "error": safety_error,
                    "surface_packet": _redact_sensitive_mapping(packet),
                    "network_call_performed": False,
                    "live_mutation_performed": False,
                },
                checks_run=("operation_supported", "live_send_authorized", "unsafe_action_guard"),
                next_action="retry only after explicit live operator-surface send authorization",
            )

        message = str(packet.get("message") or _render_telegram_operator_message(packet)).strip()
        if not self.account or not self.target or not message:
            return self._blocked_result(
                invocation,
                status_summary="OpenClaw Telegram send missing account, target, or message.",
                outputs={
                    "schema": OPENCLAW_TELEGRAM_MESSAGE_SCHEMA,
                    "error": {
                        "error_class": "invalid_surface_packet",
                        "message": "account, target, and message are required",
                    },
                    "network_call_performed": False,
                    "live_mutation_performed": False,
                },
                checks_run=("operation_supported", "telegram_destination_present"),
                next_action="configure account and target, and provide a message",
            )

        replay = self._read_idempotency_receipt(invocation)
        if replay is not None:
            outputs = dict(replay)
            outputs["idempotency_replayed"] = True
            receipt = make_adapter_receipt(
                invocation,
                status=ADAPTER_STATUS_SUCCEEDED,
                summary="OpenClaw Telegram send replayed from local receipt.",
                created_at=self.created_at,
                stage_id=stage_id,
                outputs=outputs,
                checks_run=("operation_supported", "idempotency_receipt_read"),
            )
            self.receipts.append(receipt)
            return result_from_receipt(invocation, receipt, outputs=outputs)

        command = [
            *self.command,
            "message",
            "send",
            "--channel",
            "telegram",
            "--account",
            self.account,
            "--target",
            self.target,
            "--message",
            message,
            "--json",
        ]
        completed = self.runner(command, capture_output=True, text=True, check=False)
        stdout = str(getattr(completed, "stdout", "") or "")
        stderr = str(getattr(completed, "stderr", "") or "")
        returncode = int(getattr(completed, "returncode", 1))
        command_json = _loads_json_mapping(stdout)
        if returncode != 0:
            error = {
                "error_class": "openclaw_telegram_send_failed",
                "message": "OpenClaw Telegram send command failed.",
                "returncode": returncode,
                "stderr": _redact_sensitive_text(stderr),
            }
            return self._blocked_result(
                invocation,
                status_summary=error["message"],
                outputs={
                    "schema": OPENCLAW_TELEGRAM_MESSAGE_SCHEMA,
                    "error": error,
                    "command": _redacted_telegram_command(command),
                    "command_json": _redact_sensitive_mapping(command_json),
                    "network_call_performed": True,
                    "live_mutation_performed": False,
                },
                checks_run=("operation_supported", "openclaw_cli_invoked", "command_returncode_zero"),
                next_action="inspect the OpenClaw Telegram command failure before retrying",
            )

        message_id = str(
            command_json.get("message_id")
            or command_json.get("id")
            or command_json.get("external_id")
            or invocation.idempotency_key
            or invocation.invocation_id
        )
        surface_ref = {
            "surface_id": f"surface:{invocation.invocation_id}",
            "kind": "telegram_message",
            "external_id": message_id,
            "title": str(packet.get("title") or "OpenClaw Telegram message"),
            "readback_required": True,
            "status": "sent",
        }
        outputs = {
            "schema": OPENCLAW_TELEGRAM_MESSAGE_SCHEMA,
            "surface_ref": surface_ref,
            "canonical_surface": self.canonical_surface,
            "channel": "telegram",
            "account": self.account,
            "target": self.target,
            "message_id": message_id,
            "command": _redacted_telegram_command(command),
            "command_json": _redact_sensitive_mapping(command_json),
            "command_stdout_present": bool(stdout.strip()),
            "network_call_performed": True,
            "live_mutation_performed": True,
            "live_operator_surface_allowed": True,
            "public_publish_blocked": True,
            "idempotency_key": invocation.idempotency_key,
            "idempotency_replayed": False,
        }
        self.sent_messages[surface_ref["surface_id"]] = outputs
        self._write_idempotency_receipt(invocation, outputs)
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED,
            summary="OpenClaw Telegram message sent with command JSON readback.",
            created_at=self.created_at,
            stage_id=stage_id,
            outputs=outputs,
            checks_run=("operation_supported", "live_send_authorized", "unsafe_action_guard", "openclaw_cli_invoked"),
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def readback(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="readback",
            idempotency_key=str(ref.get("surface_id") or ref.get("external_id") or "openclaw-telegram"),
        )
        sent = self.sent_messages.get(str(ref.get("surface_id"))) or self._read_receipt_from_ref(ref)
        exists = sent is not None
        outputs = {
            "schema": OPENCLAW_TELEGRAM_MESSAGE_SCHEMA,
            "surface_ref": ref,
            "send_receipt": sent or {},
            "readback_confirmed": exists,
            "network_call_performed": False,
            "live_mutation_performed": False,
            "public_publish_blocked": True,
        }
        summary = "OpenClaw Telegram send receipt read back." if exists else "OpenClaw Telegram send receipt is missing."
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if exists else ADAPTER_STATUS_BLOCKED,
            summary=summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=("local_send_receipt_lookup",),
            residual_risk=None if exists else summary,
            next_action=None if exists else "publish the Telegram message before readback",
        )
        self.receipts.append(receipt)
        return receipt

    def ingest_decisions(self, surface_query: Mapping[str, Any]) -> list[Receipt]:
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="ingest_decisions",
            idempotency_key=str(surface_query.get("query_id", "openclaw-telegram-decision")),
        )
        summary = "OpenClaw Telegram adapter does not ingest live chat decisions."
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=summary,
            created_at=self.created_at,
            outputs={
                "schema": LIVE_OPERATOR_SURFACE_DECISION_SCHEMA,
                "surface_query": _redact_sensitive_mapping(surface_query),
                "error": {"error_class": "decision_ingest_not_supported", "message": summary},
                "live_mutation_performed": False,
            },
            checks_run=("decision_ingest_disabled_for_live_telegram",),
            residual_risk=summary,
            next_action="use a canonical decision-ingest surface such as Obsidian Markdown",
        )
        self.receipts.append(receipt)
        return [receipt]

    def clear(self, surface_ref: SurfaceRef | Mapping[str, Any], reason: str) -> Receipt:
        ref = _plain_mapping(surface_ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="clear",
            idempotency_key=str(ref.get("surface_id") or "openclaw-telegram"),
        )
        error = _live_mutation_error("OpenClaw Telegram clear/delete is not authorized by this adapter.")
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=error["message"],
            created_at=self.created_at,
            outputs={
                "schema": OPENCLAW_TELEGRAM_MESSAGE_SCHEMA,
                "surface_ref": ref,
                "reason": reason,
                "error": error,
                "network_call_performed": False,
                "live_mutation_performed": False,
            },
            checks_run=("telegram_clear_refused",),
            residual_risk=error["message"],
            next_action="create a fresh explicit gate for any live Telegram cleanup request",
        )
        self.receipts.append(receipt)
        return receipt

    def validate(self, surface_ref: SurfaceRef | Mapping[str, Any]) -> Receipt:
        ref = _plain_mapping(surface_ref)
        readback = self.readback(ref)
        invocation = _synthetic_invocation(
            adapter_family=self.family,
            adapter_id=self.adapter_id,
            operation="validate",
            idempotency_key=str(ref.get("surface_id") or "openclaw-telegram"),
        )
        valid = readback.status == ADAPTER_STATUS_SUCCEEDED
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_SUCCEEDED if valid else ADAPTER_STATUS_BLOCKED,
            summary="OpenClaw Telegram send receipt validation completed.",
            created_at=self.created_at,
            outputs={
                "schema": OPENCLAW_TELEGRAM_MESSAGE_SCHEMA,
                "surface_ref": ref,
                "valid": valid,
                "readback_receipt_ref": readback.receipt_id,
                "network_call_performed": False,
                "public_publish_blocked": True,
            },
            checks_run=("readback_exists",),
            residual_risk=None if valid else readback.summary,
        )
        self.receipts.append(receipt)
        return receipt

    def _blocked_result(
        self,
        invocation: AdapterInvocation,
        *,
        status_summary: str,
        outputs: Mapping[str, Any],
        checks_run: tuple[str, ...],
        next_action: str,
    ) -> AdapterResult:
        receipt = make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=status_summary,
            created_at=self.created_at,
            outputs=outputs,
            checks_run=checks_run,
            residual_risk=status_summary,
            next_action=next_action,
        )
        self.receipts.append(receipt)
        return result_from_receipt(invocation, receipt, outputs=outputs)

    def _idempotency_receipt_path(self, invocation: AdapterInvocation) -> Path | None:
        if self.receipt_dir is None:
            return None
        key = _slug(str(invocation.idempotency_key or invocation.invocation_id))
        return self.receipt_dir / f"{key}.send-receipt.json"

    def _read_idempotency_receipt(self, invocation: AdapterInvocation) -> dict[str, Any] | None:
        key = str(invocation.idempotency_key or invocation.invocation_id)
        for sent in self.sent_messages.values():
            if str(sent.get("idempotency_key") or "") == key:
                return sent
        path = self._idempotency_receipt_path(invocation)
        if path is not None and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def _write_idempotency_receipt(self, invocation: AdapterInvocation, outputs: Mapping[str, Any]) -> None:
        path = self._idempotency_receipt_path(invocation)
        if path is None:
            return
        path.write_text(json.dumps(outputs, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _read_receipt_from_ref(self, surface_ref: Mapping[str, Any]) -> dict[str, Any] | None:
        external_id = str(surface_ref.get("external_id") or "")
        for sent in self.sent_messages.values():
            if str(sent.get("message_id") or "") == external_id:
                return sent
        if self.receipt_dir is None:
            return None
        for path in self.receipt_dir.glob("*.send-receipt.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if str(payload.get("message_id") or "") == external_id:
                return payload
        return None


def _plain_mapping(value: Mapping[str, Any] | SurfaceRef) -> dict[str, Any]:
    if isinstance(value, SurfaceRef):
        return to_plain_data(asdict(value))
    return dict(value)


def _synthetic_invocation(
    *,
    adapter_family: AdapterFamily,
    adapter_id: str,
    operation: str,
    instance_id: str = "local-instance",
    stage_run_id: str = "local-stage-run",
    idempotency_key: str | None = None,
) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"{adapter_id}:{operation}:{idempotency_key or instance_id}",
        workflow_id="local-workflow",
        instance_id=instance_id,
        stage_run_id=stage_run_id,
        adapter_family=adapter_family,
        adapter_id=adapter_id,
        operation=operation,
        idempotency_key=idempotency_key,
    )


def _render_telegram_operator_message(packet: Mapping[str, Any]) -> str:
    title = str(packet.get("title") or "OpenClaw operator review")
    ask = str(packet.get("human_ask") or packet.get("ask") or packet.get("body") or "")
    exact_action = str(packet.get("exact_action") or packet.get("requested_action") or "")
    fingerprint = str(packet.get("action_fingerprint") or "")
    decisions = ", ".join(_string_tuple(packet.get("allowed_decisions", ()))) or "none"
    parts = [title]
    if ask:
        parts.append(ask)
    if exact_action:
        parts.append(f"Exact action: {exact_action}")
    if fingerprint:
        parts.append(f"Action fingerprint: {fingerprint}")
    parts.append(f"Allowed decisions: {decisions}")
    parts.append("Live operator-surface send authorized; public publish remains blocked.")
    return "\n".join(parts)


_UNSAFE_LIVE_SURFACE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("public_publish", r"\b(public\s+publish|publish\s+publicly|substack|medium|social\s+post|public\s+website|public\s+repo\s+release)\b"),
    ("trading_or_money", r"\b(live\s+trade|trade|trading|broker|order\s+placement|order\s+cancellation|position\s+size|money\s+movement|spend|purchase|transfer|billing|invoice)\b"),
    ("auth_or_secret", r"\b(auth|oauth|credential|credentials|secret|token|api\s*key|login|session)\b"),
    ("deploy_or_production", r"\b(deploy|deployment|production\s+mutation|migration|service\s+restart|runtime\s+mutation|oldmac\s+mutation)\b"),
    ("destructive", r"\b(delete|deletion|destroy|destructive|archive\s+without\s+recovery|irreversible|overwrite|prune|cleanup\s+job)\b"),
    ("unscoped_live_mutation", r"\b(unscoped\s+live|any\s+live\s+mutation|arbitrary\s+mutation|mutate\s+anything)\b"),
)


def _live_operator_surface_safety_error(
    payload: Mapping[str, Any],
    *,
    effect_name: str,
) -> dict[str, Any] | None:
    if not bool(payload.get("live_operator_surface_allowed", False)):
        return _live_mutation_error(f"{effect_name} requires live_operator_surface_allowed=True.")
    if bool(payload.get("public_publish_allowed", False)) or payload.get("public_publish_blocked", True) is False:
        return _live_mutation_error(f"{effect_name} refused a packet that did not keep public publish blocked.")
    if bool(payload.get("mutation_permission_granted", False)):
        return _live_mutation_error(f"{effect_name} refused ambiguous mutation permission.")
    unsafe = _unsafe_live_surface_hits(payload)
    if unsafe:
        return {
            "error_class": "unsafe_live_surface_scope_refused",
            "message": f"{effect_name} refused unsafe or out-of-scope live action language.",
            "retryable": False,
            "unsafe_hits": unsafe,
        }
    return None


def _unsafe_live_surface_hits(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    scan_keys = {
        "title",
        "human_ask",
        "ask",
        "body",
        "message",
        "requested_action",
        "exact_action",
        "exact_action_approved",
        "allowed_scope",
        "requested_effects",
        "risk_classes",
        "forbidden_actions",
        "side_effects",
    }
    hits: list[dict[str, str]] = []
    for key in scan_keys:
        if key not in payload:
            continue
        text = _stringify_for_scan(payload[key]).lower()
        if not text:
            continue
        for category, pattern in _UNSAFE_LIVE_SURFACE_PATTERNS:
            if re.search(pattern, text):
                hits.append({"field": key, "category": category})
    return hits


def _stringify_for_scan(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return " ".join(f"{key} {_stringify_for_scan(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringify_for_scan(item) for item in value)
    return str(value)


def _live_mutation_error(message: str) -> dict[str, Any]:
    return {
        "error_class": "live_mutation_refused",
        "message": message,
        "retryable": False,
    }


def _redact_sensitive_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    sensitive_markers = ("token", "secret", "password", "api_key", "apikey", "credential")
    for key, value in payload.items():
        key_text = str(key).lower()
        if any(marker in key_text for marker in sensitive_markers):
            redacted[str(key)] = "<redacted>"
        elif isinstance(value, Mapping):
            redacted[str(key)] = _redact_sensitive_mapping(value)
        else:
            redacted[str(key)] = value
    return redacted


def _redact_sensitive_text(text: str) -> str:
    if not text:
        return ""
    redacted = text
    for pattern in (
        r"(?i)(token|secret|password|api[_-]?key|credential)=\S+",
        r"(?i)(token|secret|password|api[_-]?key|credential):\s*\S+",
    ):
        redacted = re.sub(pattern, r"\1=<redacted>", redacted)
    return redacted


def _redacted_telegram_command(command: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            redacted.append("<redacted-message>")
            skip_next = False
            continue
        redacted.append(part)
        if part == "--message":
            skip_next = True
    return redacted


def _loads_json_mapping(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {"value": payload}


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return (str(value),)


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return slug or "item"
