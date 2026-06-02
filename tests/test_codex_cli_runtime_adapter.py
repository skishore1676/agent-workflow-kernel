import json
import os
import stat
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "codex_cli"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterInvocation,
    AdapterRegistry,
    RiskClass,
    RuntimeAdapter,
    StageType,
)
from agent_workflow_kernel_codex_cli import (  # noqa: E402
    CodexCliExecRuntimeAdapter,
    CodexCliSessionRuntimeAdapter,
    codex_cli_runtime_registrations,
)


SESSION_ID = "11111111-2222-3333-4444-555555555555"


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


def write_fake_codex(path: Path, *, emit_session: bool = True) -> None:
    session_expr = repr(SESSION_ID) if emit_session else "None"
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            prompt = sys.stdin.read()
            output_path = None
            for index, item in enumerate(args):
                if item in ("--output-last-message", "-o"):
                    output_path = args[index + 1]
                    break
            if output_path is None:
                raise SystemExit("missing output path")

            resume_index = args.index("resume") if "resume" in args else -1
            resumed = resume_index >= 0
            resumed_session_id = None
            if resumed:
                skip_value = False
                for item in args[resume_index + 1:]:
                    if skip_value:
                        skip_value = False
                        continue
                    if item.startswith("-"):
                        if item in ("--output-last-message", "-o", "--config", "-c", "--model", "-m"):
                            skip_value = True
                        continue
                    if item == output_path:
                        continue
                    resumed_session_id = item
                    break

            Path(output_path).write_text(
                json.dumps({{"ok": True, "resumed": resumed, "prompt": prompt}}),
                encoding="utf-8",
            )
            event = {{
                "type": "turn.completed",
                "usage": {{"input_tokens": 10, "output_tokens": 5}},
                "argv": args,
                "resumed_session_id": resumed_session_id,
            }}
            session_id = {session_expr}
            if session_id:
                event["session_id"] = session_id
            print(json.dumps(event))
            """
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class CodexCliRuntimeAdapterTest(unittest.TestCase):
    def test_exec_adapter_invokes_codex_exec_and_captures_artifacts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake = temp / "codex"
            artifacts = temp / "artifacts"
            write_fake_codex(fake)
            adapter = CodexCliExecRuntimeAdapter(
                executable=str(fake),
                default_cwd=str(temp),
                timeout_seconds=5,
            )

            result = adapter.invoke(
                invocation(adapter.adapter_id),
                {
                    "prompt": "Return a tiny fixture result.",
                    "codex_cli": {"artifact_dir": str(artifacts)},
                },
            )

            self.assertIsInstance(adapter, RuntimeAdapter)
            self.assertEqual(result.status, "succeeded")
            self.assertEqual(result.outputs["mode"], "one_shot")
            self.assertFalse(result.outputs["session"]["session_reused"])
            self.assertEqual(result.outputs["usage"]["total_tokens"], 15)
            self.assertEqual(result.outputs["structured_result"]["ok"], True)
            command = result.outputs["command"]
            self.assertIn("exec", command)
            self.assertNotIn("resume", command)
            self.assertTrue(Path(result.outputs["artifacts"]["last_message"].removeprefix("file://")).exists())

    def test_session_adapter_reuses_captured_session_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake = temp / "codex"
            artifacts = temp / "artifacts"
            write_fake_codex(fake)
            adapter = CodexCliSessionRuntimeAdapter(
                executable=str(fake),
                default_cwd=str(temp),
                timeout_seconds=5,
            )
            runtime_input = {
                "prompt": "Stage one.",
                "actor_ref": "codex_worker",
                "codex_cli": {"artifact_dir": str(artifacts), "max_session_turns": 10},
            }

            first = adapter.invoke(invocation(adapter.adapter_id, stage_run_id="run-1"), runtime_input)
            second = adapter.invoke(
                invocation(adapter.adapter_id, stage_run_id="run-2"),
                {**runtime_input, "prompt": "Stage two."},
            )

            self.assertEqual(first.status, "succeeded")
            self.assertEqual(first.outputs["session"]["session_id"], SESSION_ID)
            self.assertFalse(first.outputs["session"]["session_reused"])
            self.assertEqual(first.outputs["session"]["turn_count"], 1)
            self.assertEqual(second.status, "succeeded")
            self.assertTrue(second.outputs["session"]["session_reused"])
            self.assertEqual(second.outputs["session"]["session_id"], SESSION_ID)
            self.assertEqual(second.outputs["session"]["turn_count"], 2)
            self.assertIn("resume", second.outputs["command"])
            self.assertIn(SESSION_ID, second.outputs["command"])

    def test_session_adapter_fails_when_session_id_is_not_trackable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake = temp / "codex"
            write_fake_codex(fake, emit_session=False)
            adapter = CodexCliSessionRuntimeAdapter(
                executable=str(fake),
                default_cwd=str(temp),
                timeout_seconds=5,
            )

            result = adapter.invoke(
                invocation(adapter.adapter_id),
                {"prompt": "Need a trackable session.", "codex_cli": {"artifact_dir": str(temp)}},
            )

            self.assertEqual(result.status, "failed")
            self.assertFalse(result.outputs["session"]["session_trackable"])
            self.assertEqual(adapter.sessions, {})

    def test_registration_helper_registers_both_runtime_adapters(self) -> None:
        registrations = codex_cli_runtime_registrations(executable="/usr/bin/false")
        registry = AdapterRegistry(registrations)

        exec_registration = registry.resolve(
            "runtime.codex_cli_exec",
            stage_type=StageType.AGENT_WORK,
        )
        session_registration = registry.resolve(
            "runtime.codex_cli_session",
            stage_type=StageType.AGENT_WORK,
        )

        self.assertEqual(exec_registration.family, AdapterFamily.RUNTIME)
        self.assertEqual(session_registration.family, AdapterFamily.RUNTIME)
        self.assertIn(RiskClass.LOCAL_DRAFT, session_registration.side_effects)
        self.assertTrue(session_registration.supports("invoke"))


if __name__ == "__main__":
    unittest.main()
