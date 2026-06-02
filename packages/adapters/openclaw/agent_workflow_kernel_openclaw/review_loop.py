"""OpenClaw Blackboard decision-loop adapter.

This wraps OpenClaw's existing deterministic ingester and Jarvis pickup loop.
AWK should not reimplement those lane rules; it should invoke them through a
receipt-backed OpenClaw adapter boundary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from agent_workflow_kernel import AdapterFamily, AdapterInvocation, Receipt, digest_data
from agent_workflow_kernel.adapters import (
    ADAPTER_STATUS_BLOCKED,
    ADAPTER_STATUS_SUCCEEDED,
    CapabilitySet,
    make_adapter_receipt,
)


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


class OpenClawBlackboardDecisionLoopAdapter:
    """Run or inspect OpenClaw's Blackboard decision ingestion loop."""

    adapter_id = "host.openclaw.blackboard_decision_loop"
    family = AdapterFamily.HOST
    operations = (
        "refresh_blackboard",
        "publish_attention",
        "ingest_decisions",
        "plan_review_runner",
        "run_decision_loop",
    )

    def __init__(
        self,
        openclaw_root: str | Path,
        *,
        vault_root: str | Path | None = None,
        created_at: str | None = None,
        timeout_seconds: int = 2700,
        runner: CommandRunner | None = None,
    ) -> None:
        self.openclaw_root = Path(openclaw_root).expanduser().resolve()
        self.workspace_main = self.openclaw_root / "workspace-main"
        self.vault_root = Path(vault_root).expanduser().resolve() if vault_root is not None else None
        self.update_script = self.workspace_main / "scripts" / "update_review_inbox.py"
        self.publisher_script = self.workspace_main / "scripts" / "publish_or_research_attention.py"
        self.ingest_script = self.workspace_main / "scripts" / "ingest_agent_reviews.py"
        self.runner_script = self.workspace_main / "scripts" / "agent_review_runner.py"
        legacy_loop_script = (
            self.openclaw_root
            / "scripts"
            / "legacy"
            / "run_blackboard_decision_ingester.openclaw_direct_legacy.sh"
        )
        self.direct_loop_script = (
            legacy_loop_script if legacy_loop_script.exists() else self.openclaw_root / "scripts" / "run_blackboard_decision_ingester.sh"
        )
        self.created_at = created_at or _now_iso()
        self.timeout_seconds = timeout_seconds
        self._runner = runner or subprocess.run

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
            features=(
                "blackboard_refresh",
                "attention_handoff_publish",
                "checked_decision_ingest",
                "agent_review_runner_plan",
                "direct_openclaw_decision_loop",
            ),
            metadata={
                "openclaw_root": str(self.openclaw_root),
                "workspace_main": str(self.workspace_main),
                "live_apply_requires_explicit_allow": True,
                "agent_dispatch_requires_explicit_allow": True,
            },
        )

    def refresh_blackboard(self, invocation: AdapterInvocation, *, validate: bool = True) -> Receipt:
        validation = self._validate_common(required=(self.update_script,))
        if validation is not None:
            return self._blocked(invocation, validation, checks_run=("validate_openclaw_paths",))
        cmd = [sys.executable, str(self.update_script.name)]
        if validate:
            cmd.append("--validate")
        result = self._run(cmd, cwd=self.update_script.parent)
        return self._command_receipt(
            invocation,
            result=result,
            success_summary="OpenClaw Blackboard refreshed.",
            blocked_summary="OpenClaw Blackboard refresh failed.",
            checks_run=("validate_openclaw_paths", "run_update_review_inbox"),
        )

    def publish_attention(
        self,
        invocation: AdapterInvocation,
        *,
        if_present: bool = True,
        validate: bool = True,
        force: bool = False,
    ) -> Receipt:
        validation = self._validate_common(required=(self.publisher_script,))
        if validation is not None:
            return self._blocked(invocation, validation, checks_run=("validate_openclaw_paths",))
        cmd = [sys.executable, str(self.publisher_script.name)]
        if if_present:
            cmd.append("--if-present")
        if validate:
            cmd.append("--validate")
        if force:
            cmd.append("--force")
        result = self._run(cmd, cwd=self.publisher_script.parent)
        return self._command_receipt(
            invocation,
            result=result,
            success_summary="OpenClaw Blackboard attention handoff publisher completed.",
            blocked_summary="OpenClaw Blackboard attention handoff publisher failed.",
            checks_run=("validate_openclaw_paths", "run_publish_or_research_attention"),
            policy_snapshot={
                "writes_operator_surface": True,
                "external_publish_allowed": False,
                "telegram_send_allowed": False,
            },
        )

    def ingest_decisions(
        self,
        invocation: AdapterInvocation,
        *,
        apply: bool = False,
        allow_apply: bool = False,
        refresh_blackboard: bool = True,
        validate: bool = True,
        agent: str | None = None,
        force: bool = False,
    ) -> Receipt:
        validation = self._validate_common(required=(self.ingest_script,))
        if validation is not None:
            return self._blocked(invocation, validation, checks_run=("validate_openclaw_paths",))
        if apply and not allow_apply:
            return self._blocked(
                invocation,
                "ingest_decisions apply mode requires allow_apply=True",
                checks_run=("explicit_apply_gate",),
                next_action="Run again with allow_apply=True only when checked decisions should mutate OpenClaw handoff state.",
            )
        cmd = [sys.executable, str(self.ingest_script.name)]
        if agent:
            cmd.extend(["--agent", agent])
        if apply:
            cmd.append("--apply")
        if force:
            cmd.append("--force")
        if refresh_blackboard:
            cmd.append("--refresh-blackboard")
        if validate:
            cmd.append("--validate")
        result = self._run(cmd, cwd=self.ingest_script.parent)
        return self._command_receipt(
            invocation,
            result=result,
            success_summary="OpenClaw Blackboard review decisions ingested.",
            blocked_summary="OpenClaw Blackboard review decision ingest failed.",
            checks_run=("validate_openclaw_paths", "run_ingest_agent_reviews", "explicit_apply_gate"),
            policy_snapshot={"apply_requested": apply, "apply_allowed": allow_apply},
        )

    def plan_review_runner(self, invocation: AdapterInvocation, *, limit: int = 10) -> Receipt:
        validation = self._validate_common(required=(self.runner_script,))
        if validation is not None:
            return self._blocked(invocation, validation, checks_run=("validate_openclaw_paths",))
        cmd = [sys.executable, str(self.runner_script.name), "plan", "--limit", str(limit)]
        result = self._run(cmd, cwd=self.runner_script.parent)
        return self._command_receipt(
            invocation,
            result=result,
            success_summary="OpenClaw agent-review runner plan read.",
            blocked_summary="OpenClaw agent-review runner plan failed.",
            checks_run=("validate_openclaw_paths", "run_agent_review_runner_plan"),
        )

    def run_decision_loop(
        self,
        invocation: AdapterInvocation,
        *,
        allow_agent_dispatch: bool = False,
        telegram_target: str | None = None,
        telegram_account: str | None = None,
        review_runner_dispatch: str = "agent",
    ) -> Receipt:
        validation = self._validate_common(required=(self.direct_loop_script,))
        if validation is not None:
            return self._blocked(invocation, validation, checks_run=("validate_openclaw_paths",))
        if not allow_agent_dispatch:
            return self._blocked(
                invocation,
                "run_decision_loop requires allow_agent_dispatch=True",
                checks_run=("explicit_agent_dispatch_gate",),
                next_action="Use ingest_decisions/apply plus plan_review_runner for inspection, or explicitly allow the direct OpenClaw runner loop.",
            )
        env = self._env()
        env["BLACKBOARD_REVIEW_RUNNER_DISPATCH"] = review_runner_dispatch
        env["BLACKBOARD_LEGACY_SUPPRESS_LAUNCHD_EVENT"] = "1"
        env["BLACKBOARD_LEGACY_SUPPRESS_TELEGRAM"] = "1"
        if telegram_target:
            env["BLACKBOARD_INGESTER_TELEGRAM_TARGET"] = telegram_target
        if telegram_account:
            env["BLACKBOARD_INGESTER_TELEGRAM_ACCOUNT"] = telegram_account
        result = self._run([str(self.direct_loop_script)], cwd=self.openclaw_root, env=env)
        return self._command_receipt(
            invocation,
            result=result,
            success_summary="OpenClaw Blackboard decision loop completed.",
            blocked_summary="OpenClaw Blackboard decision loop failed.",
            checks_run=("validate_openclaw_paths", "explicit_agent_dispatch_gate", "run_direct_blackboard_decision_loop"),
            policy_snapshot={"agent_dispatch_allowed": allow_agent_dispatch, "review_runner_dispatch": review_runner_dispatch},
        )

    def _validate_common(self, *, required: tuple[Path, ...]) -> str | None:
        if not self.openclaw_root.exists():
            return f"missing OpenClaw root: {self.openclaw_root}"
        if not self.workspace_main.exists():
            return f"missing workspace-main: {self.workspace_main}"
        for path in required:
            if not path.exists():
                return f"missing required OpenClaw script: {path}"
        if self.vault_root is not None and not self.vault_root.exists():
            return f"missing vault root: {self.vault_root}"
        return None

    def _run(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            completed = self._runner(
                cmd,
                cwd=str(cwd),
                env=env or self._env(),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "status": "blocked",
                "command": _redacted_command(cmd),
                "cwd": str(cwd),
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "error": str(exc),
            }
        stdout = _short_text(completed.stdout)
        stderr = _short_text(completed.stderr)
        parsed = _json_or_none(completed.stdout)
        return {
            "status": "succeeded" if completed.returncode == 0 else "blocked",
            "command": _redacted_command(cmd),
            "cwd": str(cwd),
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "parsed_json": parsed,
            "output_hash": digest_data({"stdout": stdout, "stderr": stderr, "returncode": completed.returncode}),
        }

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.vault_root is not None:
            env["OPENCLAW_OBSIDIAN_VAULT"] = str(self.vault_root)
        return env

    def _command_receipt(
        self,
        invocation: AdapterInvocation,
        *,
        result: Mapping[str, Any],
        success_summary: str,
        blocked_summary: str,
        checks_run: tuple[str, ...],
        policy_snapshot: Mapping[str, Any] | None = None,
    ) -> Receipt:
        status = ADAPTER_STATUS_SUCCEEDED if result.get("status") == "succeeded" else ADAPTER_STATUS_BLOCKED
        return make_adapter_receipt(
            invocation,
            status=status,
            summary=success_summary if status == ADAPTER_STATUS_SUCCEEDED else blocked_summary,
            created_at=self.created_at,
            outputs={"command_result": dict(result)},
            checks_run=checks_run,
            policy_snapshot=dict(policy_snapshot or {}),
            residual_risk=None if status == ADAPTER_STATUS_SUCCEEDED else str(result.get("error") or result.get("stderr") or blocked_summary),
            next_action=None if status == ADAPTER_STATUS_SUCCEEDED else "Inspect the command output and OpenClaw runner receipts.",
        )

    def _blocked(
        self,
        invocation: AdapterInvocation,
        message: str,
        *,
        checks_run: tuple[str, ...],
        next_action: str | None = None,
    ) -> Receipt:
        return make_adapter_receipt(
            invocation,
            status=ADAPTER_STATUS_BLOCKED,
            summary=message,
            created_at=self.created_at,
            outputs={"error": {"error_class": "openclaw_decision_loop_blocked", "message": message}},
            checks_run=checks_run,
            residual_risk=message,
            next_action=next_action,
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_or_none(text: str | None) -> Any | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _short_text(value: str | None, limit: int = 4000) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _redacted_command(cmd: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for part in cmd:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        text = str(part)
        redacted.append(text)
        if text in {"--target", "--message", "--token", "--secret", "--password"}:
            redact_next = True
    return redacted
