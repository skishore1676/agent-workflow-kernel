import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterRegistration,
    AdapterRegistry,
    AdapterRegistryError,
    ApprovalDecision,
    HumanApprovalReceipt,
    KernelRuntimeConfig,
    LocalFakeRuntimeAdapter,
    LocalFakeSurfaceAdapter,
    LocalMarkdownHumanReviewSurfaceAdapter,
    PromptRef,
    PromptRegistry,
    Receipt,
    RiskClass,
    StageDef,
    StageRunStatus,
    StageType,
    Transition,
    WorkflowDef,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowStatus,
)


UTC = timezone.utc
PROMPT_REFS = (
    PromptRef(id="identity.portable_worker", kind="identity", version="1.0.0"),
    PromptRef(id="policy.no_external_effects", kind="policy", version="1.0.0", render_mode="yaml"),
    PromptRef(id="lane.quality_review", kind="lane", version="1.0.0"),
    PromptRef(id="stage.review", kind="stage", version="1.0.0"),
)


class WorkflowKernelRunOnceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "kernel.sqlite3"
        self.ledger = WorkflowLedger(self.db_path)
        self.now = datetime(2026, 5, 31, 13, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.ledger.close()
        self.tmpdir.cleanup()

    def test_start_queues_first_stage_and_records_workflow_event(self) -> None:
        kernel = self.kernel_for(self.workflow_with_runtime_stage())

        instance = kernel.start(
            instance_id="instance-1",
            inputs={"objective": "write a receipt-backed fixture"},
            now=self.now,
        )

        self.assertEqual(instance.status, WorkflowStatus.RUNNING)
        self.assertEqual(instance.current_stage_id, "draft")
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.current_stage_id, "draft")
        run = self.ledger.get_stage_run("instance-1:draft:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.QUEUED)
        self.assertEqual(run.adapter_id, "runtime.local_fake")
        events = self.ledger.list_events()
        self.assertEqual([event["event_type"] for event in events], ["workflow_started"])
        self.assertEqual(events[0]["payload"]["first_stage_id"], "draft")

    def test_run_once_invokes_readonly_adapter_and_records_receipt(self) -> None:
        kernel = self.kernel_for(self.workflow_with_runtime_stage())
        kernel.start(
            instance_id="instance-1",
            inputs={"objective": "write a receipt-backed fixture"},
            now=self.now,
        )

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "succeeded")
        self.assertIsNotNone(step.adapter_result)
        assert step.adapter_result is not None
        self.assertEqual(step.adapter_result.status, "succeeded")
        self.assertEqual(step.receipt_id, "receipt:" + step.adapter_result.invocation_id + ":succeeded")
        run = self.ledger.get_stage_run("instance-1:draft:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.SUCCEEDED)
        self.assertEqual(run.receipt_id, step.receipt_id)

        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE receipt_id = ?",
            (step.receipt_id,),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        self.assertEqual(receipt["stage_id"], "draft")
        self.assertEqual(receipt["runtime_provenance"]["adapter_id"], "runtime.local_fake")
        self.assertEqual(receipt["policy_snapshot"]["decision"], "allow_with_receipt")
        self.assertEqual(
            receipt["runtime_provenance"]["outputs"]["runtime_input"]["stage"]["id"],
            "draft",
        )

        invocation_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM adapter_invocations"
        ).fetchone()["count"]
        self.assertEqual(invocation_count, 1)
        events = [event["event_type"] for event in self.ledger.list_events()]
        self.assertEqual(
            events,
            [
                "workflow_started",
                "stage_claimed",
                "receipt_recorded",
                "stage_completed",
                "workflow_stage_succeeded",
            ],
        )

    def test_prompt_refs_render_context_and_record_prompt_provenance(self) -> None:
        kernel = self.kernel_for(
            self.workflow_with_runtime_stage(prompt_refs=PROMPT_REFS),
            prompt_registry_path=ROOT / "prompts",
        )
        kernel.start(
            instance_id="instance-1",
            inputs={"objective": "review a prompt-backed stage"},
            now=self.now,
        )

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "succeeded")
        self.assertIsNotNone(step.receipt_id)
        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE receipt_id = ?",
            (step.receipt_id,),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        context_ref = receipt["context_packet_ref"]
        self.assertIsNotNone(context_ref)
        self.assertEqual(receipt["prompt_provenance"]["context"]["packet_id"], context_ref)
        self.assertEqual(len(receipt["prompt_provenance"]["refs"]), 4)
        self.assertTrue(
            receipt["prompt_provenance"]["context"]["rendered_input_digest"].startswith("sha256:")
        )

        invocation_row = self.ledger.connection.execute(
            "SELECT context_packet_ref FROM adapter_invocations"
        ).fetchone()
        self.assertEqual(invocation_row["context_packet_ref"], context_ref)
        runtime_input = receipt["runtime_provenance"]["outputs"]["runtime_input"]
        self.assertEqual(runtime_input["context_packet"]["packet_id"], context_ref)
        self.assertEqual(runtime_input["context_packet"]["workflow"]["id"], "toy-kernel")
        self.assertIn("identity.portable_worker", runtime_input["rendered_input"])

    def test_prompt_hash_mismatch_blocks_before_adapter_invocation(self) -> None:
        bad_ref = PromptRef(
            id="stage.review",
            kind="stage",
            version="1.0.0",
            content_hash="sha256:" + "0" * 64,
        )
        workflow = self.workflow_with_runtime_stage(prompt_refs=(bad_ref,))
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        kernel = WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(
                owner_id="kernel-test",
                adapter_registry=registry,
                prompt_registry=PromptRegistry.load(ROOT / "prompts"),
            ),
        )
        kernel.start(instance_id="instance-1", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("Hash mismatch", step.failure_summary or "")
        self.assertEqual(adapter.receipts, [])
        invocation_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM adapter_invocations"
        ).fetchone()["count"]
        self.assertEqual(invocation_count, 0)
        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE stage_run_id = ?",
            ("instance-1:draft:1",),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        self.assertEqual(receipt["kind"], "kernel.prompt_context")
        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(receipt["prompt_provenance"]["error"]["class"], "invalid_output")

    def test_missing_adapter_blocks_without_invocation(self) -> None:
        workflow = self.workflow_with_runtime_stage(adapter="runtime.missing")
        kernel = self.kernel_for(workflow)
        kernel.start(instance_id="instance-1", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("missing adapter registration", step.failure_summary or "")
        run = self.ledger.get_stage_run("instance-1:draft:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.BLOCKED)
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.BLOCKED)
        invocation_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM adapter_invocations"
        ).fetchone()["count"]
        self.assertEqual(invocation_count, 0)

    def test_human_gate_waits_without_adapter_invocation(self) -> None:
        workflow = WorkflowDef(
            id="toy-human",
            version="0.1.0",
            name="Toy human workflow",
            stages=(
                StageDef(
                    id="approve",
                    type=StageType.HUMAN_GATE,
                    adapter="surface.local_fake",
                    outcomes=("approved", "rejected"),
                ),
            ),
            transitions=(),
        )
        kernel = self.kernel_for(workflow)
        kernel.start(instance_id="instance-1", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "waiting_on_human")
        self.assertIn("waiting for explicit decision", step.failure_summary or "")
        run = self.ledger.get_stage_run("instance-1:approve:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.WAITING_ON_HUMAN)
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.WAITING_ON_HUMAN)
        approval_required = self.ledger.connection.execute(
            "SELECT approval_required FROM stage_runs WHERE stage_run_id = ?",
            ("instance-1:approve:1",),
        ).fetchone()["approval_required"]
        self.assertEqual(approval_required, 1)
        events = [event["event_type"] for event in self.ledger.list_events()]
        self.assertIn("human_gate_waiting", events)

    def test_success_queues_next_stage_from_transition(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        kernel.start(instance_id="instance-1", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "succeeded")
        draft = self.ledger.get_stage_run("instance-1:draft:1")
        approve = self.ledger.get_stage_run("instance-1:approve:1")
        self.assertIsNotNone(draft)
        self.assertIsNotNone(approve)
        assert draft is not None
        assert approve is not None
        self.assertEqual(draft.status, StageRunStatus.SUCCEEDED)
        self.assertEqual(approve.status, StageRunStatus.QUEUED)
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.RUNNING)
        self.assertEqual(stored.current_stage_id, "approve")

    def test_approved_human_decision_resumes_to_next_stage(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-1")
        decision = self._decision_from_waiting_gate(
            approval_id="approval-1",
            decision=ApprovalDecision.APPROVED,
        )

        result = kernel.ingest_human_decision(
            instance_id="instance-1",
            decision=decision,
            now=self.now,
        )

        self.assertEqual(result.decision, "queued")
        self.assertEqual(result.outcome, "approval_granted")
        self.assertEqual(result.queued_stage_id, "apply")
        approve = self.ledger.get_stage_run("instance-1:approve:1")
        apply = self.ledger.get_stage_run("instance-1:apply:1")
        self.assertIsNotNone(approve)
        self.assertIsNotNone(apply)
        assert approve is not None
        assert apply is not None
        self.assertEqual(approve.status, StageRunStatus.SUCCEEDED)
        self.assertEqual(approve.receipt_id, "approval-1")
        self.assertEqual(apply.status, StageRunStatus.QUEUED)
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.RUNNING)
        self.assertEqual(stored.current_stage_id, "apply")
        decision_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM human_decisions"
        ).fetchone()["count"]
        self.assertEqual(decision_count, 1)

    def test_rejected_human_decision_does_not_queue_unsafe_next_stage(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-1")
        decision = self._decision_from_waiting_gate(
            approval_id="approval-reject",
            decision=ApprovalDecision.REJECTED,
        )

        result = kernel.ingest_human_decision(
            instance_id="instance-1",
            decision=decision,
            now=self.now,
        )

        self.assertEqual(result.decision, "terminal")
        self.assertEqual(result.outcome, "reject")
        self.assertEqual(result.terminal_status, WorkflowStatus.POLICY_DENIED)
        self.assertIsNone(self.ledger.get_stage_run("instance-1:apply:1"))
        stored = self.ledger.get_workflow_instance("instance-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.POLICY_DENIED)
        self.assertIsNone(stored.current_stage_id)

    def test_revise_human_decision_queues_configured_revision_path_only(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-1")
        decision = self._decision_from_waiting_gate(
            approval_id="approval-revise",
            decision=ApprovalDecision.REVISE,
        )

        result = kernel.ingest_human_decision(
            instance_id="instance-1",
            decision=decision,
            now=self.now,
        )

        self.assertEqual(result.decision, "queued")
        self.assertEqual(result.outcome, "revise_plan")
        self.assertEqual(result.queued_stage_id, "draft")
        draft_retry = self.ledger.get_stage_run("instance-1:draft:2")
        self.assertIsNotNone(draft_retry)
        assert draft_retry is not None
        self.assertEqual(draft_retry.status, StageRunStatus.QUEUED)
        self.assertIsNone(self.ledger.get_stage_run("instance-1:apply:1"))

    def test_missing_human_decision_blocks_waiting_gate(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-1")

        missing = kernel.ingest_human_decision(
            instance_id="instance-1",
            decision=None,
            now=self.now,
        )

        self.assertEqual(missing.decision, "blocked")
        self.assertIn("Missing human decision", missing.failure_summary or "")
        run = self.ledger.get_stage_run("instance-1:approve:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.BLOCKED)
        self.assertIsNone(self.ledger.get_stage_run("instance-1:apply:1"))

    def test_mismatched_human_decision_blocks_waiting_gate(self) -> None:
        kernel = self.kernel_for(self.workflow_with_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-1")
        mismatched = self._decision_from_waiting_gate(
            approval_id="approval-mismatch",
            decision=ApprovalDecision.APPROVED,
            action_fingerprint="wrong-fingerprint",
        )

        result = kernel.ingest_human_decision(
            instance_id="instance-1",
            decision=mismatched,
            now=self.now,
        )

        self.assertEqual(result.decision, "blocked")
        self.assertIn("fingerprint", result.failure_summary or "")
        run = self.ledger.get_stage_run("instance-1:approve:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.BLOCKED)
        self.assertIsNone(self.ledger.get_stage_run("instance-1:apply:1"))

    def test_non_readonly_registration_requires_human_before_invocation(self) -> None:
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry(
            (
                AdapterRegistration.from_runtime_adapter(
                    adapter,
                    side_effects=(RiskClass.EXTERNAL_EFFECT,),
                ),
            )
        )
        kernel = WorkflowKernel(
            self.ledger,
            self.workflow_with_runtime_stage(),
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-1", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("approval", step.failure_summary or "")
        self.assertEqual(adapter.receipts, [])
        invocation_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM adapter_invocations"
        ).fetchone()["count"]
        self.assertEqual(invocation_count, 0)

    def test_human_gate_surface_lifecycle_publishes_reads_back_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as notes_dir:
            surface = LocalMarkdownHumanReviewSurfaceAdapter(
                notes_dir,
                created_at=self.now.isoformat(),
            )
            kernel = self.kernel_for(
                self.workflow_with_surface_lifecycle_gate(surface.adapter_id),
                surface_adapter=surface,
            )
            self._run_to_waiting_gate(kernel, instance_id="instance-1")

            publish = kernel.publish_waiting_human_gate(
                instance_id="instance-1",
                test_only=True,
                non_live=True,
                now=self.now,
            )
            note_path = Path(publish.outputs["note_path"])
            readback = kernel.readback_human_gate_surface(
                instance_id="instance-1",
                now=self.now,
            )
            note_text = note_path.read_text(encoding="utf-8")
            note_path.write_text(
                note_text.replace("- [ ] `approved`", "- [x] `approved`"),
                encoding="utf-8",
            )

            ingest = kernel.ingest_human_gate_surface_decision(
                instance_id="instance-1",
                now=self.now,
            )

        self.assertEqual(publish.status, "succeeded")
        self.assertIsNotNone(publish.surface_ref)
        self.assertEqual(publish.outputs["workflow_id"], "toy-surface-resume")
        self.assertEqual(publish.outputs["instance_id"], "instance-1")
        self.assertEqual(publish.outputs["stage_id"], "approve")
        self.assertEqual(publish.outputs["stage_run_id"], "instance-1:approve:1")
        self.assertEqual(publish.outputs["gate_id"][:5], "gate-")
        self.assertEqual(publish.outputs["requested_action"], "surface_review_clear")
        self.assertIn("Gate ID: `gate-", note_text)
        self.assertIn("- Requested action: `surface_review_clear`", note_text)
        self.assertEqual(readback.status, "succeeded")
        self.assertTrue(readback.outputs["readback"]["exists"])
        self.assertEqual(ingest.status, "succeeded")
        self.assertIsNotNone(ingest.decision_result)
        assert ingest.decision_result is not None
        self.assertEqual(ingest.decision_result.decision, "queued")
        self.assertEqual(ingest.decision_result.outcome, "approved")
        self.assertEqual(ingest.decision_result.queued_stage_id, "apply")
        apply = self.ledger.get_stage_run("instance-1:apply:1")
        self.assertIsNotNone(apply)
        assert apply is not None
        self.assertEqual(apply.status, StageRunStatus.QUEUED)

    def test_surface_decision_ingest_blocks_invalid_markdown_decisions(self) -> None:
        cases = {
            "multiple_checked": lambda text: text.replace(
                "- [ ] `approved`",
                "- [x] `approved`",
            ).replace(
                "- [ ] `rejected`",
                "- [x] `rejected`",
            ),
            "unknown_checked": lambda text: text + "- [x] `ship_it_anyway`\n",
            "mismatched_fingerprint": lambda text: text.replace(
                "- Action fingerprint: `",
                "- Action fingerprint: `edited-",
            ),
        }

        for name, mutate in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as notes_dir:
                surface = LocalMarkdownHumanReviewSurfaceAdapter(
                    notes_dir,
                    created_at=self.now.isoformat(),
                )
                kernel = self.kernel_for(
                    self.workflow_with_surface_lifecycle_gate(surface.adapter_id),
                    surface_adapter=surface,
                )
                instance_id = f"instance-{name}"
                self._run_to_waiting_gate(kernel, instance_id=instance_id)
                publish = kernel.publish_waiting_human_gate(
                    instance_id=instance_id,
                    test_only=True,
                    non_live=True,
                    now=self.now,
                )
                note_path = Path(publish.outputs["note_path"])
                note_path.write_text(
                    mutate(note_path.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )

                ingest = kernel.ingest_human_gate_surface_decision(
                    instance_id=instance_id,
                    now=self.now,
                )

                self.assertEqual(ingest.status, "blocked")
                self.assertIsNotNone(ingest.decision_result)
                assert ingest.decision_result is not None
                self.assertEqual(ingest.decision_result.decision, "blocked")
                self.assertIsNone(self.ledger.get_stage_run(f"{instance_id}:apply:1"))
                approve = self.ledger.get_stage_run(f"{instance_id}:approve:1")
                self.assertIsNotNone(approve)
                assert approve is not None
                self.assertEqual(approve.status, StageRunStatus.BLOCKED)

    def test_generic_surface_adapter_can_publish_and_readback_waiting_gate(self) -> None:
        surface = LocalFakeSurfaceAdapter(created_at=self.now.isoformat())
        kernel = self.kernel_for(
            self.workflow_with_surface_lifecycle_gate(surface.adapter_id),
            surface_adapter=surface,
        )
        self._run_to_waiting_gate(kernel, instance_id="instance-generic")

        publish = kernel.publish_waiting_human_gate(
            instance_id="instance-generic",
            test_only=True,
            non_live=True,
            now=self.now,
        )
        readback = kernel.readback_human_gate_surface(
            instance_id="instance-generic",
            now=self.now,
        )

        self.assertEqual(publish.status, "succeeded")
        self.assertEqual(publish.outputs["surface_packet"]["gate_id"][:5], "gate-")
        self.assertEqual(
            publish.outputs["surface_packet"]["action_fingerprint"],
            publish.outputs["surface_packet"]["policy_gate"]["action_fingerprint"],
        )
        self.assertEqual(readback.status, "succeeded")
        self.assertEqual(
            readback.outputs["readback"]["packet"]["stage_run_id"],
            "instance-generic:approve:1",
        )

    def test_surface_adapter_without_structured_decision_blocks_resume(self) -> None:
        surface = LocalFakeSurfaceAdapter(created_at=self.now.isoformat())
        kernel = self.kernel_for(
            self.workflow_with_surface_lifecycle_gate(surface.adapter_id),
            surface_adapter=surface,
        )
        self._run_to_waiting_gate(kernel, instance_id="instance-generic")
        kernel.publish_waiting_human_gate(
            instance_id="instance-generic",
            test_only=True,
            non_live=True,
            now=self.now,
        )

        ingest = kernel.ingest_human_gate_surface_decision(
            instance_id="instance-generic",
            now=self.now,
        )

        self.assertEqual(ingest.status, "blocked")
        self.assertIn("exactly one", ingest.failure_summary or "")
        self.assertIsNone(self.ledger.get_stage_run("instance-generic:apply:1"))

    def test_surface_decision_receipt_mismatch_blocks_via_kernel_validation(self) -> None:
        class WrongGateSurfaceAdapter(LocalFakeSurfaceAdapter):
            adapter_id = "surface.wrong_gate"

            def ingest_decisions(self, surface_query):
                receipt = Receipt(
                    receipt_id="receipt:wrong-gate-decision",
                    kind="adapter.surface.ingest_decisions",
                    workflow_id=str(surface_query["workflow_id"]),
                    instance_id=str(surface_query["instance_id"]),
                    stage_id=str(surface_query["stage_id"]),
                    stage_run_id=str(surface_query["stage_run_id"]),
                    status="succeeded",
                    summary="Wrong gate decision fixture.",
                    created_at=self.created_at,
                    runtime_provenance={
                        "outputs": {
                            "gate_id": "gate-wrong",
                            "human_ref": "Suman(test)",
                            "canonical_surface": "local_fake",
                            "decision": "approved",
                            "requested_action": surface_query["requested_action"],
                            "exact_action_approved": surface_query["exact_action"],
                            "action_fingerprint": surface_query["expected_action_fingerprint"],
                            "evidence_refs": surface_query["evidence_refs"],
                            "test_only": True,
                            "non_live": True,
                        }
                    },
                )
                self.receipts.append(receipt)
                return [receipt]

        surface = WrongGateSurfaceAdapter(created_at=self.now.isoformat())
        kernel = self.kernel_for(
            self.workflow_with_surface_lifecycle_gate(surface.adapter_id),
            surface_adapter=surface,
        )
        self._run_to_waiting_gate(kernel, instance_id="instance-mismatch")
        kernel.publish_waiting_human_gate(
            instance_id="instance-mismatch",
            test_only=True,
            non_live=True,
            now=self.now,
        )

        ingest = kernel.ingest_human_gate_surface_decision(
            instance_id="instance-mismatch",
            now=self.now,
        )

        self.assertEqual(ingest.status, "blocked")
        self.assertIn("waiting gate", ingest.failure_summary or "")
        self.assertIsNone(self.ledger.get_stage_run("instance-mismatch:apply:1"))

    def test_missing_surface_adapter_fails_audibly(self) -> None:
        kernel = self.kernel_for(self.workflow_with_surface_lifecycle_gate("surface.missing"))
        self._run_to_waiting_gate(kernel, instance_id="instance-missing")

        with self.assertRaises(AdapterRegistryError):
            kernel.publish_waiting_human_gate(
                instance_id="instance-missing",
                test_only=True,
                non_live=True,
                now=self.now,
            )

    def kernel_for(
        self,
        workflow: WorkflowDef,
        *,
        prompt_registry: PromptRegistry | None = None,
        prompt_registry_path: Path | None = None,
        surface_adapter: object | None = None,
    ) -> WorkflowKernel:
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registrations = [AdapterRegistration.from_runtime_adapter(adapter)]
        if surface_adapter is not None:
            registrations.append(AdapterRegistration.from_surface_adapter(surface_adapter))
        registry = AdapterRegistry(tuple(registrations))
        return WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(
                owner_id="kernel-test",
                adapter_registry=registry,
                prompt_registry=prompt_registry,
                prompt_registry_path=prompt_registry_path,
            ),
        )

    def workflow_with_runtime_stage(
        self,
        *,
        adapter: str = "runtime.local_fake",
        prompt_refs: tuple[PromptRef, ...] = (),
    ) -> WorkflowDef:
        return WorkflowDef(
            id="toy-kernel",
            version="0.1.0",
            name="Toy kernel workflow",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter=adapter,
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                    prompt_refs=prompt_refs,
                ),
            ),
            transitions=(),
        )

    def workflow_with_human_gate(self) -> WorkflowDef:
        return WorkflowDef(
            id="toy-resume",
            version="0.1.0",
            name="Toy resumable workflow",
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
                    adapter="surface.local_fake",
                    outcomes=("approval_granted", "reject", "revise_plan"),
                    actors={"operator": "Suman"},
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
                Transition(from_stage="approve", on="approval_granted", to_stage="apply"),
                Transition(from_stage="approve", on="reject", terminal="policy_denied"),
                Transition(from_stage="approve", on="revise_plan", to_stage="draft"),
                Transition(from_stage="apply", on="applied", terminal="done"),
            ),
        )

    def workflow_with_surface_lifecycle_gate(self, surface_adapter: str) -> WorkflowDef:
        return WorkflowDef(
            id="toy-surface-resume",
            version="0.1.0",
            name="Toy surface resumable workflow",
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
                    adapter=surface_adapter,
                    outcomes=("approved", "rejected", "revise"),
                    inputs={"decision_action": "surface_review_clear"},
                    actors={"operator": "Suman(test)"},
                    surface={
                        "title": "Surface review packet",
                        "human_ask": "Choose the next workflow state.",
                        "allowed_decisions": ("approved", "rejected", "revise"),
                        "evidence_refs": ("fixture://surface-review",),
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

    def _run_to_waiting_gate(self, kernel: WorkflowKernel, *, instance_id: str) -> None:
        kernel.start(instance_id=instance_id, inputs={}, now=self.now)
        first = kernel.run_once(now=self.now)
        self.assertEqual(first.decision, "succeeded")
        gate = kernel.run_once(now=self.now)
        self.assertEqual(gate.decision, "waiting_on_human")

    def _decision_from_waiting_gate(
        self,
        *,
        approval_id: str,
        decision: ApprovalDecision,
        action_fingerprint: str | None = None,
    ) -> HumanApprovalReceipt:
        gate_event = next(
            event for event in self.ledger.list_events()
            if event["event_type"] == "human_gate_waiting"
        )
        payload = gate_event["payload"]
        return HumanApprovalReceipt(
            approval_id=approval_id,
            gate_id=payload["gate_id"],
            human_ref="Suman",
            canonical_surface="local_test_fixture",
            decision=decision,
            exact_action_approved=payload["requested_action"],
            action_fingerprint=action_fingerprint or payload["action_fingerprint"],
            evidence_refs=(f"event:{gate_event['event_id']}",),
            created_at=self.now,
            transcript_or_message_ref="local-test://human-decision",
        )


if __name__ == "__main__":
    unittest.main()
