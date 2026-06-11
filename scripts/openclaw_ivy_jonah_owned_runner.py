#!/usr/bin/env python3
"""AWK-owned production runner for the OpenClaw Ivy/Jonah editorial lane.

This is the clean-cutover entrypoint for OpenClaw's Ivy/Jonah lane. AWK owns
the workflow instance, receipts, and terminal state. Review-decision work is
handled natively here instead of shelling through the legacy Work Ledger CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
KERNEL_PATH = ROOT / "packages" / "kernel"
if str(KERNEL_PATH) not in sys.path:
    sys.path.insert(0, str(KERNEL_PATH))

from agent_workflow_kernel import (  # noqa: E402
    AdapterRegistration,
    AdapterRegistry,
    KernelRuntimeConfig,
    LocalFakeRuntimeAdapter,
    PromptRef,
    PromptRegistry,
    StageDef,
    StageType,
    Transition,
    WorkflowDef,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowRunner,
)


SCHEMA = "openclaw.awk_ivy_jonah_owned_runner.v1"
WORKFLOW_ID = "openclaw_ivy_jonah_owned_cutover"
WORKFLOW_VERSION = "1.0.0"
OWNER_ID = "awk-ivy-jonah-owned-runner"
IVY_RUNTIME_REL = "workspace/agents/ivy_writing_ops"
IVY_REVIEW_HANDOFF_REL = f"{IVY_RUNTIME_REL}/handoffs/review_decisions"
IVY_PROJECTS_REL = f"{IVY_RUNTIME_REL}/projects"
IVY_LEDGER_REL = f"{IVY_RUNTIME_REL}/scripts/or_project_ledger.py"
IVY_ATTENTION_REL = f"{IVY_RUNTIME_REL}/scripts/ivy_writing_ops_v2.py"
IVY_ATTENTION_PUBLISHER_REL = "workspace-main/scripts/surfaces/publish_or_research_attention.py"
IVY_PUBLISH_CONTROL_REL = "scripts/lanes/ivy_writing_ops_publish_control.py"
PROMPTS_ROOT = ROOT / "prompts"
DETERMINISTIC_OWNED_RUNNER_NO_PROMPT_REASON = (
    "Deterministic AWK-owned runner stage; it executes the OpenClaw Ivy/Jonah "
    "lane directly and does not render model prompts through the kernel stage."
)
CLAIMABLE_HANDOFF_STATUSES = {"pending", "approved", "revision_requested", "in_progress", "delegated"}


class OpenClawIvyJonahOwnedAdapter(LocalFakeRuntimeAdapter):
    """Run the owned OpenClaw Ivy/Jonah domain lane under AWK control."""

    adapter_id = "runtime.openclaw_ivy_jonah_owned"

    def __init__(
        self,
        *,
        openclaw_root: Path,
        stale_minutes: int,
        dry_run: bool,
        created_at: str,
        timeout_seconds: int = 2700,
    ) -> None:
        super().__init__(created_at=created_at)
        self.openclaw_root = openclaw_root
        self.stale_minutes = stale_minutes
        self.dry_run = dry_run
        self.timeout_seconds = timeout_seconds
        self.prompt_registry = PromptRegistry.load(PROMPTS_ROOT)

    def invoke(self, invocation, runtime_input):
        result = super().invoke(invocation, runtime_input)
        stage_id = str(runtime_input["stage"]["id"])
        command_result = self._run_stage(stage_id)
        outcome = command_result["outcome"]
        status = "succeeded" if command_result["ok"] else "blocked"
        return replace(
            result,
            status=status,
            outputs={
                **result.outputs,
                "outcome": outcome,
                "legacy_compatibility_adapter": False,
                "native_owned_runner": True,
                "openclaw_root": str(self.openclaw_root),
                "dry_run": self.dry_run,
                "command": command_result,
            },
            residual_risk=(
                None
                if command_result["ok"]
                else "OpenClaw Ivy/Jonah owned runner blocked under AWK ownership."
            ),
            next_hint=outcome,
        )

    def _run_stage(self, stage_id: str) -> dict[str, Any]:
        if stage_id == "audit_editorial_path":
            self._ensure_runtime_dirs()
            pending = len(self._pending_handoffs())
            return {
                "ok": True,
                "outcome": "ok",
                "stdout_json": {
                    "ok": True,
                    "action": "audited_editorial_path",
                    "pending_handoffs": pending,
                },
            }
        if stage_id == "run_review_handoff":
            if self.dry_run:
                return {
                    "ok": True,
                    "outcome": "noop",
                    "skipped": True,
                    "reason": "dry_run",
                    "stdout_json": {"ok": True, "action": "dry_run"},
                }
            return self._run_review_handoff()
        if stage_id == "advance_lifecycle":
            if self.dry_run:
                return {
                    "ok": True,
                    "outcome": "noop",
                    "skipped": True,
                    "reason": "dry_run",
                    "stdout_json": {"ok": True, "action": "dry_run"},
                }
            return self._advance_lifecycle()
        if stage_id == "refresh_blackboard":
            if self.dry_run:
                return {
                    "ok": True,
                    "outcome": "refreshed",
                    "skipped": True,
                    "reason": "dry_run",
                    "stdout_json": {"ok": True, "action": "dry_run"},
                }
            return self._run_command(
                [
                    sys.executable,
                    "workspace-main/scripts/surfaces/update_review_inbox.py",
                    "--validate",
                ],
                success_outcome="refreshed",
                blocked_outcome="blocked",
            )
        return {
            "ok": False,
            "outcome": "blocked",
            "error": f"unknown AWK Ivy/Jonah owned-runner stage: {stage_id}",
        }

    def _ensure_runtime_dirs(self) -> None:
        for rel in (
            IVY_REVIEW_HANDOFF_REL,
            f"{IVY_RUNTIME_REL}/handoffs/attention",
            f"{IVY_RUNTIME_REL}/reports/review",
            f"{IVY_RUNTIME_REL}/read_models",
            IVY_PROJECTS_REL,
        ):
            (self.openclaw_root / rel).mkdir(parents=True, exist_ok=True)

    def _pending_handoffs(self) -> list[tuple[Path, dict[str, Any]]]:
        handoffs: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted((self.openclaw_root / IVY_REVIEW_HANDOFF_REL).glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if str(data.get("agent_id") or "") != "ivy_writing_ops":
                continue
            status = str(data.get("status") or "").strip().lower()
            if status not in CLAIMABLE_HANDOFF_STATUSES:
                continue
            handoffs.append((path, data))
        return handoffs

    def _run_review_handoff(self) -> dict[str, Any]:
        self._ensure_runtime_dirs()
        pending = self._pending_handoffs()
        if not pending:
            return {
                "ok": True,
                "outcome": "noop",
                "stdout_json": {"ok": True, "action": "noop", "reason": "no_pending_review_handoff"},
            }
        handoff_path, handoff = pending[0]
        return self._process_handoff(handoff_path=handoff_path, handoff=handoff)

    def _process_handoff(self, *, handoff_path: Path, handoff: Mapping[str, Any]) -> dict[str, Any]:
        action = str(handoff.get("action") or "").strip().lower()
        handoff_type = str(handoff.get("handoff_type") or "").strip()
        project_id = str(handoff.get("project_id") or "").strip()
        if not project_id:
            return self._blocked_handoff(handoff_path, handoff, "missing project_id")
        if action == "advance_to_p4" or handoff_type == "ivy_writing_ops_p3_approved_to_p4":
            return self._handle_p3_approval(handoff_path=handoff_path, handoff=handoff, project_id=project_id)
        if action == "revise_p3" or handoff_type == "ivy_writing_ops_p3_revision_request":
            return self._handle_revision_handoff(
                handoff_path=handoff_path,
                handoff=handoff,
                project_id=project_id,
                gate="P3",
                file_key="p3",
            )
        if action == "revise_p5" or handoff_type == "ivy_writing_ops_p5_revision_request":
            return self._handle_revision_handoff(
                handoff_path=handoff_path,
                handoff=handoff,
                project_id=project_id,
                gate="P5",
                file_key="p4",
            )
        if action == "prepare_publish_bundle" or handoff_type == "ivy_writing_ops_m5_publish_decision":
            return self._handle_publish_bundle(handoff_path=handoff_path, handoff=handoff, project_id=project_id)
        if action in {"kill", "park"} or handoff_type == "ivy_writing_ops_terminal_review_decision":
            return self._handle_terminal_handoff(handoff_path=handoff_path, handoff=handoff, project_id=project_id, action=action)
        return self._blocked_handoff(handoff_path, handoff, f"unsupported handoff {handoff_type}/{action}")

    def _handle_p3_approval(self, *, handoff_path: Path, handoff: Mapping[str, Any], project_id: str) -> dict[str, Any]:
        self._apply_review_decision(project_id=project_id, gate="P3", handoff=handoff)
        project = self._load_project(project_id) or {}
        if _normalize_gate(str(project.get("gate") or "")) == "P3":
            advance = self._run_command(
                [
                    sys.executable,
                    IVY_LEDGER_REL,
                    "--root",
                    IVY_RUNTIME_REL,
                    "advance",
                    "--project",
                    project_id,
                    "--to",
                    "P4",
                    "--why",
                    "AWK accepted Suman's P3 approval and advanced to P4 draft package.",
                    "--actor",
                    OWNER_ID,
                ],
                success_outcome="advanced",
                blocked_outcome="blocked",
            )
            if not advance["ok"]:
                return self._blocked_handoff(handoff_path, handoff, "failed to advance project to P4", command=advance)
        review_result = self._run_native_editorial_loop(project_id=project_id)
        if not review_result["ok"]:
            return self._blocked_handoff(handoff_path, handoff, str(review_result.get("reason") or "native editorial loop failed"), extra=review_result)
        published = self._publish_human_gate(self._load_project(project_id) or {"id": project_id, "gate": "P5"}, reason="native_p4_editorial_review_complete")
        if not published["ok"]:
            return self._blocked_handoff(handoff_path, handoff, "failed to publish P5 human gate", extra=published)
        self._mark_handoff(
            handoff_path,
            handoff,
            status="processed",
            summary="AWK handled the P3 approval, completed the Ivy/Jonah native editorial loop, and published the P5 review gate.",
            extra={
                "project_id": project_id,
                "operator_summary_path": review_result.get("operator_summary_path", ""),
            },
        )
        data = dict(published.get("stdout_json") or {})
        data.setdefault("advanced_from_gate", "P3")
        data.setdefault("advanced_to_gate", "P5")
        data.setdefault("handoff_path", str(handoff_path))
        data.setdefault("operator_summary_path", review_result.get("operator_summary_path", ""))
        return {
            "ok": True,
            "outcome": "handled",
            "stdout_json": data,
        }

    def _handle_revision_handoff(
        self,
        *,
        handoff_path: Path,
        handoff: Mapping[str, Any],
        project_id: str,
        gate: str,
        file_key: str,
    ) -> dict[str, Any]:
        self._apply_review_decision(project_id=project_id, gate=gate, handoff=handoff)
        revision = self._run_ivy_revision(project_id=project_id, handoff=handoff, gate=gate, file_key=file_key)
        if not revision["ok"]:
            return self._blocked_handoff(handoff_path, handoff, str(revision.get("reason") or "revision failed"), extra=revision)
        if gate == "P5":
            render = self._run_command(
                [
                    sys.executable,
                    IVY_LEDGER_REL,
                    "--root",
                    IVY_RUNTIME_REL,
                    "render",
                    "--project",
                    project_id,
                    "--gate",
                    "P5",
                ],
                success_outcome="rendered",
                blocked_outcome="blocked",
            )
            if not render["ok"]:
                return self._blocked_handoff(handoff_path, handoff, "failed to rerender P5 artifact", command=render)
        published = self._publish_human_gate(self._load_project(project_id) or {"id": project_id, "gate": gate}, reason=f"native_{gate.lower()}_revision_complete")
        if not published["ok"]:
            return self._blocked_handoff(handoff_path, handoff, f"failed to publish {gate} human gate", extra=published)
        self._mark_handoff(
            handoff_path,
            handoff,
            status="processed",
            summary=f"AWK revised the {gate} artifact through Ivy and republished the {gate} review gate.",
            extra={"project_id": project_id},
        )
        data = dict(published.get("stdout_json") or {})
        data.setdefault("handoff_path", str(handoff_path))
        return {"ok": True, "outcome": "handled", "stdout_json": data}

    def _handle_publish_bundle(self, *, handoff_path: Path, handoff: Mapping[str, Any], project_id: str) -> dict[str, Any]:
        self._apply_review_decision(project_id=project_id, gate="P5", handoff=handoff)
        prepare = self._run_command(
            [
                sys.executable,
                IVY_PUBLISH_CONTROL_REL,
                "prepare-bundle",
                "--openclaw-root",
                str(self.openclaw_root),
                "--or-root",
                IVY_RUNTIME_REL,
                "--source",
                "obsidian_blackboard",
                "--project",
                project_id,
                "--channel",
                "substack",
            ],
            success_outcome="prepared",
            blocked_outcome="blocked",
        )
        if not prepare["ok"]:
            return self._blocked_handoff(handoff_path, handoff, "failed to prepare publish bundle", command=prepare)
        plan = self._run_command(
            [
                sys.executable,
                IVY_PUBLISH_CONTROL_REL,
                "browser-plan",
                "--openclaw-root",
                str(self.openclaw_root),
                "--or-root",
                IVY_RUNTIME_REL,
                "--source",
                "obsidian_blackboard",
                "--project",
                project_id,
                "--platform",
                "substack",
            ],
            success_outcome="prepared",
            blocked_outcome="blocked",
        )
        if not plan["ok"]:
            return self._blocked_handoff(handoff_path, handoff, "failed to prepare browser plan", command=plan)
        plan_json = plan.get("stdout_json") or {}
        publish_ready = self.openclaw_root / IVY_RUNTIME_REL / "projects" / project_id / "publish_bundle" / "publish-ready.md"
        publish_staging = self.openclaw_root / IVY_RUNTIME_REL / "projects" / project_id / "publish-staging.json"
        self._mark_handoff(
            handoff_path,
            handoff,
            status="processed",
            summary="AWK prepared the local publish bundle and browser staging plan. No external publish was performed.",
            extra={
                "project_id": project_id,
                "publish_ready_path": str(publish_ready),
                "publish_staging_path": str(publish_staging),
                "browser_plan_path": str(plan_json.get("plan") or ""),
            },
        )
        return {
            "ok": True,
            "outcome": "handled",
            "stdout_json": {
                "ok": True,
                "action": "prepared_ivy_writing_ops_publish_packet",
                "project_id": project_id,
                "handoff_path": str(handoff_path),
                "publish_ready_path": str(publish_ready),
                "publish_staging_path": str(publish_staging),
                "browser_plan_path": str(plan_json.get("plan") or ""),
                "external_publish_performed": False,
            },
        }

    def _handle_terminal_handoff(self, *, handoff_path: Path, handoff: Mapping[str, Any], project_id: str, action: str) -> dict[str, Any]:
        gate = _normalize_gate(str((self._load_project(project_id) or {}).get("gate") or "P5"))
        decision = "deferred" if action == "park" else "rejected"
        self._apply_review_decision(project_id=project_id, gate=gate, handoff={**handoff, "decision": decision})
        status = "parked" if action == "park" else "killed"
        change = self._run_command(
            [
                sys.executable,
                IVY_LEDGER_REL,
                "--root",
                IVY_RUNTIME_REL,
                "status",
                "--project",
                project_id,
                "--status",
                status,
                "--why",
                f"AWK applied Suman's terminal {action} review decision.",
                "--actor",
                OWNER_ID,
            ],
            success_outcome="terminalized",
            blocked_outcome="blocked",
        )
        if not change["ok"]:
            return self._blocked_handoff(handoff_path, handoff, f"failed to apply terminal {action} decision", command=change)
        self._mark_handoff(
            handoff_path,
            handoff,
            status="processed",
            summary=f"AWK applied the terminal Ivy review decision '{action}' and stopped the project.",
            extra={"project_id": project_id},
        )
        return {
            "ok": True,
            "outcome": "handled",
            "stdout_json": {
                "ok": True,
                "action": f"applied_ivy_writing_ops_terminal_{action}",
                "project_id": project_id,
                "handoff_path": str(handoff_path),
            },
        }

    def _run_native_editorial_loop(self, *, project_id: str) -> dict[str, Any]:
        paths = self._project_paths(project_id)
        ivy_session_key = f"agent:ivy_writing_ops:awk-ivy-jonah:{project_id}:ivy"
        jonah_session_key = f"agent:jonah_editor:awk-ivy-jonah:{project_id}:jonah"
        build = self._run_openclaw_agent(
            agent_id="ivy_writing_ops",
            session_key=ivy_session_key,
            timeout=900,
            message=self._build_draft_prompt(project_id=project_id, paths=paths),
        )
        if build.get("error"):
            return {"ok": False, "reason": build["error"]}
        build_payload = _parse_agent_json(str(build.get("text") or ""))
        if str(build_payload.get("outcome") or "") != "ready":
            return {"ok": False, "reason": str(build_payload.get("blocking_reason") or build_payload.get("summary") or "Ivy blocked draft build")}
        draft_markdown = str(build_payload.get("draft_package_markdown") or "").strip()
        if not draft_markdown:
            return {"ok": False, "reason": "Ivy did not return draft_package_markdown"}
        paths["p4"].write_text(draft_markdown.rstrip() + "\n", encoding="utf-8")
        source_trail = str(build_payload.get("source_trail_markdown") or "").strip()
        if source_trail:
            paths["source_trail"].write_text(source_trail.rstrip() + "\n", encoding="utf-8")
        review = self._run_editor_review(project_id=project_id, paths=paths, ivy_session_key=ivy_session_key, jonah_session_key=jonah_session_key, review_round=1)
        if not review["ok"]:
            return review
        if review["outcome"] == "accepted":
            return self._accept_editorial_review(project_id=project_id, review=review, paths=paths)
        if review["outcome"] != "needs_revision":
            return {"ok": False, "reason": review.get("reason") or "Jonah blocked the editorial review"}
        revise = self._run_openclaw_agent(
            agent_id="ivy_writing_ops",
            session_key=ivy_session_key,
            timeout=600,
            message=self._revise_draft_prompt(project_id=project_id, paths=paths, review=review),
        )
        if revise.get("error"):
            return {"ok": False, "reason": revise["error"]}
        revise_payload = _parse_agent_json(str(revise.get("text") or ""))
        if str(revise_payload.get("outcome") or "") != "revised":
            return {"ok": False, "reason": str(revise_payload.get("blocking_reason") or revise_payload.get("summary") or "Ivy blocked draft revision")}
        revised_markdown = str(revise_payload.get("revised_draft_package_markdown") or "").strip()
        if not revised_markdown:
            return {"ok": False, "reason": "Ivy did not return revised_draft_package_markdown"}
        paths["p4"].write_text(revised_markdown.rstrip() + "\n", encoding="utf-8")
        second = self._run_editor_review(project_id=project_id, paths=paths, ivy_session_key=ivy_session_key, jonah_session_key=jonah_session_key, review_round=2)
        if not second["ok"]:
            return second
        if second["outcome"] != "accepted":
            return {"ok": False, "reason": second.get("reason") or "Jonah did not accept the revised draft within budget"}
        return self._accept_editorial_review(project_id=project_id, review=second, paths=paths)

    def _run_editor_review(
        self,
        *,
        project_id: str,
        paths: Mapping[str, Path],
        ivy_session_key: str,
        jonah_session_key: str,
        review_round: int,
    ) -> dict[str, Any]:
        review = self._run_openclaw_agent(
            agent_id="jonah_editor",
            session_key=jonah_session_key,
            timeout=900,
            message=self._editor_review_prompt(
                project_id=project_id,
                paths=paths,
                ivy_session_key=ivy_session_key,
                review_round=review_round,
            ),
        )
        if review.get("error"):
            return {"ok": False, "reason": review["error"]}
        payload = _parse_agent_json(str(review.get("text") or ""))
        outcome = str(payload.get("outcome") or "").strip()
        status = str(payload.get("editor_review_status") or "").strip() or ("revise" if outcome == "needs_revision" else "publishable_with_minor_edits")
        issues = [str(item).strip() for item in payload.get("issues") or [] if str(item).strip()]
        if outcome == "accepted":
            self._write_editor_review(project_id=project_id, status=status if status in {"publishable", "publishable_with_minor_edits"} else "publishable_with_minor_edits", summary=str(payload.get("summary") or "Jonah accepted the draft package."), issues=issues or ["No material issues."], recommendation=str(payload.get("recommendation") or "Advance to P5 final review."))
            return {"ok": True, "outcome": "accepted", "summary": payload.get("summary"), "recommendation": payload.get("recommendation"), "issues": issues}
        if outcome == "needs_revision":
            self._write_editor_review(project_id=project_id, status="revise", summary=str(payload.get("summary") or "Jonah requested a revision before P5."), issues=issues or [str(payload.get("requested_revision") or "Revision requested.")], recommendation=str(payload.get("recommendation") or payload.get("requested_revision") or "Revise and return to Jonah."))
            return {"ok": True, "outcome": "needs_revision", "summary": payload.get("summary"), "recommendation": payload.get("recommendation"), "requested_revision": payload.get("requested_revision"), "issues": issues}
        self._write_editor_review(project_id=project_id, status="ask_suman", summary=str(payload.get("summary") or "Jonah blocked the draft package."), issues=issues or ["Jonah blocked the draft package."], recommendation=str(payload.get("recommendation") or "Inspect the draft package before retrying."))
        return {"ok": False, "outcome": "block", "reason": str(payload.get("summary") or payload.get("recommendation") or "Jonah blocked the draft package")}

    def _accept_editorial_review(self, *, project_id: str, review: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
        advance = self._run_command(
            [
                sys.executable,
                IVY_LEDGER_REL,
                "--root",
                IVY_RUNTIME_REL,
                "advance",
                "--project",
                project_id,
                "--to",
                "P5",
                "--why",
                "AWK native Ivy/Jonah editorial review accepted the P4 package.",
                "--actor",
                OWNER_ID,
            ],
            success_outcome="advanced",
            blocked_outcome="blocked",
        )
        if not advance["ok"]:
            return {"ok": False, "reason": "failed to advance accepted draft to P5"}
        self._refresh_read_models()
        return {
            "ok": True,
            "outcome": "accepted",
            "operator_summary_path": str(paths["project"].parent / "p4_editor_review.md"),
            "summary": review.get("summary", ""),
        }

    def _run_ivy_revision(
        self,
        *,
        project_id: str,
        handoff: Mapping[str, Any],
        gate: str,
        file_key: str,
    ) -> dict[str, Any]:
        paths = self._project_paths(project_id)
        target = paths[file_key]
        prompt = self._generic_revision_prompt(project_id=project_id, handoff=handoff, gate=gate, target_path=target)
        response = self._run_openclaw_agent(
            agent_id="ivy_writing_ops",
            session_key=f"agent:ivy_writing_ops:awk-ivy-jonah:{project_id}:{gate.lower()}-revision",
            timeout=900,
            message=prompt,
        )
        if response.get("error"):
            return {"ok": False, "reason": response["error"]}
        payload = _parse_agent_json(str(response.get("text") or ""))
        replacement = str(payload.get("replacement_markdown") or "").strip()
        if str(payload.get("outcome") or "") != "revised" or not replacement:
            return {"ok": False, "reason": str(payload.get("blocking_reason") or payload.get("summary") or "Ivy did not produce a revised artifact")}
        target.write_text(replacement.rstrip() + "\n", encoding="utf-8")
        return {"ok": True}

    def _apply_review_decision(self, *, project_id: str, gate: str, handoff: Mapping[str, Any]) -> None:
        comments = json.dumps(handoff.get("comments") or [])
        inline_comments = json.dumps(handoff.get("inline_comments") or [])
        cmd = [
            sys.executable,
            IVY_LEDGER_REL,
            "--root",
            IVY_RUNTIME_REL,
            "review-decision",
            "--project",
            project_id,
            "--gate",
            gate,
            "--decision",
            str(handoff.get("decision") or "approved"),
            "--action",
            str(handoff.get("action") or ""),
            "--actor",
            "Suman",
            "--note",
            str(handoff.get("decision_label") or ""),
            "--comments-json",
            comments,
            "--inline-comments-json",
            inline_comments,
            "--receipt-path",
            str(handoff.get("receipt_path") or ""),
        ]
        result = self._run_command(cmd, success_outcome="applied", blocked_outcome="blocked")
        if not result["ok"]:
            raise RuntimeError(result.get("stderr") or result.get("stdout") or "failed to apply review decision")

    def _write_editor_review(self, *, project_id: str, status: str, summary: str, issues: Sequence[str], recommendation: str) -> None:
        cmd = [
            sys.executable,
            IVY_LEDGER_REL,
            "--root",
            IVY_RUNTIME_REL,
            "editor-review",
            "--project",
            project_id,
            "--status",
            status,
            "--reviewer",
            "Jonah",
            "--summary",
            summary,
            "--recommendation",
            recommendation,
        ]
        for issue in issues:
            cmd.extend(["--issue", str(issue)])
        result = self._run_command(cmd, success_outcome="written", blocked_outcome="blocked")
        if not result["ok"]:
            raise RuntimeError(result.get("stderr") or result.get("stdout") or "failed to write editor review")

    def _project_paths(self, project_id: str) -> dict[str, Path]:
        root = self.openclaw_root / IVY_RUNTIME_REL / "projects" / project_id
        return {
            "project": root / "project.json",
            "p3": root / "p3_research_brief.md",
            "p4": root / "p4_draft_package.md",
            "p5": root / "p5_final_review.md",
            "source_trail": root / "source_trail.md",
        }

    def _run_openclaw_agent(self, *, agent_id: str, session_key: str, message: str, timeout: int) -> dict[str, Any]:
        command = os.environ.get("OPENCLAW_COMMAND", "openclaw")
        cmd = [
            command,
            "agent",
            "--agent",
            agent_id,
            "--session-key",
            session_key,
            "--message",
            message,
            "--json",
            "--timeout",
            str(timeout),
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.openclaw_root),
                text=True,
                capture_output=True,
                timeout=timeout + 30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"error": str(exc), "stdout": "", "stderr": ""}
        if proc.returncode != 0:
            return {"error": proc.stderr.strip() or proc.stdout.strip() or f"{command} agent failed", "stdout": proc.stdout, "stderr": proc.stderr}
        try:
            data = json.loads(proc.stdout)
        except Exception as exc:
            return {"error": f"invalid openclaw agent JSON: {exc}", "stdout": proc.stdout, "stderr": proc.stderr}
        payloads = ((data.get("result") or {}).get("payloads") or [])
        text = str(payloads[0].get("text") or "") if payloads else ""
        meta = (data.get("result") or {}).get("meta") or {}
        agent_meta = meta.get("agentMeta") or {}
        return {
            "raw": data,
            "text": text,
            "run_id": data.get("runId"),
            "status": data.get("status"),
            "session_id": agent_meta.get("sessionId"),
            "session_key": session_key,
            "agent_id": agent_id,
        }

    def _bundle_text(self, refs: Sequence[tuple[str, str, str]]) -> str:
        bundle = self.prompt_registry.resolve(
            tuple(
                PromptRef(id=prompt_id, kind=kind, version=version, registry="local", render_mode="markdown", required=True)
                for prompt_id, kind, version in refs
            )
        )
        return "\n\n".join(prompt.content for prompt in bundle.prompts)

    def _build_draft_prompt(self, *, project_id: str, paths: Mapping[str, Path]) -> str:
        bundle = self._bundle_text(
            (
                ("identity.ivy_or_research", "identity", "1.0.0"),
                ("policy.openclaw.editorial_public_boundary", "policy", "1.0.0"),
                ("lane.ivy_jonah_editorial", "lane", "1.0.0"),
                ("stage.ivy_jonah.build_draft_package", "stage", "1.0.0"),
            )
        )
        p3_excerpt = _sample(paths["p3"], 6000)
        return (
            f"{bundle}\n\n"
            "Return JSON only.\n"
            "Schema:\n"
            "{\"schema\":\"draft_package_result.v1\",\"outcome\":\"ready|blocked\",\"summary\":\"...\","
            "\"draft_package_markdown\":\"...\",\"source_trail_markdown\":\"...\",\"blocking_reason\":\"...\"}\n\n"
            f"Project: {project_id}\n"
            f"P3 artifact path: {paths['p3']}\n"
            f"P4 artifact path: {paths['p4']}\n"
            "Use the existing project files in the local checkout. Write a complete P4 markdown package fit for the Ivy writing lane and preserve public-source integrity.\n\n"
            f"P3 excerpt:\n{p3_excerpt}\n"
        )

    def _editor_review_prompt(
        self,
        *,
        project_id: str,
        paths: Mapping[str, Path],
        ivy_session_key: str,
        review_round: int,
    ) -> str:
        bundle = self._bundle_text(
            (
                ("identity.ivy_or_research", "identity", "1.0.0"),
                ("identity.jonah_editor", "identity", "1.0.0"),
                ("policy.openclaw.editorial_public_boundary", "policy", "1.0.0"),
                ("lane.ivy_jonah_editorial", "lane", "1.0.0"),
                ("stage.ivy_jonah.editor_review", "stage", "1.0.0"),
            )
        )
        return (
            f"{bundle}\n\n"
            "Return JSON only.\n"
            "Schema:\n"
            "{\"schema\":\"editorial_verdict.v1\",\"outcome\":\"accepted|needs_revision|block\","
            "\"editor_review_status\":\"publishable|publishable_with_minor_edits|revise|ask_suman|kill\","
            "\"summary\":\"...\",\"issues\":[\"...\"],\"recommendation\":\"...\",\"requested_revision\":\"...\"}\n\n"
            f"Project: {project_id}\n"
            f"Review round: {review_round}\n"
            f"Ivy session key: {ivy_session_key}\n"
            f"P3 excerpt:\n{_sample(paths['p3'], 3000)}\n\n"
            f"P4 excerpt:\n{_sample(paths['p4'], 7000)}\n"
        )

    def _revise_draft_prompt(self, *, project_id: str, paths: Mapping[str, Path], review: Mapping[str, Any]) -> str:
        bundle = self._bundle_text(
            (
                ("identity.ivy_or_research", "identity", "1.0.0"),
                ("policy.openclaw.editorial_public_boundary", "policy", "1.0.0"),
                ("lane.ivy_jonah_editorial", "lane", "1.0.0"),
                ("stage.ivy_jonah.revise_draft", "stage", "1.0.0"),
            )
        )
        requested = str(review.get("requested_revision") or review.get("recommendation") or "Revise the draft per Jonah's review.")
        return (
            f"{bundle}\n\n"
            "Return JSON only.\n"
            "Schema:\n"
            "{\"schema\":\"revision_result.v1\",\"outcome\":\"revised|blocked\",\"summary\":\"...\","
            "\"revised_draft_package_markdown\":\"...\",\"blocking_reason\":\"...\"}\n\n"
            f"Project: {project_id}\n"
            f"Requested revision: {requested}\n"
            f"Current P4 excerpt:\n{_sample(paths['p4'], 7000)}\n"
        )

    def _generic_revision_prompt(self, *, project_id: str, handoff: Mapping[str, Any], gate: str, target_path: Path) -> str:
        comments = "\n".join(f"- {item}" for item in handoff.get("comments") or [])
        inline = "\n".join(
            f"- {item.get('target') or 'general'}: {item.get('comment') or ''}"
            for item in handoff.get("inline_comments") or []
            if isinstance(item, Mapping)
        )
        return (
            "You are Ivy Writing Ops.\n\n"
            "Revise the requested gate artifact in the local checkout and return JSON only.\n"
            "Schema:\n"
            "{\"schema\":\"gate_revision_result.v1\",\"outcome\":\"revised|blocked\",\"summary\":\"...\","
            "\"replacement_markdown\":\"...\",\"blocking_reason\":\"...\"}\n\n"
            f"Project: {project_id}\n"
            f"Gate: {gate}\n"
            f"Target path: {target_path}\n"
            f"Decision label: {handoff.get('decision_label') or ''}\n"
            f"Comments:\n{comments or '- none'}\n"
            f"Inline comments:\n{inline or '- none'}\n\n"
            f"Current artifact excerpt:\n{_sample(target_path, 7000)}\n"
        )

    def _mark_handoff(
        self,
        handoff_path: Path,
        handoff: Mapping[str, Any],
        *,
        status: str,
        summary: str,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        updated = dict(handoff)
        updated["status"] = status
        updated["runner_owner"] = OWNER_ID
        updated["runner_summary"] = summary
        updated["runner_finished_at"] = self.created_at
        for key, value in (extra or {}).items():
            updated[key] = value
        handoff_path.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _blocked_handoff(
        self,
        handoff_path: Path,
        handoff: Mapping[str, Any],
        reason: str,
        *,
        command: Mapping[str, Any] | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._mark_handoff(
            handoff_path,
            handoff,
            status="blocked",
            summary=reason,
            extra={"runner_error": reason, **dict(extra or {})},
        )
        return {
            "ok": False,
            "outcome": "blocked",
            "stdout_json": {
                "ok": False,
                "action": "blocked_native_ivy_jonah_owned_runner",
                "handoff_path": str(handoff_path),
                "project_id": str(handoff.get("project_id") or ""),
                "reason": reason,
            },
            "command": command or {},
        }

    def _advance_lifecycle(self) -> dict[str, Any]:
        self._ensure_runtime_dirs()
        project = self._select_project()
        if not project:
            return {
                "ok": True,
                "outcome": "noop",
                "stdout_json": {"ok": True, "action": "noop", "reason": "no_active_ivy_projects"},
            }
        project_id = str(project.get("id") or "")
        gate = _normalize_gate(str(project.get("gate") or "P1"))
        status = str(project.get("status") or "")
        if status == "needs_suman" or bool(project.get("needs_suman")):
            return self._publish_human_gate(project, reason="project_already_waiting_on_human")
        next_gate = _next_gate(gate)
        if not next_gate:
            return self._publish_human_gate(project, reason="project_at_terminal_review_gate")
        agent_gate = _agent_gate_required(project, gate, next_gate)
        if agent_gate:
            return {
                "ok": True,
                "outcome": "agent_gate_required",
                "stdout_json": {
                    "ok": True,
                    "action": "agent_gate_required",
                    "project_id": project_id,
                    "gate": gate,
                    "next_gate": next_gate,
                    **agent_gate,
                },
            }
        advance = self._run_command(
            [
                sys.executable,
                IVY_LEDGER_REL,
                "--root",
                IVY_RUNTIME_REL,
                "advance",
                "--project",
                project_id,
                "--to",
                next_gate,
                "--why",
                f"AWK lifecycle owner advanced machine-owned {gate}->{next_gate}",
                "--actor",
                OWNER_ID,
            ],
            success_outcome="advanced",
            blocked_outcome="blocked",
        )
        if not advance["ok"]:
            return {
                **advance,
                "stdout_json": {
                    "ok": False,
                    "action": "blocked_ivy_lifecycle_advance",
                    "project_id": project_id,
                    "gate": gate,
                    "next_gate": next_gate,
                    "stderr": advance.get("stderr", ""),
                    "stdout": advance.get("stdout", ""),
                },
            }
        self._refresh_read_models()
        updated = self._load_project(project_id) or project
        if updated.get("needs_suman") or str(updated.get("status") or "") == "needs_suman":
            published = self._publish_human_gate(updated, reason=f"advanced_to_human_gate_{next_gate}")
            data = dict(published.get("stdout_json") or {})
            data.setdefault("advanced_from_gate", gate)
            data.setdefault("advanced_to_gate", next_gate)
            published["stdout_json"] = data
            return published
        artifact = _gate_artifact_path(project_id, next_gate)
        return {
            "ok": True,
            "outcome": "advanced",
            "stdout_json": {
                "ok": True,
                "action": "advanced_ivy_lifecycle_project",
                "project_id": project_id,
                "from_gate": gate,
                "to_gate": next_gate,
                "artifact_path": artifact,
                "next_action": updated.get("next_action"),
                "owner": "machine",
            },
        }

    def _select_project(self) -> dict[str, Any] | None:
        projects: list[dict[str, Any]] = []
        for path in sorted((self.openclaw_root / IVY_PROJECTS_REL).glob("*/project.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            status = str(data.get("status") or "")
            if status in {"active", "needs_suman"} or data.get("needs_suman"):
                projects.append(data)
        if not projects:
            return None
        rank = {"P5": 5, "P4": 4, "P3": 3, "P2": 2, "P1": 1}
        projects.sort(
            key=lambda item: (
                1 if item.get("needs_suman") or str(item.get("status") or "") == "needs_suman" else 0,
                rank.get(_normalize_gate(str(item.get("gate") or "P1")), 0),
                str(item.get("last_touched") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        return projects[0]

    def _load_project(self, project_id: str) -> dict[str, Any] | None:
        path = self.openclaw_root / IVY_PROJECTS_REL / project_id / "project.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _refresh_read_models(self) -> None:
        for cmd in (
            [sys.executable, IVY_LEDGER_REL, "--root", IVY_RUNTIME_REL, "source-intake-plan", "--lookback-days", "30"],
            [sys.executable, IVY_LEDGER_REL, "--root", IVY_RUNTIME_REL, "weekly-post-candidate"],
            [sys.executable, IVY_LEDGER_REL, "--root", IVY_RUNTIME_REL, "lint"],
        ):
            subprocess.run(cmd, cwd=str(self.openclaw_root), text=True, capture_output=True, timeout=self.timeout_seconds, check=False)

    def _publish_human_gate(self, project: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
        project_id = str(project.get("id") or "")
        gate = _normalize_gate(str(project.get("gate") or "P1"))
        artifact = _gate_artifact_path(project_id, gate)
        title = f"Review Ivy Writing Ops {project.get('title') or project_id}"
        summary = str(project.get("ambiguity") or project.get("next_action") or "Ivy Writing Ops needs Suman review.")
        attention = self._run_command(
            [
                sys.executable,
                IVY_ATTENTION_REL,
                "--root",
                IVY_RUNTIME_REL,
                "attention-plan",
                "--title",
                title,
                "--artifact-path",
                artifact,
                "--purpose",
                "critique",
                "--urgency",
                "normal",
                "--approval-required",
                "--summary",
                summary,
            ],
            success_outcome="attention_created",
            blocked_outcome="blocked",
        )
        if not attention["ok"]:
            return {
                **attention,
                "stdout_json": {
                    "ok": False,
                    "action": "blocked_ivy_human_gate_attention",
                    "project_id": project_id,
                    "gate": gate,
                    "artifact_path": artifact,
                    "stderr": attention.get("stderr", ""),
                },
            }
        attention_json = attention.get("stdout_json")
        if not isinstance(attention_json, Mapping):
            attention_json = {}
        attention_path = str(attention_json.get("output_path") or "")
        publish_cmd = [
            sys.executable,
            IVY_ATTENTION_PUBLISHER_REL,
            "--or-root",
            IVY_RUNTIME_REL,
            "--validate",
        ]
        if attention_path:
            publish_cmd.extend(["--attention", attention_path])
        publish = self._run_command(
            publish_cmd,
            success_outcome="human_gate_published",
            blocked_outcome="blocked",
        )
        publish_json = publish.get("stdout_json")
        if not isinstance(publish_json, Mapping):
            publish_json = {}
        ok = bool(publish["ok"]) and bool(publish_json.get("ok", True))
        return {
            **publish,
            "ok": ok,
            "outcome": "human_gate_published" if ok else "blocked",
            "stdout_json": {
                "ok": ok,
                "action": "published_ivy_human_gate",
                "project_id": project_id,
                "gate": gate,
                "artifact_path": artifact,
                "attention_path": attention_path,
                "review_note": publish_json.get("review_note"),
                "review_note_rel": publish_json.get("review_note_rel"),
                "artifact_record": publish_json.get("artifact_record"),
                "already_published": publish_json.get("already_published"),
                "reason": reason,
                "owner": "human",
            },
        }

    def _run_command(
        self,
        cmd: Sequence[str],
        *,
        success_outcome: str,
        blocked_outcome: str,
    ) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                list(cmd),
                cwd=str(self.openclaw_root),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "ok": False,
                "outcome": blocked_outcome,
                "argv": list(cmd),
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "error": str(exc),
            }
        parsed = _parse_json(completed.stdout)
        return {
            "ok": completed.returncode == 0,
            "outcome": success_outcome if completed.returncode == 0 else blocked_outcome,
            "argv": list(cmd),
            "returncode": completed.returncode,
            "stdout": _short(completed.stdout),
            "stderr": _short(completed.stderr),
            "stdout_json": parsed,
        }


def ivy_jonah_owned_workflow() -> WorkflowDef:
    return WorkflowDef(
        id=WORKFLOW_ID,
        version=WORKFLOW_VERSION,
        name="OpenClaw Ivy/Jonah AWK-Owned Cutover Runner",
        owner=OWNER_ID,
        description=(
            "AWK-owned production runner for the Ivy/Jonah editorial lane. "
            "AWK owns review-handoff execution, one-step Ivy lifecycle "
            "advancement, and human-gate publication."
        ),
        defaults={
            "policy_class": "internal_generation",
            "timeout_seconds": 2700,
            "retry": {"max_attempts": 1},
        },
        stages=(
            StageDef(
                id="audit_editorial_path",
                type=StageType.AGENT_WORK,
                adapter=OpenClawIvyJonahOwnedAdapter.adapter_id,
                outcomes=("ok", "blocked"),
                inputs={"operation": "invoke"},
                actors={"runner": OWNER_ID},
                no_prompt_reason=DETERMINISTIC_OWNED_RUNNER_NO_PROMPT_REASON,
                policy={"class": "read_only"},
            ),
            StageDef(
                id="run_review_handoff",
                type=StageType.AGENT_WORK,
                adapter=OpenClawIvyJonahOwnedAdapter.adapter_id,
                outcomes=("handled", "noop", "blocked"),
                inputs={"operation": "invoke"},
                actors={"runner": OWNER_ID},
                no_prompt_reason=DETERMINISTIC_OWNED_RUNNER_NO_PROMPT_REASON,
                policy={
                    "class": "internal_generation",
                    "forbidden_actions": (
                        "public_publish",
                        "external_send",
                        "auth_or_secret_access",
                        "trade_or_money_action",
                    ),
                },
            ),
            StageDef(
                id="advance_lifecycle",
                type=StageType.AGENT_WORK,
                adapter=OpenClawIvyJonahOwnedAdapter.adapter_id,
                outcomes=("advanced", "human_gate_published", "agent_gate_required", "noop", "blocked"),
                inputs={"operation": "invoke"},
                actors={"runner": OWNER_ID},
                no_prompt_reason=DETERMINISTIC_OWNED_RUNNER_NO_PROMPT_REASON,
                policy={
                    "class": "internal_generation",
                    "forbidden_actions": (
                        "public_publish",
                        "external_send",
                        "auth_or_secret_access",
                        "trade_or_money_action",
                    ),
                },
            ),
            StageDef(
                id="refresh_blackboard",
                type=StageType.AGENT_WORK,
                adapter=OpenClawIvyJonahOwnedAdapter.adapter_id,
                outcomes=("refreshed", "blocked"),
                inputs={"operation": "invoke"},
                actors={"runner": OWNER_ID},
                no_prompt_reason=DETERMINISTIC_OWNED_RUNNER_NO_PROMPT_REASON,
                policy={"class": "internal_state"},
            ),
        ),
        transitions=(
            Transition(from_stage="audit_editorial_path", on="ok", to_stage="run_review_handoff"),
            Transition(from_stage="audit_editorial_path", on="blocked", terminal="blocked"),
            Transition(from_stage="run_review_handoff", on="noop", to_stage="advance_lifecycle"),
            Transition(from_stage="run_review_handoff", on="handled", to_stage="refresh_blackboard"),
            Transition(from_stage="run_review_handoff", on="blocked", terminal="blocked"),
            Transition(from_stage="advance_lifecycle", on="noop", terminal="done"),
            Transition(from_stage="advance_lifecycle", on="advanced", terminal="done"),
            Transition(from_stage="advance_lifecycle", on="agent_gate_required", terminal="done"),
            Transition(from_stage="advance_lifecycle", on="human_gate_published", to_stage="refresh_blackboard"),
            Transition(from_stage="advance_lifecycle", on="blocked", terminal="blocked"),
            Transition(from_stage="refresh_blackboard", on="refreshed", terminal="done"),
            Transition(from_stage="refresh_blackboard", on="blocked", terminal="blocked"),
        ),
    )


def run_owned_ivy_jonah(
    *,
    openclaw_root: str | Path,
    ledger_path: str | Path,
    stale_minutes: int = 90,
    dry_run: bool = False,
    instance_id: str | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    openclaw = Path(openclaw_root).expanduser().resolve()
    ledger_file = Path(ledger_path).expanduser().resolve()
    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = _iso(now)
    workflow = ivy_jonah_owned_workflow()
    ledger = WorkflowLedger(ledger_file)
    resolved_instance_id = instance_id or f"openclaw-ivy-jonah-owned:{timestamp}:{uuid.uuid4().hex[:12]}"
    try:
        ledger.initialize()
        adapter = OpenClawIvyJonahOwnedAdapter(
            openclaw_root=openclaw,
            stale_minutes=stale_minutes,
            dry_run=dry_run,
            created_at=timestamp,
        )
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        kernel = WorkflowKernel(
            ledger,
            workflow,
            KernelRuntimeConfig(owner_id=OWNER_ID, adapter_registry=registry),
        )
        if ledger.get_workflow_instance(resolved_instance_id) is None:
            kernel.start(
                instance_id=resolved_instance_id,
                inputs={
                    "openclaw_root": str(openclaw),
                    "stale_minutes": stale_minutes,
                    "dry_run": dry_run,
                    "owned_runner": "openclaw.ivy_jonah.native",
                },
                idempotency_key=resolved_instance_id,
                now=timestamp,
            )
        summary = WorkflowRunner(ledger, owner_id=OWNER_ID).run_kernel_until_idle(
            kernel,
            instance_id=resolved_instance_id,
            now=timestamp,
        )
        return _summary(
            ledger=ledger,
            workflow=workflow,
            instance_id=resolved_instance_id,
            summary=summary,
            openclaw_root=openclaw,
            ledger_path=ledger_file,
            timestamp=timestamp,
            dry_run=dry_run,
        )
    finally:
        ledger.close()


def _summary(
    *,
    ledger: WorkflowLedger,
    workflow: WorkflowDef,
    instance_id: str,
    summary: Any,
    openclaw_root: Path,
    ledger_path: Path,
    timestamp: str,
    dry_run: bool,
) -> dict[str, Any]:
    instance = ledger.get_workflow_instance(instance_id)
    rows = ledger.connection.execute(
        """
        SELECT stage_run_id, stage_id, status, receipt_id, failure_class, failure_summary
        FROM stage_runs
        WHERE instance_id = ?
        ORDER BY created_at, stage_run_id
        """,
        (instance_id,),
    ).fetchall()
    receipts = ledger.connection.execute(
        """
        SELECT receipt_json
        FROM receipts
        WHERE instance_id = ?
        ORDER BY created_at, receipt_id
        """,
        (instance_id,),
    ).fetchall()
    receipt_payloads = [json.loads(row["receipt_json"]) for row in receipts]
    handled_receipts = [
        receipt
        for receipt in receipt_payloads
        if (receipt.get("runtime_provenance") or {}).get("outputs", {}).get("outcome") == "handled"
    ]
    noop_receipts = [
        receipt
        for receipt in receipt_payloads
        if (receipt.get("runtime_provenance") or {}).get("outputs", {}).get("outcome") == "noop"
    ]
    stage_payloads = _stage_command_payloads(receipt_payloads)
    handoff_payload = stage_payloads.get("run_review_handoff") or {}
    lifecycle_payload = stage_payloads.get("advance_lifecycle") or {}
    handoff_json = _mapping_or_empty(handoff_payload.get("stdout_json"))
    lifecycle_json = _mapping_or_empty(lifecycle_payload.get("stdout_json"))
    handoff_action = str(handoff_json.get("action") or "")
    lifecycle_action = str(lifecycle_json.get("action") or "")
    action = (
        handoff_action
        if handled_receipts and handoff_action
        else lifecycle_action
        if lifecycle_action and lifecycle_action != "dry_run"
        else "noop"
        if noop_receipts
        else "blocked"
    )
    pass_through_keys = (
        "project_id",
        "handoff_path",
        "operator_summary_path",
        "obsidian_path",
        "browser_plan_path",
        "publish_ready_path",
        "publish_staging_path",
        "artifact_path",
        "attention_path",
        "review_note",
        "review_note_rel",
        "artifact_record",
        "from_gate",
        "to_gate",
        "advanced_from_gate",
        "advanced_to_gate",
        "gate",
        "next_action",
        "owner",
        "reason",
    )
    pass_through_source = {**handoff_json, **lifecycle_json}
    pass_through = {key: pass_through_source.get(key) for key in pass_through_keys if pass_through_source.get(key)}
    stage_order = {stage.id: index for index, stage in enumerate(workflow.stages)}
    stage_runs = sorted(
        [dict(row) for row in rows],
        key=lambda row: (stage_order.get(str(row["stage_id"]), 999), str(row["stage_run_id"])),
    )
    return {
        "schema": SCHEMA,
        "ok": getattr(summary, "status", None) == "done",
        "mode": "dry_run" if dry_run else "run",
        "dry_run": dry_run,
        "created_at": timestamp,
        "workflow_id": workflow.id,
        "workflow_version": workflow.version,
        "instance_id": instance_id,
        "status": getattr(summary, "status", None),
        "stop_reason": getattr(summary, "stop_reason", None),
        "workflow_status": instance.status.value if instance is not None else None,
        "current_stage_id": instance.current_stage_id if instance is not None else None,
        "action": action,
        "compatibility_action": "handled" if handled_receipts else "noop" if noop_receipts else "blocked",
        "runner_result": dict(handoff_json),
        "lifecycle_result": dict(lifecycle_json),
        **pass_through,
        "openclaw_root": str(openclaw_root),
        "ledger_path": str(ledger_path),
        "legacy_compatibility_adapter": False,
        "external_publish_performed": False,
        "public_publish_allowed": False,
        "stage_runs": stage_runs,
        "receipt_ids": [receipt["receipt_id"] for receipt in receipt_payloads],
        "receipts": [
            {
                "stage_id": receipt.get("stage_id"),
                "status": receipt.get("status"),
                "adapter_id": (receipt.get("runtime_provenance") or {}).get("adapter_id"),
                "operation": (receipt.get("runtime_provenance") or {}).get("operation"),
                "outcome": (receipt.get("runtime_provenance") or {}).get("outputs", {}).get("outcome"),
                "legacy_compatibility_adapter": (receipt.get("runtime_provenance") or {})
                .get("outputs", {})
                .get("legacy_compatibility_adapter"),
            }
            for receipt in receipt_payloads
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AWK-owned Ivy/Jonah OpenClaw cutover runner.")
    parser.add_argument("--openclaw-root", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--stale-minutes", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--instance-id")
    parser.add_argument("--summary-json", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = run_owned_ivy_jonah(
        openclaw_root=args.openclaw_root,
        ledger_path=args.ledger,
        stale_minutes=args.stale_minutes,
        dry_run=args.dry_run,
        instance_id=args.instance_id,
    )
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["ok"] else 1


def _sample(path: Path, limit: int = 7000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def _parse_agent_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _iso(now: datetime | str | None) -> str:
    if isinstance(now, str):
        return now
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_gate(value: str) -> str:
    value = value.strip().upper()
    return {"M1": "P1", "M2": "P2", "M3": "P3", "M4": "P4", "M5": "P5"}.get(value, value or "P1")


def _next_gate(gate: str) -> str | None:
    gates = ["P1", "P2", "P3", "P4", "P5"]
    gate = _normalize_gate(gate)
    try:
        index = gates.index(gate)
    except ValueError:
        return None
    return gates[index + 1] if index + 1 < len(gates) else None


def _gate_artifact_path(project_id: str, gate: str) -> str:
    names = {
        "P1": "p1_scout.md",
        "P2": "p2_deep_dive.md",
        "P3": "p3_research_brief.md",
        "P4": "p4_draft_package.md",
        "P5": "p5_final_review.md",
    }
    return f"projects/{project_id}/{names.get(_normalize_gate(gate), 'project.json')}"


def _agent_gate_required(project: Mapping[str, Any], gate: str, next_gate: str) -> dict[str, str] | None:
    if (
        _normalize_gate(gate) == "P4"
        and _normalize_gate(next_gate) == "P5"
        and str(project.get("target_channel") or "") == "substack_medium"
    ):
        return {
            "owner": "jonah_editor",
            "reason": "substack_medium P4 requires Jonah editor review before P5",
            "next_action": "delegate Jonah editor review before P5 final refinement",
        }
    return None


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _short(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _stage_command_payloads(receipt_payloads: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for receipt in receipt_payloads:
        outputs = (receipt.get("runtime_provenance") or {}).get("outputs", {})
        if not isinstance(outputs, Mapping):
            continue
        command = outputs.get("command")
        if isinstance(command, Mapping):
            payloads[str(receipt.get("stage_id") or "")] = dict(command)
    return payloads


if __name__ == "__main__":
    raise SystemExit(main())
