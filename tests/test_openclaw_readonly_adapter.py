import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

from agent_workflow_kernel import AdapterFamily, AdapterInvocation, to_plain_data  # noqa: E402
from agent_workflow_kernel_openclaw import (  # noqa: E402
    OpenClawMutationBlocked,
    OpenClawReadOnlyAdapter,
    artifact_refs_from_fixture,
    guard_read_only_operation,
    mapping_from_fixture,
)


def fixture_data() -> dict[str, object]:
    return {
        "fixture_id": "openclaw-fixture-1",
        "created_at": "2026-05-31T00:00:00Z",
        "invocation": {
            "invocation_id": "invoke-openclaw-readonly-1",
            "workflow_id": "workflow-openclaw-parity",
            "instance_id": "instance-openclaw-parity",
            "stage_run_id": "stage-run-openclaw-parity",
            "adapter_family": "host",
            "adapter_id": "openclaw.readonly",
            "operation": "inspect_fixture",
            "input_ref": "fixture:openclaw-fixture-1",
            "context_packet_ref": "context:openclaw-fixture-1",
            "idempotency_key": "openclaw-fixture-1",
        },
        "mapping": {
            "lane_id": "or-research",
            "agent_id": "or_research",
            "host_ref": "host:openclaw-reference",
            "work_ledger": {
                "work_item_id": "wl-item-123",
                "handoff_id": "handoff-456",
                "receipt_ids": ["wl-receipt-1", "wl-receipt-2"],
                "interaction_id": "interaction-789",
                "turn_id": "turn-001",
            },
            "surface_refs": [
                {
                    "surface_id": "surface:review-note-1",
                    "kind": "review_note",
                    "external_id": "review-note-1",
                    "title": "Read-only review packet",
                    "readback_required": True,
                    "status": "observed",
                }
            ],
            "runtime_refs": [
                {
                    "runtime_id": "runtime:agent-session-1",
                    "kind": "agent_session",
                    "external_id": "session-key-redacted",
                    "host_ref": "host:openclaw-reference",
                    "redacted_locator": "openclaw-session:session-key-redacted",
                    "status": "completed",
                }
            ],
        },
        "artifacts": [
            {
                "artifact_id": "artifact:parity-packet",
                "role": "parity_fixture",
                "uri": "fixture://openclaw/parity-packet.md",
                "content_hash": "sha256:fixture-packet",
                "mime_type": "text/markdown",
                "created_by": "openclaw.readonly",
            }
        ],
        "residual_risk": "Fixture data can drift from live OpenClaw.",
        "next_action": "Compare against a future dual-run receipt before replacing any path.",
    }


class OpenClawReadOnlyAdapterTest(unittest.TestCase):
    def test_mapping_carries_reference_host_ids_without_resolution(self) -> None:
        mapping = mapping_from_fixture(fixture_data())

        self.assertEqual(mapping.lane_id, "or-research")
        self.assertEqual(mapping.agent_id, "or_research")
        self.assertEqual(mapping.work_ledger_ids.work_item_id, "wl-item-123")
        self.assertEqual(mapping.work_ledger_ids.receipt_ids, ("wl-receipt-1", "wl-receipt-2"))
        self.assertEqual(mapping.surface_refs[0].surface_id, "surface:review-note-1")
        self.assertEqual(mapping.runtime_refs[0].host_ref, "host:openclaw-reference")
        self.assertNotIn("/Users/sunny", str(mapping.to_metadata()))

    def test_adapter_converts_fixture_to_kernel_result_and_receipt(self) -> None:
        adapter = OpenClawReadOnlyAdapter()

        inspection = adapter.inspect_fixture(fixture_data())

        self.assertEqual(inspection.invocation.adapter_family, AdapterFamily.HOST)
        self.assertEqual(inspection.result.status, "succeeded")
        self.assertEqual(inspection.receipt.status, "succeeded")
        self.assertEqual(inspection.result.receipt_ref, inspection.receipt.receipt_id)
        self.assertEqual(inspection.receipt.context_packet_ref, "context:openclaw-fixture-1")
        self.assertEqual(inspection.artifact_refs[0].role, "parity_fixture")
        self.assertEqual(
            inspection.receipt.runtime_provenance["outputs"]["mapping"]["work_ledger_ids"]["handoff_id"],
            "handoff-456",
        )
        self.assertEqual(inspection.receipt.policy_snapshot["risk_class"], "read_only")
        self.assertEqual(adapter.receipts, [inspection.receipt])

    def test_artifact_conversion_hashes_when_fixture_omits_hash(self) -> None:
        refs = artifact_refs_from_fixture(
            [
                {
                    "artifact_id": "artifact:auto-hash",
                    "role": "parity_fixture",
                    "uri": "fixture://openclaw/auto-hash.json",
                }
            ]
        )

        self.assertTrue(refs[0].content_hash.startswith("sha256:"))
        self.assertEqual(refs[0].visibility, "internal")

    def test_mutating_operations_are_blocked_before_any_fixture_conversion(self) -> None:
        adapter = OpenClawReadOnlyAdapter()
        data = fixture_data()
        data["invocation"] = dict(data["invocation"])  # type: ignore[index]
        data["invocation"]["operation"] = "publish"  # type: ignore[index]

        with self.assertRaises(OpenClawMutationBlocked):
            adapter.inspect_fixture(data)

        self.assertEqual(adapter.receipts, [])

    def test_mutation_guard_is_explicit_and_structured_block_is_available(self) -> None:
        with self.assertRaises(OpenClawMutationBlocked):
            guard_read_only_operation("send_telegram_handoff")

        adapter = OpenClawReadOnlyAdapter()
        invocation = AdapterInvocation(
            invocation_id="invoke-mutation",
            workflow_id="workflow-openclaw-parity",
            instance_id="instance-openclaw-parity",
            stage_run_id="stage-run-openclaw-parity",
            adapter_family=AdapterFamily.SURFACE,
            adapter_id=adapter.adapter_id,
            operation="send_telegram_handoff",
        )
        result = adapter.blocked_mutation_result(invocation)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.outputs["read_only"], True)
        self.assertEqual(adapter.receipts[0].status, "blocked")

    def test_kernel_package_does_not_export_openclaw_adapter(self) -> None:
        import agent_workflow_kernel

        exported = set(agent_workflow_kernel.__all__)

        self.assertNotIn("OpenClawReadOnlyAdapter", exported)
        self.assertFalse(any(name.lower().startswith("openclaw") for name in exported))
        self.assertEqual(to_plain_data(OpenClawReadOnlyAdapter().capabilities())["features"][2], "read_only")


if __name__ == "__main__":
    unittest.main()
