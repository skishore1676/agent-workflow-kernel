import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterInvocation,
    AdapterRegistration,
    AdapterRegistry,
    KernelRuntimeConfig,
    LocalFakeRuntimeAdapter,
    SandboxObsidianMarkdownSurfaceAdapter,
    SandboxTelegramOutboxSurfaceAdapter,
    StageDef,
    StageRunStatus,
    StageType,
    SurfaceAdapter,
    Transition,
    WorkflowDef,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowRunner,
    WorkflowStatus,
)


UTC = timezone.utc


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


def review_packet(**overrides: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "title": "Sandbox review",
        "stage_id": "review",
        "human_ask": "Choose the next state.",
        "allowed_decisions": ("approved", "rejected", "revise"),
        "requested_action": "review_clear",
        "exact_action": "review_clear",
        "action_fingerprint": "sha256:sandbox-review",
        "evidence_refs": ("fixture://sandbox-review",),
        "test_only": True,
        "non_live": True,
        "gate_id": "gate-1",
        "human_ref": "Suman(test)",
    }
    packet.update(overrides)
    return packet


class SandboxSurfaceConnectorsTest(unittest.TestCase):
    def test_sandbox_connectors_declare_fail_closed_write_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapters = (
                SandboxObsidianMarkdownSurfaceAdapter(Path(temp_dir) / "vault"),
                SandboxTelegramOutboxSurfaceAdapter(Path(temp_dir) / "outbox"),
            )

            for adapter in adapters:
                with self.subTest(adapter=adapter.adapter_id):
                    capabilities = adapter.capabilities()
                    contract = capabilities.metadata["surface_contract"]

                    self.assertIsInstance(adapter, SurfaceAdapter)
                    self.assertEqual(capabilities.family, AdapterFamily.SURFACE)
                    self.assertEqual(capabilities.metadata["mutation_mode"], "sandbox")
                    self.assertEqual(capabilities.metadata["write_class"], "sandbox")
                    self.assertTrue(capabilities.metadata["sandbox"])
                    self.assertFalse(capabilities.metadata["live"])
                    self.assertFalse(capabilities.metadata["live_mutation_allowed"])
                    self.assertFalse(capabilities.metadata["network_calls_allowed"])
                    self.assertTrue(capabilities.metadata["risk_policy"]["fail_closed_on_unknown_or_live"])
                    self.assertFalse(contract["live_mutation_allowed"])
                    self.assertTrue(contract["readback_required"])
                    self.assertTrue(contract["decision_ingest_supported"])

    def test_obsidian_publish_readback_idempotent_and_checked_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = SandboxObsidianMarkdownSurfaceAdapter(Path(temp_dir) / "vault")
            call = invocation(adapter.adapter_id)

            first = adapter.publish(call, review_packet(note_path="Inbox/review.md"))
            second = adapter.publish(call, review_packet(note_path="Inbox/review.md"))
            readback = adapter.readback(first.outputs["surface_ref"])
            note_path = Path(first.outputs["note_path"])
            note_path.write_text(
                note_path.read_text(encoding="utf-8").replace(
                    "- [ ] `approved`",
                    "- [x] `approved`",
                ),
                encoding="utf-8",
            )
            decisions = adapter.ingest_decisions(
                {
                    "query_id": "obsidian-decision-1",
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
        self.assertEqual(first.outputs["note_path"], second.outputs["note_path"])
        self.assertEqual(readback.status, "succeeded")
        self.assertTrue(readback.runtime_provenance["outputs"]["readback_confirmed"])
        self.assertEqual(decisions[0].status, "succeeded")
        outputs = decisions[0].runtime_provenance["outputs"]
        self.assertEqual(outputs["schema"], "sandbox_surface_decision.v1")
        self.assertEqual(outputs["canonical_surface"], "obsidian_sandbox_markdown")
        self.assertEqual(outputs["decision"], "approved")
        self.assertEqual(outputs["exact_action_approved"], "review_clear")
        self.assertEqual(outputs["action_fingerprint"], "sha256:sandbox-review")
        self.assertTrue(outputs["test_only"])
        self.assertTrue(outputs["non_live"])

    def test_obsidian_refuses_path_traversal_and_live_packet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = SandboxObsidianMarkdownSurfaceAdapter(Path(temp_dir) / "vault")

            traversal = adapter.publish(
                invocation(adapter.adapter_id),
                review_packet(note_path="../outside.md"),
            )
            live = adapter.publish(
                invocation(adapter.adapter_id),
                review_packet(non_live=False, live_mutation_requested=True),
            )

        self.assertEqual(traversal.status, "blocked")
        self.assertEqual(traversal.outputs["error"]["error_class"], "path_traversal_refused")
        self.assertEqual(live.status, "blocked")
        self.assertEqual(live.outputs["error"]["error_class"], "live_mutation_refused")

    def test_telegram_spools_readback_idempotent_and_ingests_injected_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = SandboxTelegramOutboxSurfaceAdapter(Path(temp_dir) / "outbox")
            call = invocation(adapter.adapter_id)

            first = adapter.publish(call, review_packet())
            second = adapter.publish(call, review_packet())
            readback = adapter.readback(first.outputs["surface_ref"])
            inject = adapter.inject_decision(
                first.outputs["surface_ref"],
                decision="approved",
            )
            decisions = adapter.ingest_decisions(
                {
                    "query_id": "telegram-decision-1",
                    "surface_ref": first.outputs["surface_ref"],
                    "allowed_decisions": ("approved", "rejected", "revise"),
                    "exact_action": "review_clear",
                    "expected_action_fingerprint": "sha256:sandbox-review",
                    "evidence_refs": ("fixture://sandbox-review",),
                    "human_ref": "Suman(test)",
                    "gate_id": "gate-1",
                }
            )
            message = json.loads(Path(first.outputs["message_path"]).read_text(encoding="utf-8"))

        self.assertEqual(first.status, "succeeded")
        self.assertEqual(second.status, "succeeded")
        self.assertTrue(second.outputs["idempotency_replayed"])
        self.assertFalse(first.outputs["network_call_performed"])
        self.assertFalse(message["network_call_performed"])
        self.assertEqual(readback.status, "succeeded")
        self.assertTrue(readback.runtime_provenance["outputs"]["readback_confirmed"])
        self.assertEqual(inject.status, "succeeded")
        self.assertEqual(decisions[0].status, "succeeded")
        outputs = decisions[0].runtime_provenance["outputs"]
        self.assertEqual(outputs["canonical_surface"], "telegram_sandbox_outbox")
        self.assertEqual(outputs["decision"], "approved")
        self.assertEqual(outputs["transcript_or_message_ref"], first.outputs["message_path"])
        self.assertFalse(outputs["network_call_performed"])

    def test_telegram_refuses_real_send_or_live_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            configured_live = SandboxTelegramOutboxSurfaceAdapter(
                Path(temp_dir) / "live-outbox",
                mutation_mode="live",
            )
            network_enabled = SandboxTelegramOutboxSurfaceAdapter(
                Path(temp_dir) / "network-outbox",
                allow_network_send=True,
            )
            normal = SandboxTelegramOutboxSurfaceAdapter(Path(temp_dir) / "outbox")

            live_config = configured_live.publish(invocation(configured_live.adapter_id), review_packet())
            network_config = network_enabled.publish(invocation(network_enabled.adapter_id), review_packet())
            send_now = normal.publish(
                invocation(normal.adapter_id),
                review_packet(send_now=True, telegram_bot_token="do-not-print"),
            )

        self.assertEqual(live_config.status, "blocked")
        self.assertEqual(live_config.outputs["error"]["error_class"], "unknown_mutation_mode")
        self.assertEqual(network_config.status, "blocked")
        self.assertEqual(network_config.outputs["error"]["error_class"], "live_mutation_refused")
        self.assertEqual(send_now.status, "blocked")
        self.assertEqual(send_now.outputs["error"]["error_class"], "live_mutation_refused")
        self.assertEqual(send_now.outputs["surface_packet"]["telegram_bot_token"], "<redacted>")

    def test_kernel_human_gate_lifecycle_uses_telegram_sandbox_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "kernel.sqlite3"
            ledger = WorkflowLedger(db_path)
            now = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
            runtime = LocalFakeRuntimeAdapter(created_at=now.isoformat())
            surface = SandboxTelegramOutboxSurfaceAdapter(
                Path(temp_dir) / "telegram-outbox",
                created_at=now.isoformat(),
            )
            registry = AdapterRegistry(
                (
                    AdapterRegistration.from_runtime_adapter(runtime),
                    AdapterRegistration.from_surface_adapter(surface),
                )
            )
            kernel = WorkflowKernel(
                ledger,
                self.human_gate_workflow(surface.adapter_id),
                KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
            )
            runner = WorkflowRunner(ledger, owner_id="runner-test")

            kernel.start(instance_id="instance-telegram", inputs={}, now=now)
            waiting = runner.run_kernel_until_idle(kernel, publish_human_gate=True, now=now)
            surface_ref = waiting.surface_results[0].surface_ref
            assert surface_ref is not None
            injected = surface.inject_decision(surface_ref, decision="approved")
            resumed = runner.run_kernel_until_idle(
                kernel,
                publish_human_gate=True,
                ingest_human_decision=True,
                now=now,
            )
            approve_run = ledger.get_stage_run("instance-telegram:approve:1")
            apply_run = ledger.get_stage_run("instance-telegram:apply:1")
            stored = ledger.get_workflow_instance("instance-telegram")
            ledger.close()

        self.assertEqual(waiting.status, "waiting_on_human")
        self.assertEqual([result.operation for result in waiting.surface_results], ["publish", "readback"])
        self.assertEqual(injected.status, "succeeded")
        self.assertEqual(resumed.status, "done")
        self.assertIsNotNone(approve_run)
        self.assertIsNotNone(apply_run)
        assert approve_run is not None
        assert apply_run is not None
        assert stored is not None
        self.assertEqual(approve_run.status, StageRunStatus.SUCCEEDED)
        self.assertEqual(apply_run.status, StageRunStatus.SUCCEEDED)
        self.assertEqual(stored.status, WorkflowStatus.DONE)

    def human_gate_workflow(self, surface_adapter_id: str) -> WorkflowDef:
        return WorkflowDef(
            id="sandbox-surface-human-gate",
            version="0.1.0",
            name="Sandbox surface human gate",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                ),
                StageDef(
                    id="approve",
                    type=StageType.HUMAN_GATE,
                    adapter=surface_adapter_id,
                    outcomes=("approved", "rejected", "revise"),
                    inputs={"decision_action": "review_clear"},
                    actors={"operator": "Suman(test)"},
                    surface={
                        "title": "Telegram sandbox review",
                        "human_ask": "Choose the next workflow state.",
                        "allowed_decisions": ("approved", "rejected", "revise"),
                        "evidence_refs": ("fixture://telegram-sandbox",),
                    },
                ),
                StageDef(
                    id="apply",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("applied",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                ),
            ),
            transitions=(
                Transition(from_stage="draft", on="done", to_stage="approve"),
                Transition(from_stage="approve", on="approved", to_stage="apply"),
                Transition(from_stage="approve", on="rejected", terminal="policy_denied"),
                Transition(from_stage="approve", on="revise", to_stage="draft"),
                Transition(from_stage="apply", on="applied", terminal="done"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
