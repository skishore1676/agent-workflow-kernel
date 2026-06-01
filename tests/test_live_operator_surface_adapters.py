import json
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterInvocation,
    LiveObsidianMarkdownSurfaceAdapter,
    SurfaceAdapter,
)
from agent_workflow_kernel.local_adapters import OpenClawTelegramSurfaceAdapter  # noqa: E402


def invocation(adapter_id: str, operation: str = "publish") -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"invoke-{adapter_id.replace('.', '-')}-{operation}",
        workflow_id="workflow-1",
        instance_id="instance-1",
        stage_run_id="run-1",
        adapter_family=AdapterFamily.SURFACE,
        adapter_id=adapter_id,
        operation=operation,
        input_ref="input:1",
        context_packet_ref="context:1",
        idempotency_key="idempotency-1",
    )


def live_packet(**overrides: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "title": "Live operator review",
        "stage_id": "review",
        "human_ask": "Choose the next state.",
        "allowed_decisions": ("approved", "rejected", "revise"),
        "requested_action": "record_operator_review_decision",
        "exact_action": "Record operator review decision only.",
        "action_fingerprint": "sha256:live-review",
        "evidence_refs": ("fixture://live-review",),
        "gate_id": "gate-1",
        "human_ref": "Suman",
        "live_operator_surface_allowed": True,
        "public_publish_blocked": True,
    }
    packet.update(overrides)
    return packet


class LiveOperatorSurfaceAdaptersTest(unittest.TestCase):
    def test_live_adapters_declare_guarded_live_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            obsidian = LiveObsidianMarkdownSurfaceAdapter(
                Path(temp_dir) / "vault",
                allowed_relative_prefix="Operator",
                allow_live_write=True,
            )
            telegram = OpenClawTelegramSurfaceAdapter(
                account="operator",
                target="suman",
                allow_live_send=True,
            )

            for adapter in (obsidian, telegram):
                with self.subTest(adapter=adapter.adapter_id):
                    capabilities = adapter.capabilities()
                    contract = capabilities.metadata["surface_contract"]

                    self.assertIsInstance(adapter, SurfaceAdapter)
                    self.assertEqual(capabilities.family, AdapterFamily.SURFACE)
                    self.assertEqual(capabilities.metadata["mutation_mode"], "live")
                    self.assertEqual(capabilities.metadata["write_class"], "live_operator_surface")
                    self.assertTrue(capabilities.metadata["live_mutation_allowed"])
                    self.assertTrue(contract["live_mutation_allowed"])
                    self.assertTrue(contract["readback_required"])
                    self.assertTrue(contract["metadata"]["public_publish_blocked"])
                    self.assertTrue(contract["metadata"]["requires_packet_live_operator_surface_allowed"])

    def test_obsidian_publish_readback_idempotent_and_checked_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = LiveObsidianMarkdownSurfaceAdapter(
                Path(temp_dir) / "vault",
                allowed_relative_prefix="Operator",
                allow_live_write=True,
            )
            call = invocation(adapter.adapter_id)

            first = adapter.publish(call, live_packet(note_path="Reviews/review.md"))
            second = adapter.publish(call, live_packet(note_path="Reviews/review.md"))
            readback = adapter.readback(first.outputs["surface_ref"])
            note_path = Path(first.outputs["note_path"])
            note_text = note_path.read_text(encoding="utf-8")
            note_path.write_text(
                note_text.replace("- [ ] `approved`", "- [x] `approved`"),
                encoding="utf-8",
            )
            decisions = adapter.ingest_decisions(
                {
                    "query_id": "obsidian-live-decision-1",
                    "surface_ref": first.outputs["surface_ref"],
                    "allowed_decisions": first.outputs["allowed_decisions"],
                    "exact_action": first.outputs["exact_action"],
                    "expected_action_fingerprint": first.outputs["action_fingerprint"],
                    "evidence_refs": first.outputs["evidence_refs"],
                    "human_ref": first.outputs["human_ref"],
                    "gate_id": first.outputs["gate_id"],
                }
            )

        self.assertEqual(first.status, "succeeded")
        self.assertEqual(second.status, "succeeded")
        self.assertTrue(second.outputs["idempotency_replayed"])
        self.assertIn("/Operator/Reviews/review.md", first.outputs["note_path"])
        self.assertTrue(first.outputs["live_operator_surface_allowed"])
        self.assertTrue(first.outputs["public_publish_blocked"])
        self.assertEqual(readback.status, "succeeded")
        self.assertTrue(readback.runtime_provenance["outputs"]["readback_confirmed"])
        self.assertTrue(readback.runtime_provenance["outputs"]["hash_matches"])
        self.assertIn("live_operator_surface_allowed: true", note_text)
        self.assertIn("public_publish_blocked: true", note_text)
        self.assertEqual(decisions[0].status, "succeeded")
        outputs = decisions[0].runtime_provenance["outputs"]
        self.assertEqual(outputs["schema"], "live_operator_surface_decision.v1")
        self.assertEqual(outputs["decision"], "approved")
        self.assertEqual(outputs["exact_action_approved"], "Record operator review decision only.")
        self.assertFalse(outputs["test_only"])
        self.assertFalse(outputs["non_live"])
        self.assertTrue(outputs["live_operator_surface_allowed"])

    def test_obsidian_refuses_missing_allow_flag_path_traversal_and_unsafe_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            blocked_adapter = LiveObsidianMarkdownSurfaceAdapter(
                Path(temp_dir) / "vault",
                allowed_relative_prefix="Operator",
            )
            adapter = LiveObsidianMarkdownSurfaceAdapter(
                Path(temp_dir) / "vault",
                allowed_relative_prefix="Operator",
                allow_live_write=True,
            )

            blocked_config = blocked_adapter.publish(
                invocation(blocked_adapter.adapter_id),
                live_packet(note_path="Review.md"),
            )
            missing_packet_flag = adapter.publish(
                invocation(adapter.adapter_id),
                live_packet(note_path="Review.md", live_operator_surface_allowed=False),
            )
            traversal = adapter.publish(
                invocation(adapter.adapter_id),
                live_packet(note_path="../outside.md"),
            )
            unsafe = adapter.publish(
                invocation(adapter.adapter_id),
                live_packet(exact_action="Deploy production runtime mutation."),
            )
            ambiguous = adapter.publish(
                invocation(adapter.adapter_id),
                live_packet(mutation_permission_granted=True),
            )

        self.assertEqual(blocked_config.status, "blocked")
        self.assertEqual(blocked_config.outputs["error"]["error_class"], "live_mutation_refused")
        self.assertEqual(missing_packet_flag.status, "blocked")
        self.assertEqual(missing_packet_flag.outputs["error"]["error_class"], "live_mutation_refused")
        self.assertEqual(traversal.status, "blocked")
        self.assertEqual(traversal.outputs["error"]["error_class"], "path_traversal_refused")
        self.assertEqual(unsafe.status, "blocked")
        self.assertEqual(unsafe.outputs["error"]["error_class"], "unsafe_live_surface_scope_refused")
        self.assertEqual(ambiguous.status, "blocked")
        self.assertEqual(ambiguous.outputs["error"]["error_class"], "live_mutation_refused")

    def test_telegram_send_uses_openclaw_cli_readback_and_idempotency_receipt(self) -> None:
        calls: list[list[str]] = []

        def fake_runner(command: list[str], **_: object) -> CompletedProcess:
            calls.append(command)
            return CompletedProcess(command, 0, stdout=json.dumps({"ok": True, "message_id": "tg-123"}), stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = OpenClawTelegramSurfaceAdapter(
                account="operator",
                target="suman",
                allow_live_send=True,
                receipt_dir=Path(temp_dir) / "receipts",
                runner=fake_runner,
            )
            call = invocation(adapter.adapter_id)

            first = adapter.publish(call, live_packet(message="Operator review ready."))
            second = adapter.publish(call, live_packet(message="Operator review ready."))
            readback = adapter.readback(first.outputs["surface_ref"])

        self.assertEqual(first.status, "succeeded")
        self.assertEqual(second.status, "succeeded")
        self.assertTrue(second.outputs["idempotency_replayed"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0],
            [
                "openclaw",
                "message",
                "send",
                "--channel",
                "telegram",
                "--account",
                "operator",
                "--target",
                "suman",
                "--message",
                "Operator review ready.",
                "--json",
            ],
        )
        self.assertTrue(first.outputs["network_call_performed"])
        self.assertTrue(first.outputs["live_operator_surface_allowed"])
        self.assertEqual(first.outputs["message_id"], "tg-123")
        self.assertEqual(readback.status, "succeeded")
        self.assertTrue(readback.runtime_provenance["outputs"]["readback_confirmed"])
        self.assertEqual(readback.runtime_provenance["outputs"]["send_receipt"]["message_id"], "tg-123")

    def test_telegram_refuses_without_flags_and_unsafe_actions_without_calling_runner(self) -> None:
        calls: list[list[str]] = []

        def fake_runner(command: list[str], **_: object) -> CompletedProcess:
            calls.append(command)
            return CompletedProcess(command, 0, stdout="{}", stderr="")

        adapter = OpenClawTelegramSurfaceAdapter(
            account="operator",
            target="suman",
            runner=fake_runner,
        )
        allowed_adapter = OpenClawTelegramSurfaceAdapter(
            account="operator",
            target="suman",
            allow_live_send=True,
            runner=fake_runner,
        )

        missing_adapter_flag = adapter.publish(invocation(adapter.adapter_id), live_packet(message="Ready."))
        missing_packet_flag = allowed_adapter.publish(
            invocation(allowed_adapter.adapter_id),
            live_packet(message="Ready.", live_operator_surface_allowed=False),
        )
        unsafe = allowed_adapter.publish(
            invocation(allowed_adapter.adapter_id),
            live_packet(message="Place a live trade now."),
        )

        self.assertEqual(missing_adapter_flag.status, "blocked")
        self.assertEqual(missing_adapter_flag.outputs["error"]["error_class"], "live_mutation_refused")
        self.assertEqual(missing_packet_flag.status, "blocked")
        self.assertEqual(missing_packet_flag.outputs["error"]["error_class"], "live_mutation_refused")
        self.assertEqual(unsafe.status, "blocked")
        self.assertEqual(unsafe.outputs["error"]["error_class"], "unsafe_live_surface_scope_refused")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
