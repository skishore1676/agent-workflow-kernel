import json
import sys
from subprocess import CompletedProcess
from typing import Any, Mapping
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterInvocation,
    AdapterRegistry,
    RiskClass,
    StageType,
)
from agent_workflow_kernel_openclaw import (  # noqa: E402
    OpenClawAgentRuntimeAdapter,
    openclaw_agent_runtime_registrations,
)


class RecordingRunner:
    def __init__(self, responses: list[CompletedProcess[str]]) -> None:
        self.responses = list(responses)
        self.calls: list[list[str]] = []

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
    ) -> CompletedProcess[str]:
        del cwd, env, text, capture_output, timeout, check
        self.calls.append(list(cmd))
        if not self.responses:
            raise RuntimeError("openclaw CLI call had no mocked response")
        return self.responses.pop(0)


def invocation(*, stage_run_id: str = "stage-run") -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"invoke-{stage_run_id}",
        workflow_id="workflow",
        instance_id="instance",
        stage_run_id=stage_run_id,
        adapter_family=AdapterFamily.RUNTIME,
        adapter_id="runtime.openclaw_agent",
        operation="invoke",
        input_ref="input",
        context_packet_ref="context:workflow",
        idempotency_key=stage_run_id,
    )


class OpenClawAgentRuntimeAdapterTest(unittest.TestCase):
    def test_agent_mode_invocation_and_poll_use_agent_command(self) -> None:
        runner = RecordingRunner(
            [
                CompletedProcess(
                    args=["openclaw", "agent"],
                    returncode=0,
                    stdout=json.dumps({"session": {"session_id": "sess-agent-1", "status": "running"}}),
                ),
                CompletedProcess(
                    args=["openclaw", "sessions"],
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "sessions": [
                                {
                                    "session_key": "agent:editorial:ivy-jonah",
                                    "session_id": "sess-agent-1",
                                    "status": "done",
                                }
                            ]
                        }
                    ),
                ),
            ]
        )

        adapter = OpenClawAgentRuntimeAdapter(
            default_agent="agent-junior",
            runner=runner,
            artifact_root=Path("/tmp") / "openclaw-runtime-tests",
        )

        result = adapter.invoke(
            invocation(),
            {
                "stage": {"id": "editor_review", "budget": {}},
                "openclaw_agent": {
                    "agent": "editorial-ivy",
                    "session_key": "agent:editorial:ivy-jonah",
                },
            },
        )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.outputs["session"]["session_key"], "agent:editorial:ivy-jonah")
        self.assertEqual(result.outputs["session"]["session_id"], "sess-agent-1")
        self.assertEqual(result.outputs["session"]["command_mode"], "agent")

        init_command = runner.calls[0]
        self.assertEqual(init_command[:2], ["openclaw", "agent"])
        self.assertIn("--agent", init_command)
        self.assertIn("editorial-ivy", init_command)
        self.assertIn("--json", init_command)

        poll = adapter.poll({"session_key": "agent:editorial:ivy-jonah"})
        self.assertEqual(poll.status, "succeeded")
        self.assertEqual(poll.outputs["status_output"].get("status"), "done")

        poll_command = runner.calls[1]
        self.assertEqual(poll_command[:2], ["openclaw", "sessions"])
        self.assertIn("--json", poll_command)
        self.assertIn("--agent", poll_command)
        self.assertIn("editorial-ivy", poll_command)

    def test_session_start_mode_tracks_session_mode_and_proof_cancel(self) -> None:
        runner = RecordingRunner(
            [
                CompletedProcess(
                    args=["openclaw", "session", "start"],
                    returncode=0,
                    stdout=json.dumps({"result": {"session": {"sessionId": "sess-session-2"}}}),
                ),
                CompletedProcess(
                    args=["openclaw", "session", "proof"],
                    returncode=0,
                    stdout=json.dumps({"ok": True, "summary": "proof"}),
                ),
                CompletedProcess(
                    args=["openclaw", "session", "cancel"],
                    returncode=0,
                    stdout="{}",
                ),
            ]
        )

        adapter = OpenClawAgentRuntimeAdapter(
            default_command_mode="session_start",
            default_agent="agent-ivy",
            runner=runner,
            artifact_root=Path("/tmp") / "openclaw-runtime-tests",
        )

        result = adapter.invoke(
            invocation(stage_run_id="session-mode"),
            {
                "stage": {"id": "editor_review", "budget": {}},
                "openclaw_agent": {
                    "session_key": "agent:editorial:ivy-session",
                    "agent": "editorial-jonah",
                },
            },
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.outputs["session"]["command_mode"], "session_start")
        self.assertEqual(result.outputs["session"]["session_id"], "sess-session-2")

        invoke_command = runner.calls[0]
        self.assertEqual(invoke_command[:3], ["openclaw", "session", "start"])
        self.assertIn("--packet", invoke_command)

        proof = adapter.collect_proof(
            {"session_key": "agent:editorial:ivy-session"},
            {"id": "proof-request"},
        )
        self.assertEqual(proof.status, "succeeded")
        self.assertEqual(proof.runtime_provenance["outputs"]["proof"]["summary"], "proof")

        proof_command = runner.calls[1]
        self.assertEqual(proof_command[:3], ["openclaw", "session", "proof"])

        cancel = adapter.cancel({"session_key": "agent:editorial:ivy-session"}, "unit-test")
        self.assertEqual(cancel.runtime_provenance["outputs"]["status"], "cancel_requested")
        cancel_command = runner.calls[2]
        self.assertEqual(cancel_command[:2], ["openclaw", "session"])
        self.assertIn("cancel", cancel_command)

    def test_registration_helper_registers_openclaw_agent_runtime(self) -> None:
        registrations = openclaw_agent_runtime_registrations(openclaw_cli="openclaw")
        registry = AdapterRegistry(registrations)

        registration = registry.resolve("runtime.openclaw_agent", stage_type=StageType.AGENT_WORK)

        self.assertEqual(registration.family, AdapterFamily.RUNTIME)
        self.assertTrue(registration.side_effects)
        self.assertIn(RiskClass.LOCAL_DRAFT, registration.side_effects)
        self.assertTrue(registration.supports("invoke"))


if __name__ == "__main__":
    unittest.main()
