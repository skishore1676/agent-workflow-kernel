import json
import sys
import unittest
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "codex_sdk"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterInvocation,
    AdapterRegistry,
    RiskClass,
    RuntimeAdapter,
    StageType,
)
from agent_workflow_kernel_codex_sdk import (  # noqa: E402
    CodexSdkSessionRuntimeAdapter,
    codex_sdk_runtime_registrations,
)


class FakeSandbox(Enum):
    read_only = "read-only"
    workspace_write = "workspace-write"
    full_access = "full-access"


class FakeApprovalMode(Enum):
    deny_all = "deny_all"
    auto_review = "auto_review"


@dataclass
class FakeUsageBreakdown:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeUsage:
    total: FakeUsageBreakdown
    last: FakeUsageBreakdown


@dataclass
class FakeTurnResult:
    id: str
    status: str
    final_response: str
    usage: FakeUsage
    items: list[dict[str, object]]


class FakeThread:
    def __init__(self, client: "FakeCodexClient", thread_id: str) -> None:
        self.client = client
        self.id = thread_id

    def run(self, input: str, **kwargs: object) -> FakeTurnResult:
        self.client.runs.append({"thread_id": self.id, "input": input, "kwargs": kwargs})
        turn_index = len(self.client.runs)
        return FakeTurnResult(
            id=f"turn-{turn_index}",
            status="completed",
            final_response=json.dumps(
                {
                    "ok": True,
                    "thread_id": self.id,
                    "turn": turn_index,
                    "prompt": input,
                }
            ),
            usage=FakeUsage(
                total=FakeUsageBreakdown(input_tokens=20, output_tokens=8),
                last=FakeUsageBreakdown(input_tokens=12, output_tokens=4),
            ),
            items=[{"type": "assistant_message", "text": "done"}],
        )


class FakeCodexClient:
    def __init__(self) -> None:
        self.started: list[dict[str, object]] = []
        self.resumed: list[dict[str, object]] = []
        self.runs: list[dict[str, object]] = []
        self.closed = False

    def thread_start(self, **kwargs: object) -> FakeThread:
        self.started.append(kwargs)
        return FakeThread(self, "thread-1")

    def thread_resume(self, thread_id: str, **kwargs: object) -> FakeThread:
        self.resumed.append({"thread_id": thread_id, "kwargs": kwargs})
        return FakeThread(self, thread_id)

    def close(self) -> None:
        self.closed = True


def invocation(adapter_id: str, *, stage_run_id: str = "run-1") -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"invoke-{stage_run_id}",
        workflow_id="workflow-1",
        instance_id="instance-1",
        stage_run_id=stage_run_id,
        adapter_family=AdapterFamily.RUNTIME,
        adapter_id=adapter_id,
        operation="invoke",
        input_ref="input:1",
        context_packet_ref="context:1",
        idempotency_key=f"idempotency-{stage_run_id}",
    )


def fake_sdk() -> dict[str, object]:
    return {
        "Sandbox": FakeSandbox,
        "ApprovalMode": FakeApprovalMode,
        "version": "fake-sdk",
    }


class CodexSdkRuntimeAdapterTest(unittest.TestCase):
    def test_sdk_session_adapter_starts_thread_and_captures_artifacts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_client = FakeCodexClient()
            adapter = CodexSdkSessionRuntimeAdapter(
                default_cwd=str(temp),
                timeout_seconds=5,
                client_factory=lambda: fake_client,
                sdk_module=fake_sdk(),
            )

            result = adapter.invoke(
                invocation(adapter.adapter_id),
                {
                    "prompt": "Inspect a fixture and return JSON.",
                    "actor_ref": "sdk_worker",
                    "codex_sdk": {"artifact_dir": str(temp / "artifacts")},
                },
            )

            self.assertIsInstance(adapter, RuntimeAdapter)
            self.assertEqual(result.status, "succeeded")
            self.assertEqual(result.outputs["mode"], "bounded_session")
            self.assertEqual(result.outputs["session"]["thread_id"], "thread-1")
            self.assertFalse(result.outputs["session"]["session_reused"])
            self.assertEqual(result.outputs["session"]["turn_count"], 1)
            self.assertEqual(result.outputs["usage"]["input_tokens"], 20)
            self.assertEqual(result.outputs["usage"]["output_tokens"], 8)
            self.assertEqual(result.outputs["usage"]["total_tokens"], 28)
            self.assertEqual(result.outputs["structured_result"]["ok"], True)
            self.assertEqual(fake_client.started[0]["sandbox"], FakeSandbox.read_only)
            self.assertEqual(fake_client.started[0]["approval_mode"], FakeApprovalMode.deny_all)
            self.assertEqual(fake_client.runs[0]["kwargs"]["sandbox"], FakeSandbox.read_only)
            last_message = Path(result.outputs["artifacts"]["last_message"].removeprefix("file://"))
            turn_result = Path(result.outputs["artifacts"]["turn_result"].removeprefix("file://"))
            self.assertTrue(last_message.exists())
            self.assertTrue(turn_result.exists())
            self.assertTrue(fake_client.closed)

    def test_sdk_session_adapter_resumes_captured_thread_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_client = FakeCodexClient()
            adapter = CodexSdkSessionRuntimeAdapter(
                default_cwd=str(temp),
                timeout_seconds=5,
                client_factory=lambda: fake_client,
                sdk_module=fake_sdk(),
            )
            runtime_input = {
                "prompt": "Stage one.",
                "actor_ref": "sdk_worker",
                "codex_sdk": {"artifact_dir": str(temp / "artifacts"), "max_session_turns": 5},
            }

            first = adapter.invoke(invocation(adapter.adapter_id, stage_run_id="run-1"), runtime_input)
            second = adapter.invoke(
                invocation(adapter.adapter_id, stage_run_id="run-2"),
                {**runtime_input, "prompt": "Stage two."},
            )

            self.assertEqual(first.status, "succeeded")
            self.assertEqual(second.status, "succeeded")
            self.assertEqual(first.outputs["session"]["thread_id"], "thread-1")
            self.assertEqual(second.outputs["session"]["thread_id"], "thread-1")
            self.assertTrue(second.outputs["session"]["session_reused"])
            self.assertEqual(second.outputs["session"]["turn_count"], 2)
            self.assertEqual(fake_client.resumed[0]["thread_id"], "thread-1")

    def test_sdk_session_adapter_fails_without_importable_sdk(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            adapter = CodexSdkSessionRuntimeAdapter(
                default_cwd=str(temp),
                timeout_seconds=5,
                sdk_module={},
            )

            result = adapter.invoke(
                invocation(adapter.adapter_id),
                {"prompt": "Need real SDK.", "codex_sdk": {"artifact_dir": str(temp)}},
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.outputs["error"]["class"], "RuntimeError")
            self.assertFalse(result.outputs["session"]["session_trackable"])
            self.assertEqual(adapter.sessions, {})

    def test_registration_helper_registers_preferred_sdk_runtime_adapter(self) -> None:
        registrations = codex_sdk_runtime_registrations(
            client_factory=FakeCodexClient,
            sdk_module=fake_sdk(),
        )
        registry = AdapterRegistry(registrations)

        registration = registry.resolve(
            "runtime.codex_sdk_session",
            stage_type=StageType.AGENT_WORK,
        )

        self.assertEqual(registration.family, AdapterFamily.RUNTIME)
        self.assertIn(RiskClass.LOCAL_DRAFT, registration.side_effects)
        self.assertTrue(registration.supports("invoke"))
        self.assertTrue(registration.metadata["preferred"])
        self.assertEqual(registration.metadata["fallback_adapter"], "runtime.codex_cli_session")


if __name__ == "__main__":
    unittest.main()
