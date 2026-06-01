import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterInvocation,
    AdapterResult,
    ArtifactRef,
    DryRunObsidianSurfaceAdapter,
    DryRunSheetsSurfaceAdapter,
    DryRunTelegramSurfaceAdapter,
    LocalFakeHostAdapter,
    LocalFakeLaneAdapter,
    LocalFakeRuntimeAdapter,
    LocalFakeSurfaceAdapter,
    LocalMarkdownHumanReviewSurfaceAdapter,
    Receipt,
    RuntimeAdapter,
    StageRun,
    StageRunStatus,
    SurfaceAdapter,
    SurfaceCapabilityContract,
    to_plain_data,
)


def invocation(
    family: AdapterFamily,
    adapter_id: str,
    operation: str,
) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"invoke-{family.value}-{operation}",
        workflow_id="workflow-1",
        instance_id="instance-1",
        stage_run_id="run-1",
        adapter_family=family,
        adapter_id=adapter_id,
        operation=operation,
        input_ref="input:1",
        context_packet_ref="context:1",
        idempotency_key="idempotency-1",
    )


class AdapterSpiLocalTest(unittest.TestCase):
    def test_runtime_adapter_invokes_and_records_receipt(self) -> None:
        adapter = LocalFakeRuntimeAdapter()
        call = invocation(AdapterFamily.RUNTIME, adapter.adapter_id, "invoke")

        result = adapter.invoke(call, {"objective": "draft a fixture"})

        self.assertIsInstance(adapter, RuntimeAdapter)
        self.assertIsInstance(result, AdapterResult)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.receipt_ref, "receipt:invoke-runtime-invoke:succeeded")
        self.assertEqual(result.outputs["runtime_input"]["objective"], "draft a fixture")
        self.assertEqual(adapter.receipts[0].context_packet_ref, "context:1")
        self.assertEqual(
            adapter.receipts[0].runtime_provenance["adapter_family"],
            "runtime",
        )

    def test_surface_adapter_publishes_and_reads_back(self) -> None:
        adapter = LocalFakeSurfaceAdapter()
        call = invocation(AdapterFamily.SURFACE, adapter.adapter_id, "publish")

        result = adapter.publish(
            call,
            {
                "title": "Review packet",
                "allowed_decisions": ("approve", "reject"),
                "readback_required": True,
            },
        )
        readback = adapter.readback(result.outputs["surface_ref"])

        self.assertIsInstance(adapter, SurfaceAdapter)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.outputs["surface_ref"]["readback_required"], True)
        self.assertIsInstance(readback, Receipt)
        self.assertEqual(readback.status, "succeeded")
        self.assertEqual(
            readback.runtime_provenance["outputs"]["packet"]["title"],
            "Review packet",
        )

    def test_dry_run_surface_adapters_declare_non_live_contracts(self) -> None:
        adapters = (
            DryRunObsidianSurfaceAdapter(),
            DryRunTelegramSurfaceAdapter(),
            DryRunSheetsSurfaceAdapter(),
        )

        for adapter in adapters:
            with self.subTest(adapter=adapter.adapter_id):
                capabilities = adapter.capabilities()
                contract = capabilities.metadata["surface_contract"]
                declared_contract = SurfaceCapabilityContract(
                    surface_kind=contract["surface_kind"],
                    mode=contract["mode"],
                    live_mutation_allowed=contract["live_mutation_allowed"],
                    dry_run_only=contract["dry_run_only"],
                    readback_required=contract["readback_required"],
                    decision_ingest_supported=contract["decision_ingest_supported"],
                    clear_requires_live_mutation=contract["clear_requires_live_mutation"],
                    external_effects=tuple(contract["external_effects"]),
                    receipt_schema=contract["receipt_schema"],
                )

                self.assertIsInstance(adapter, SurfaceAdapter)
                self.assertEqual(declared_contract.as_metadata()["mode"], "dry_run")
                self.assertEqual(capabilities.family, AdapterFamily.SURFACE)
                self.assertIn("publish", capabilities.operations)
                self.assertTrue(contract["dry_run_only"])
                self.assertFalse(contract["live_mutation_allowed"])
                self.assertTrue(contract["readback_required"])
                self.assertTrue(contract["decision_ingest_supported"])
                self.assertTrue(capabilities.metadata["non_live_only"])

    def test_dry_run_surface_publish_readback_and_decision_receipts(self) -> None:
        adapter = DryRunTelegramSurfaceAdapter()
        publish = adapter.publish(
            invocation(AdapterFamily.SURFACE, adapter.adapter_id, "publish"),
            {
                "title": "Owner brief",
                "allowed_decisions": ("approved", "rejected"),
                "exact_action": "continue_internal_work",
                "action_fingerprint": "sha256:dry-run-action",
                "test_only": True,
                "non_live": True,
            },
        )
        readback = adapter.readback(publish.outputs["surface_ref"])
        decisions = adapter.ingest_decisions(
            {
                "query_id": "decision-1",
                "gate_id": "gate-1",
                "human_ref": "Suman(test)",
                "decision": "approved",
                "allowed_decisions": ("approved", "rejected"),
                "requested_action": "continue_internal_work",
                "exact_action": "continue_internal_work",
                "expected_action_fingerprint": "sha256:dry-run-action",
                "evidence_refs": ("fixture://owner-brief",),
                "test_only": True,
                "non_live": True,
            }
        )

        self.assertEqual(publish.status, "succeeded")
        self.assertTrue(publish.outputs["dry_run"])
        self.assertTrue(publish.outputs["test_only"])
        self.assertTrue(publish.outputs["non_live"])
        self.assertFalse(publish.outputs["live_mutation_performed"])
        self.assertEqual(publish.outputs["surface_ref"]["kind"], "telegram_message")
        self.assertEqual(readback.status, "succeeded")
        self.assertTrue(readback.runtime_provenance["outputs"]["readback_confirmed"])
        self.assertEqual(len(decisions), 1)
        decision_outputs = decisions[0].runtime_provenance["outputs"]
        self.assertEqual(decisions[0].status, "succeeded")
        self.assertEqual(decision_outputs["schema"], "dry_run_surface_decision.v1")
        self.assertEqual(decision_outputs["canonical_surface"], adapter.adapter_id)
        self.assertEqual(decision_outputs["decision"], "approved")
        self.assertTrue(decision_outputs["test_only"])
        self.assertTrue(decision_outputs["non_live"])
        self.assertFalse(decision_outputs["live_mutation_performed"])

    def test_dry_run_surface_blocks_live_mutation_requests(self) -> None:
        adapter = DryRunSheetsSurfaceAdapter()

        publish = adapter.publish(
            invocation(AdapterFamily.SURFACE, adapter.adapter_id, "publish"),
            {
                "title": "Sheet row update",
                "test_only": False,
                "non_live": False,
                "live_mutation_requested": True,
            },
        )
        clear = adapter.clear(
            {"surface_id": "surface:sheet-row", "kind": "sheet_range"},
            "remove stale row",
        )
        decision = adapter.ingest_decisions(
            {
                "query_id": "live-read",
                "decision": "approved",
                "allowed_decisions": ("approved",),
                "test_only": True,
                "non_live": True,
                "read_live_surface": True,
            }
        )[0]

        self.assertEqual(publish.status, "blocked")
        self.assertEqual(publish.outputs["error"]["error_class"], "live_mutation_refused")
        self.assertFalse(publish.outputs["live_mutation_performed"])
        self.assertEqual(clear.status, "blocked")
        self.assertEqual(
            clear.runtime_provenance["outputs"]["error"]["error_class"],
            "live_mutation_refused",
        )
        self.assertEqual(decision.status, "blocked")
        self.assertEqual(
            decision.runtime_provenance["outputs"]["error"]["error_class"],
            "live_mutation_refused",
        )

    def test_local_markdown_human_review_publishes_card_and_reads_back(self) -> None:
        with TemporaryDirectory() as temp_dir:
            adapter = LocalMarkdownHumanReviewSurfaceAdapter(temp_dir)
            call = invocation(AdapterFamily.SURFACE, adapter.adapter_id, "publish")

            result = adapter.publish(call, self._review_packet())
            note_path = Path(result.outputs["note_path"])
            readback = adapter.readback(result.outputs["surface_ref"])
            note_text = note_path.read_text(encoding="utf-8")
            contract = adapter.capabilities().metadata["surface_contract"]

        self.assertIsInstance(adapter, SurfaceAdapter)
        self.assertEqual(contract["mode"], "local_artifact")
        self.assertFalse(contract["live_mutation_allowed"])
        self.assertTrue(contract["readback_required"])
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(readback.status, "succeeded")
        self.assertTrue(result.outputs["non_live"])
        self.assertIn("TEST ONLY - NON-LIVE LOCAL REVIEW PACKET", note_text)
        self.assertIn("- Workflow ID: `workflow-1`", note_text)
        self.assertIn("- Instance ID: `instance-1`", note_text)
        self.assertIn("- Stage ID: `stage-1`", note_text)
        self.assertIn("- Stage Run ID: `run-1`", note_text)
        self.assertIn("- Exact action: `weekly_read_clear`", note_text)
        self.assertIn("- Action fingerprint: `sha256:review-action`", note_text)
        self.assertIn("- `fixture://weekly-card`", note_text)
        self.assertIn("- [ ] `read_clear`", note_text)

    def test_local_markdown_human_review_blocks_live_surface_packet(self) -> None:
        with TemporaryDirectory() as temp_dir:
            adapter = LocalMarkdownHumanReviewSurfaceAdapter(temp_dir)
            packet = self._review_packet()
            packet["non_live"] = False

            result = adapter.publish(
                invocation(AdapterFamily.SURFACE, adapter.adapter_id, "publish"),
                packet,
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.outputs["error"]["error_class"], "live_mutation_refused")
        self.assertFalse(result.outputs["non_live"])

    def test_local_markdown_human_review_ingests_one_checked_decision(self) -> None:
        with TemporaryDirectory() as temp_dir:
            adapter = LocalMarkdownHumanReviewSurfaceAdapter(temp_dir)
            result = adapter.publish(
                invocation(AdapterFamily.SURFACE, adapter.adapter_id, "publish"),
                self._review_packet(),
            )
            note_path = Path(result.outputs["note_path"])
            note_path.write_text(
                note_path.read_text(encoding="utf-8").replace(
                    "- [ ] `read_clear`",
                    "- [x] `read_clear`",
                ),
                encoding="utf-8",
            )

            receipts = adapter.ingest_decisions(self._decision_query(result))

        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        outputs = receipt.runtime_provenance["outputs"]
        self.assertEqual(receipt.status, "succeeded")
        self.assertEqual(outputs["schema"], "local_human_review_decision.v1")
        self.assertEqual(outputs["canonical_surface"], "local_markdown_human_review")
        self.assertEqual(outputs["human_ref"], "Suman(test)")
        self.assertEqual(outputs["decision"], "read_clear")
        self.assertEqual(outputs["exact_action_approved"], "weekly_read_clear")
        self.assertEqual(outputs["action_fingerprint"], "sha256:review-action")
        self.assertEqual(outputs["evidence_refs"], ["fixture://weekly-card"])
        self.assertEqual(outputs["source_note_path"], str(note_path))
        self.assertTrue(outputs["test_only"])
        self.assertTrue(outputs["non_live"])

    def test_local_markdown_human_review_blocks_ambiguous_or_invalid_decisions(self) -> None:
        cases = {
            "multiple_checked": lambda text: text.replace(
                "- [ ] `read_clear`",
                "- [x] `read_clear`",
            ).replace(
                "- [ ] `defer`",
                "- [x] `defer`",
            ),
            "unknown_checked": lambda text: text + "- [x] `ship_it_anyway`\n",
            "missing_fingerprint": lambda text: text.replace(
                "- Action fingerprint: `sha256:review-action`\n",
                "",
            ),
            "mismatched_fingerprint": lambda text: text.replace(
                "- Action fingerprint: `sha256:review-action`",
                "- Action fingerprint: `sha256:edited-action`",
            ),
        }
        expected_errors = {
            "multiple_checked": "ambiguous_decision_count",
            "unknown_checked": "unknown_checked_decision",
            "missing_fingerprint": "missing_action_fingerprint",
            "mismatched_fingerprint": "action_fingerprint_mismatch",
        }

        for name, mutate in cases.items():
            with self.subTest(name=name), TemporaryDirectory() as temp_dir:
                adapter = LocalMarkdownHumanReviewSurfaceAdapter(temp_dir)
                result = adapter.publish(
                    invocation(AdapterFamily.SURFACE, adapter.adapter_id, "publish"),
                    self._review_packet(),
                )
                note_path = Path(result.outputs["note_path"])
                note_path.write_text(mutate(note_path.read_text(encoding="utf-8")), encoding="utf-8")
                query = (
                    {"surface_ref": result.outputs["surface_ref"]}
                    if name == "unknown_checked"
                    else self._decision_query(result)
                )

                receipt = adapter.ingest_decisions(query)[0]
                outputs = receipt.runtime_provenance["outputs"]

            self.assertEqual(receipt.status, "blocked")
            self.assertEqual(outputs["error"]["error_class"], expected_errors[name])
            self.assertEqual(outputs["source_note_path"], str(note_path))

    def test_host_adapter_describes_generic_local_host_and_receipts(self) -> None:
        adapter = LocalFakeHostAdapter()

        descriptor = adapter.describe()
        lease = adapter.acquire_lease("lease-key", 60)
        health = adapter.healthcheck("runner")

        self.assertEqual(descriptor.host_kind, "local")
        self.assertEqual(descriptor.capability_set.family, AdapterFamily.HOST)
        self.assertEqual(lease.status, "succeeded")
        self.assertEqual(
            lease.runtime_provenance["outputs"]["lease_id"],
            "lease:lease-key",
        )
        self.assertEqual(health.runtime_provenance["outputs"]["healthy"], True)

    def test_lane_adapter_translates_domain_payload_without_domain_assumptions(self) -> None:
        adapter = LocalFakeLaneAdapter()
        stage_run = StageRun(
            stage_run_id="run-1",
            instance_id="instance-1",
            stage_id="stage-1",
            status=StageRunStatus.STARTED,
        )
        artifact = ArtifactRef(
            artifact_id="artifact-1",
            role="draft",
            uri="artifact:local-draft",
            content_hash="sha256:test",
        )

        seed = adapter.open_work({"idempotency_key": "work-1", "payload": "value"})
        runtime_input = adapter.build_stage_input(stage_run, {"payload": "value"})
        receipt = adapter.validate_artifacts(stage_run, (artifact,))
        packet = adapter.prepare_human_gate(stage_run, {"title": "Gate"})

        self.assertEqual(seed["idempotency_key"], "work-1")
        self.assertEqual(runtime_input["stage_id"], "stage-1")
        self.assertEqual(receipt.artifact_refs, (artifact,))
        self.assertEqual(receipt.stage_id, "stage-1")
        self.assertEqual(packet["allowed_decisions"], ("approve", "reject"))

    def test_unsupported_operation_returns_structured_failure(self) -> None:
        adapter = LocalFakeRuntimeAdapter()
        call = invocation(AdapterFamily.RUNTIME, adapter.adapter_id, "teleport")

        result = adapter.invoke(call, {})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.next_hint, "choose a supported adapter operation")
        self.assertEqual(result.outputs["error"]["error_class"], "missing_capability")
        self.assertIn("invoke", result.outputs["supported_operations"])

    def test_adapter_contracts_serialize_to_plain_data(self) -> None:
        adapter = LocalFakeHostAdapter()

        data = to_plain_data(adapter.describe())

        self.assertEqual(data["capability_set"]["family"], "host")
        self.assertIn("healthcheck", data["capability_set"]["operations"])

    def _review_packet(self) -> dict[str, object]:
        return {
            "title": "Weekly review packet",
            "stage_id": "stage-1",
            "human_ask": "Mark the weekly update state.",
            "allowed_decisions": ("read_clear", "follow_up_requested", "defer"),
            "exact_action": "weekly_read_clear",
            "action_fingerprint": "sha256:review-action",
            "evidence_refs": ("fixture://weekly-card",),
            "test_only": True,
            "human_ref": "Suman(test)",
        }

    def _decision_query(self, publish_result: AdapterResult) -> dict[str, object]:
        return {
            "surface_ref": publish_result.outputs["surface_ref"],
            "allowed_decisions": publish_result.outputs["allowed_decisions"],
            "exact_action": publish_result.outputs["exact_action"],
            "expected_action_fingerprint": publish_result.outputs["action_fingerprint"],
            "evidence_refs": publish_result.outputs["evidence_refs"],
            "human_ref": publish_result.outputs["human_ref"],
        }


if __name__ == "__main__":
    unittest.main()
