import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterResult,
    AdapterRegistration,
    AdapterRegistry,
    AdapterRegistryError,
    ArtifactRef,
    ApprovalDecision,
    FailureClass,
    HumanApprovalReceipt,
    KernelRuntimeConfig,
    LocalFakeLaneAdapter,
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


class OutcomeSequenceRuntimeAdapter(LocalFakeRuntimeAdapter):
    def __init__(
        self,
        *,
        outcomes_by_stage: dict[str, tuple[str, ...]],
        created_at: str,
    ) -> None:
        super().__init__(created_at=created_at)
        self._outcomes_by_stage = outcomes_by_stage
        self._counts_by_stage: dict[str, int] = {}

    def invoke(self, invocation, runtime_input):
        result = super().invoke(invocation, runtime_input)
        stage_id = str(runtime_input["stage"]["id"])
        count = self._counts_by_stage.get(stage_id, 0)
        self._counts_by_stage[stage_id] = count + 1
        sequence = self._outcomes_by_stage.get(stage_id, ())
        if not sequence:
            return result
        outcome = sequence[min(count, len(sequence) - 1)]
        return replace(result, outputs={**result.outputs, "outcome": outcome})


class UsageReportingRuntimeAdapter(LocalFakeRuntimeAdapter):
    def invoke(self, invocation, runtime_input):
        result = super().invoke(invocation, runtime_input)
        return replace(
            result,
            outputs={
                **result.outputs,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "total_tokens": 125,
                    "cached_input_tokens": 10,
                    "session_id": "session-bound-1",
                    "source": "fixture",
                },
            },
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
                "stage_started",
                "adapter_invocation_preflight",
                "adapter_invocation_completed",
                "receipt_recorded",
                "stage_completed",
                "workflow_stage_succeeded",
            ],
        )

    def test_run_once_invokes_registered_lane_system_action(self) -> None:
        lane = LocalFakeLaneAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_lane_adapter(lane),))
        workflow = WorkflowDef(
            id="toy-lane-system-action",
            version="0.1.0",
            name="Toy lane system action",
            stages=(
                StageDef(
                    id="validate",
                    type=StageType.SYSTEM_ACTION,
                    adapter="lane.local_fake",
                    outcomes=("valid", "blocked"),
                    inputs={"source": "fixture"},
                ),
            ),
            transitions=(Transition(from_stage="validate", on="valid", terminal="done"),),
        )
        kernel = WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-lane-system-action", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "succeeded")
        stored = self.ledger.get_workflow_instance("instance-lane-system-action")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.DONE)
        self.assertEqual(stored.current_stage_id, None)
        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE stage_run_id = ?",
            ("instance-lane-system-action:validate:1",),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        self.assertEqual(receipt["runtime_provenance"]["adapter_family"], "lane")
        self.assertEqual(receipt["runtime_provenance"]["adapter_id"], "lane.local_fake")
        self.assertEqual(receipt["runtime_provenance"]["operation"], "build_stage_input")
        self.assertEqual(receipt["runtime_provenance"]["outputs"]["outcome"], "valid")
        self.assertEqual(
            receipt["runtime_provenance"]["outputs"]["stage_id"],
            "validate",
        )

    def test_adapter_reported_usage_is_captured_in_receipt_provenance(self) -> None:
        adapter = UsageReportingRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        kernel = WorkflowKernel(
            self.ledger,
            self.workflow_with_runtime_stage(),
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-usage", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "succeeded")
        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE stage_run_id = ?",
            ("instance-usage:draft:1",),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        self.assertEqual(receipt["runtime_provenance"]["usage"]["input_tokens"], 100)
        self.assertEqual(receipt["runtime_provenance"]["usage"]["output_tokens"], 25)
        self.assertEqual(receipt["runtime_provenance"]["usage"]["total_tokens"], 125)
        self.assertEqual(receipt["runtime_provenance"]["usage"]["session_id"], "session-bound-1")

    def test_stage_actor_lease_precedence_and_receipt_visibility(self) -> None:
        cases = (
            (
                "explicit",
                self.workflow_with_lease_policy(
                    default_seconds=60,
                    actor_seconds=90,
                    stage_seconds=120,
                ),
                15,
                15,
                "runner_override",
                "WorkflowKernel.run_once.lease_seconds",
            ),
            (
                "stage",
                self.workflow_with_lease_policy(
                    default_seconds=60,
                    actor_seconds=90,
                    stage_seconds=120,
                ),
                None,
                120,
                "stage",
                "stages.draft.lease",
            ),
            (
                "actor",
                self.workflow_with_lease_policy(default_seconds=60, actor_seconds=90),
                None,
                90,
                "actor",
                "actors.worker.lease",
            ),
            (
                "workflow-default",
                self.workflow_with_lease_policy(default_seconds=60),
                None,
                60,
                "workflow_default",
                "defaults.lease",
            ),
        )

        for instance_suffix, workflow, override, seconds, source, source_ref in cases:
            instance_id = f"lease-{instance_suffix}"
            kernel = self.kernel_for(workflow)
            kernel.start(instance_id=instance_id, inputs={}, now=self.now)

            step = kernel.run_once(
                instance_id=instance_id,
                lease_seconds=override,
                now=self.now,
            )

            self.assertEqual(step.decision, "succeeded")
            self.assertIsNotNone(step.stage_run)
            assert step.stage_run is not None
            self.assertEqual(step.stage_run.lease_seconds, seconds)
            self.assertEqual(step.stage_run.lease_source, source)
            run = self.ledger.get_stage_run(f"{instance_id}:draft:1")
            self.assertIsNotNone(run)
            assert run is not None
            self.assertEqual(run.lease_seconds, seconds)
            self.assertEqual(run.lease_source, source)
            self.assertEqual(run.lease_source_ref, source_ref)
            receipt_row = self.ledger.connection.execute(
                "SELECT receipt_json FROM receipts WHERE receipt_id = ?",
                (step.receipt_id,),
            ).fetchone()
            self.assertIsNotNone(receipt_row)
            receipt = json.loads(receipt_row["receipt_json"])
            self.assertEqual(receipt["runtime_provenance"]["lease"]["lease_seconds"], seconds)
            self.assertEqual(receipt["runtime_provenance"]["lease"]["source"], source)
            self.assertEqual(receipt["runtime_provenance"]["lease"]["source_ref"], source_ref)
            claim_event = next(
                event for event in self.ledger.list_events(stage_run_id=run.stage_run_id)
                if event["event_type"] == "stage_claimed"
            )
            self.assertEqual(claim_event["payload"]["lease_seconds"], seconds)
            self.assertEqual(claim_event["payload"]["lease_source"], source)
            audit = self.ledger.export_stage_run_audit(stage_run_id=run.stage_run_id)
            self.assertIsNotNone(audit)
            assert audit is not None
            self.assertEqual(audit["stage_run"]["lease_seconds"], seconds)
            self.assertEqual(audit["stage_run"]["lease_source"], source)

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
        self.assertTrue(receipt["prompt_provenance"]["prompt_bundle_digest"].startswith("sha256:"))
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

        run_row = self.ledger.connection.execute(
            """
            SELECT prompt_hash, context_packet_ref, context_packet_hash, rendered_context_hash
            FROM stage_runs
            WHERE stage_run_id = ?
            """,
            ("instance-1:draft:1",),
        ).fetchone()
        self.assertEqual(run_row["prompt_hash"], receipt["prompt_provenance"]["prompt_bundle_digest"])
        self.assertEqual(run_row["context_packet_ref"], context_ref)
        self.assertEqual(run_row["context_packet_hash"], receipt["prompt_provenance"]["context"]["packet_digest"])
        self.assertEqual(
            run_row["rendered_context_hash"],
            receipt["prompt_provenance"]["context"]["rendered_input_digest"],
        )

        audit = self.ledger.export_stage_run_audit(stage_run_id="instance-1:draft:1")
        self.assertIsNotNone(audit)
        assert audit is not None
        self.assertEqual(audit["workflow"]["id"], "toy-kernel")
        self.assertEqual(audit["instance"]["instance_id"], "instance-1")
        self.assertEqual(audit["stage_run"]["stage_run_id"], "instance-1:draft:1")
        self.assertEqual(audit["provenance"]["prompt_hash"], run_row["prompt_hash"])
        self.assertEqual(audit["provenance"]["context_packet_ref"], context_ref)
        self.assertEqual(audit["receipts"][0]["prompt_hash"], run_row["prompt_hash"])
        self.assertEqual(audit["adapter_invocations"][0]["context_packet_ref"], context_ref)
        self.assertEqual(audit["workflow_provenance"]["definition_hash"][:7], "sha256:")

    def test_fresh_kernel_uses_persisted_input_snapshot_for_prompt_context(self) -> None:
        workflow = self.workflow_with_runtime_stage(prompt_refs=PROMPT_REFS)
        kernel = self.kernel_for(workflow, prompt_registry_path=ROOT / "prompts")
        kernel.start(
            instance_id="instance-resume",
            inputs={"objective": "persist me across process restart"},
            now=self.now,
        )
        fresh_kernel = self.kernel_for(workflow, prompt_registry_path=ROOT / "prompts")

        step = fresh_kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "succeeded")
        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE receipt_id = ?",
            (step.receipt_id,),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        runtime_input = receipt["runtime_provenance"]["outputs"]["runtime_input"]
        self.assertEqual(
            runtime_input["context_packet"]["inputs"]["facts"]["workflow"]["objective"],
            "persist me across process restart",
        )

    def test_definition_hash_mismatch_blocks_before_adapter_invocation(self) -> None:
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        original = self.workflow_with_runtime_stage()
        kernel = WorkflowKernel(
            self.ledger,
            original,
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-mismatch", inputs={}, now=self.now)
        changed = WorkflowDef(
            id=original.id,
            version=original.version,
            name=original.name,
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("done",),
                    inputs={"operation": "execute"},
                    actors={"worker": "kernel-test"},
                ),
            ),
            transitions=(),
        )
        fresh_kernel = WorkflowKernel(
            self.ledger,
            changed,
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )

        step = fresh_kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("definition hash mismatch", step.failure_summary or "")
        self.assertEqual(adapter.receipts, [])
        invocation_count = self.ledger.connection.execute(
            "SELECT COUNT(*) AS count FROM adapter_invocations"
        ).fetchone()["count"]
        self.assertEqual(invocation_count, 0)

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

    def test_missing_prompt_blocks_before_adapter_invocation(self) -> None:
        missing_ref = PromptRef(id="stage.missing", kind="stage", version="9.9.9")
        workflow = self.workflow_with_runtime_stage(prompt_refs=(missing_ref,))
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
        self.assertIn("Missing required prompt", step.failure_summary or "")
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
        self.assertEqual(receipt["prompt_provenance"]["error"]["class"], "missing_dependency")

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

    def test_missing_required_artifact_blocks_transition_as_invalid_output(self) -> None:
        workflow = WorkflowDef(
            id="toy-required-artifact",
            version="0.1.0",
            name="Toy required artifact",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    outputs={"artifacts": ({"role": "packet", "required": True},)},
                ),
                StageDef(
                    id="next",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                ),
            ),
            transitions=(Transition(from_stage="draft", on="done", to_stage="next"),),
        )
        kernel = self.kernel_for(workflow)
        kernel.start(instance_id="instance-artifact", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "failed")
        self.assertIn("missing required artifact role", step.failure_summary or "")
        draft = self.ledger.get_stage_run("instance-artifact:draft:1")
        self.assertIsNotNone(draft)
        assert draft is not None
        self.assertEqual(draft.status, StageRunStatus.INVALID_OUTPUT)
        self.assertIsNone(self.ledger.get_stage_run("instance-artifact:next:1"))
        stored = self.ledger.get_workflow_instance("instance-artifact")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.BLOCKED)

    def test_required_output_field_blocks_transition_as_invalid_output(self) -> None:
        class FieldlessRuntimeAdapter(LocalFakeRuntimeAdapter):
            adapter_id = "runtime.fieldless"

            def invoke(self, invocation, runtime_input):
                return AdapterResult(
                    invocation_id=invocation.invocation_id,
                    status="succeeded",
                    outputs={"outcome": "done"},
                )

        adapter = FieldlessRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        workflow = WorkflowDef(
            id="toy-required-field",
            version="0.1.0",
            name="Toy required field",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter=adapter.adapter_id,
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    outputs={"required_fields": ("verdict",)},
                ),
            ),
            transitions=(Transition(from_stage="draft", on="done", terminal="done"),),
        )
        kernel = WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-field", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "failed")
        self.assertIn("missing required output field", step.failure_summary or "")
        run = self.ledger.get_stage_run("instance-field:draft:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.INVALID_OUTPUT)

    def test_retry_policy_queues_append_only_attempt_and_preserves_idempotency(self) -> None:
        class FailsOnceRuntimeAdapter(LocalFakeRuntimeAdapter):
            adapter_id = "runtime.fails_once"

            def __init__(self, *, created_at: str) -> None:
                super().__init__(created_at=created_at)
                self.calls = 0

            def invoke(self, invocation, runtime_input):
                self.calls += 1
                if self.calls == 1:
                    return AdapterResult(
                        invocation_id=invocation.invocation_id,
                        status="failed",
                        outputs={"error": "temporary"},
                    )
                return super().invoke(invocation, runtime_input)

        adapter = FailsOnceRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry(
            (AdapterRegistration.from_runtime_adapter(adapter, replay_safe=True),)
        )
        workflow = WorkflowDef(
            id="toy-retry",
            version="0.1.0",
            name="Toy retry",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter=adapter.adapter_id,
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    retry={"enabled": True, "max_attempts": 2, "backoff_seconds": 0},
                ),
            ),
            transitions=(Transition(from_stage="draft", on="done", terminal="done"),),
        )
        kernel = WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-retry", inputs={}, now=self.now)

        first = kernel.run_once(now=self.now)
        second = kernel.run_once(now=self.now)

        self.assertEqual(first.decision, "retry")
        self.assertEqual(second.decision, "succeeded")
        first_run = self.ledger.get_stage_run("instance-retry:draft:1")
        retry_run = self.ledger.get_stage_run("instance-retry:draft:2")
        self.assertIsNotNone(first_run)
        self.assertIsNotNone(retry_run)
        assert first_run is not None
        assert retry_run is not None
        self.assertEqual(first_run.status, StageRunStatus.FAILED)
        self.assertEqual(retry_run.status, StageRunStatus.SUCCEEDED)
        rows = self.ledger.connection.execute(
            """
            SELECT stage_run_id, idempotency_key, parent_stage_run_id
            FROM stage_runs
            WHERE instance_id = ?
            ORDER BY attempt
            """,
            ("instance-retry",),
        ).fetchall()
        self.assertEqual([row["stage_run_id"] for row in rows], ["instance-retry:draft:1", "instance-retry:draft:2"])
        self.assertEqual(rows[0]["idempotency_key"], rows[1]["idempotency_key"])
        self.assertEqual(rows[1]["parent_stage_run_id"], "instance-retry:draft:1")

    def test_retry_policy_handles_adapter_exception_as_recoverable_runtime_failure(self) -> None:
        class TimesOutOnceRuntimeAdapter(LocalFakeRuntimeAdapter):
            adapter_id = "runtime.times_out_once"

            def __init__(self, *, created_at: str) -> None:
                super().__init__(created_at=created_at)
                self.calls = 0

            def invoke(self, invocation, runtime_input):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("openclaw agent timed out after 180s")
                return super().invoke(invocation, runtime_input)

        adapter = TimesOutOnceRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry(
            (AdapterRegistration.from_runtime_adapter(adapter, replay_safe=True),)
        )
        workflow = WorkflowDef(
            id="toy-timeout-retry",
            version="0.1.0",
            name="Toy timeout retry",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter=adapter.adapter_id,
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    retry={"enabled": True, "max_attempts": 2, "backoff_seconds": 0},
                ),
            ),
            transitions=(Transition(from_stage="draft", on="done", terminal="done"),),
        )
        kernel = WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-timeout-retry", inputs={}, now=self.now)

        first = kernel.run_once(now=self.now)
        second = kernel.run_once(now=self.now)

        self.assertEqual(first.decision, "retry")
        self.assertIn("queued append-only retry", first.failure_summary or "")
        self.assertEqual(second.decision, "succeeded")
        first_run = self.ledger.get_stage_run("instance-timeout-retry:draft:1")
        retry_run = self.ledger.get_stage_run("instance-timeout-retry:draft:2")
        self.assertIsNotNone(first_run)
        self.assertIsNotNone(retry_run)
        assert first_run is not None
        assert retry_run is not None
        self.assertEqual(first_run.status, StageRunStatus.FAILED)
        self.assertEqual(first_run.failure_class, FailureClass.RUNTIME_FAILURE)
        self.assertEqual(retry_run.status, StageRunStatus.SUCCEEDED)

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

    def test_stage_policy_is_enforced_before_adapter_invocation(self) -> None:
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        workflow = self.workflow_with_runtime_stage(
            stage_policy={
                "class": "public_publish",
                "requires_explicit_approval": True,
            }
        )
        kernel = WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-policy", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("approval", step.failure_summary or "")
        self.assertEqual(adapter.receipts, [])
        self.assertEqual(
            self.ledger.connection.execute(
                "SELECT COUNT(*) AS count FROM adapter_invocations"
            ).fetchone()["count"],
            0,
        )
        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE stage_run_id = ?",
            ("instance-policy:draft:1",),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        self.assertEqual(receipt["kind"], "kernel.policy_preflight")
        self.assertEqual(receipt["policy_snapshot"]["decision"], "require_human")
        self.assertIn(
            "external_effect",
            receipt["policy_snapshot"]["effective_policy"]["risk_classes"],
        )

    def test_workflow_policy_is_stricter_than_adapter_policy(self) -> None:
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        workflow = self.workflow_with_runtime_stage(defaults={"policy_class": "destructive_change"})
        kernel = WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-workflow-policy", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertEqual(adapter.receipts, [])
        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE stage_run_id = ?",
            ("instance-workflow-policy:draft:1",),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        self.assertIn(
            "destructive_effect",
            receipt["policy_snapshot"]["effective_policy"]["risk_classes"],
        )

    def test_unknown_adapter_policy_metadata_fails_closed(self) -> None:
        adapter = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registration = AdapterRegistration(
            adapter_id=adapter.adapter_id,
            family=adapter.family,
            adapter=adapter,
            operations=adapter.operations,
            side_effects=(RiskClass.READ_ONLY,),
            metadata={"side_effects": ["unclassified_remote_mutation"]},
        )
        registry = AdapterRegistry((registration,))
        kernel = WorkflowKernel(
            self.ledger,
            self.workflow_with_runtime_stage(),
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-unknown-policy", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("unknown", step.failure_summary or "")
        self.assertEqual(adapter.receipts, [])

    def test_policy_approved_guard_blocks_without_human_decision(self) -> None:
        kernel = self.kernel_for(self.workflow_with_policy_approved_runtime_transition())
        kernel.start(instance_id="instance-guard", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("policy_approved", step.failure_summary or "")
        self.assertIsNone(self.ledger.get_stage_run("instance-guard:apply:1"))
        stored = self.ledger.get_workflow_instance("instance-guard")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.BLOCKED)
        events = [event["event_type"] for event in self.ledger.list_events()]
        self.assertIn("transition_guard_blocked", events)
        receipt_row = self.ledger.connection.execute(
            "SELECT receipt_json FROM receipts WHERE receipt_kind = ?",
            ("kernel.transition_guard",),
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["receipt_json"])
        self.assertEqual(receipt["runtime_provenance"]["guard"], "policy_approved")

    def test_policy_approved_guard_allows_recorded_human_approval(self) -> None:
        kernel = self.kernel_for(self.workflow_with_policy_approved_human_gate())
        self._run_to_waiting_gate(kernel, instance_id="instance-approved-guard")
        decision = self._decision_from_waiting_gate(
            approval_id="approval-guard",
            decision=ApprovalDecision.APPROVED,
        )

        result = kernel.ingest_human_decision(
            instance_id="instance-approved-guard",
            decision=decision,
            now=self.now,
        )

        self.assertEqual(result.decision, "queued")
        self.assertEqual(result.queued_stage_id, "apply")
        self.assertIsNotNone(self.ledger.get_stage_run("instance-approved-guard:apply:1"))

    def test_required_artifacts_block_before_guard_when_missing_artifact_roles(self) -> None:
        kernel = self.kernel_for(self.workflow_with_required_artifact_guard())
        kernel.start(instance_id="instance-artifact-missing", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "failed")
        self.assertIn("missing required artifact role", step.failure_summary or "")
        run = self.ledger.get_stage_run("instance-artifact-missing:draft:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.INVALID_OUTPUT)
        self.assertIsNone(self.ledger.get_stage_run("instance-artifact-missing:apply:1"))

    def test_has_required_artifacts_guard_allows_present_artifact_roles(self) -> None:
        class ArtifactRuntimeAdapter(LocalFakeRuntimeAdapter):
            def invoke(self, invocation, runtime_input):
                result = super().invoke(invocation, runtime_input)
                artifact = ArtifactRef(
                    artifact_id="artifact-review-packet",
                    role="review_packet",
                    uri="artifact://review-packet",
                    content_hash="sha256:" + "1" * 64,
                )
                return replace(result, artifact_refs=(artifact,))

        adapter = ArtifactRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        kernel = WorkflowKernel(
            self.ledger,
            self.workflow_with_required_artifact_guard(),
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-artifact-present", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "succeeded")
        apply = self.ledger.get_stage_run("instance-artifact-present:apply:1")
        self.assertIsNotNone(apply)
        assert apply is not None
        self.assertEqual(apply.status, StageRunStatus.QUEUED)

    def test_within_retry_budget_allows_under_budget_and_blocks_exhausted_attempts(self) -> None:
        adapter = OutcomeSequenceRuntimeAdapter(
            outcomes_by_stage={"run": ("retry_needed", "retry_needed")},
            created_at=self.now.isoformat(),
        )
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        kernel = WorkflowKernel(
            self.ledger,
            self.workflow_with_retry_budget_guard(),
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-retry-guard", inputs={}, now=self.now)

        first = kernel.run_once(now=self.now)
        second = kernel.run_once(now=self.now)

        self.assertEqual(first.decision, "succeeded")
        self.assertEqual(second.decision, "blocked")
        self.assertIn("exhausted budget", second.failure_summary or "")
        self.assertIsNotNone(self.ledger.get_stage_run("instance-retry-guard:run:2"))
        self.assertIsNone(self.ledger.get_stage_run("instance-retry-guard:run:3"))

    def test_within_revision_budget_counts_revision_turns_not_return_edge(self) -> None:
        adapter = OutcomeSequenceRuntimeAdapter(
            outcomes_by_stage={
                "review": ("needs_revision", "needs_revision"),
                "revise": ("revised",),
            },
            created_at=self.now.isoformat(),
        )
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        kernel = WorkflowKernel(
            self.ledger,
            self.workflow_with_revision_budget_guard(),
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-revision-guard", inputs={}, now=self.now)

        first = kernel.run_once(now=self.now)
        second = kernel.run_once(now=self.now)
        third = kernel.run_once(now=self.now)

        self.assertEqual(first.decision, "succeeded")
        self.assertEqual(second.decision, "succeeded")
        self.assertEqual(third.decision, "blocked")
        self.assertIn("within_revision_budget", third.failure_summary or "")
        self.assertIsNotNone(self.ledger.get_stage_run("instance-revision-guard:revise:1"))
        self.assertIsNotNone(self.ledger.get_stage_run("instance-revision-guard:review:2"))
        self.assertIsNone(self.ledger.get_stage_run("instance-revision-guard:revise:2"))

    def test_within_resume_budget_allows_one_recovery_cycle_then_blocks(self) -> None:
        adapter = OutcomeSequenceRuntimeAdapter(
            outcomes_by_stage={
                "run_or_resume": ("retry_needed", "retry_needed"),
                "recover": ("resumed",),
            },
            created_at=self.now.isoformat(),
        )
        registry = AdapterRegistry((AdapterRegistration.from_runtime_adapter(adapter),))
        kernel = WorkflowKernel(
            self.ledger,
            self.workflow_with_resume_budget_guard(),
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        kernel.start(instance_id="instance-resume-guard", inputs={}, now=self.now)

        first = kernel.run_once(now=self.now)
        second = kernel.run_once(now=self.now)
        third = kernel.run_once(now=self.now)

        self.assertEqual(first.decision, "succeeded")
        self.assertEqual(second.decision, "succeeded")
        self.assertEqual(third.decision, "blocked")
        self.assertIn("within_resume_budget", third.failure_summary or "")
        self.assertIsNotNone(self.ledger.get_stage_run("instance-resume-guard:recover:1"))
        self.assertIsNotNone(self.ledger.get_stage_run("instance-resume-guard:run_or_resume:2"))
        self.assertIsNone(self.ledger.get_stage_run("instance-resume-guard:recover:2"))

    def test_budget_guard_without_budget_fails_closed(self) -> None:
        kernel = self.kernel_for(self.workflow_with_retry_guard_stub())
        kernel.start(instance_id="instance-missing-budget", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("cannot resolve a valid budget", step.failure_summary or "")
        self.assertIsNone(self.ledger.get_stage_run("instance-missing-budget:apply:1"))

    def test_unknown_transition_guard_still_fails_closed_at_runtime(self) -> None:
        workflow = WorkflowDef(
            id="toy-unknown-guard-runtime",
            version="0.1.0",
            name="Toy unknown guard workflow",
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
                    id="apply",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("applied",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                ),
            ),
            transitions=(
                Transition(from_stage="draft", on="done", to_stage="apply", guard="typo_guard"),
            ),
        )
        kernel = self.kernel_for(workflow)
        kernel.start(instance_id="instance-unknown-guard", inputs={}, now=self.now)

        step = kernel.run_once(now=self.now)

        self.assertEqual(step.decision, "blocked")
        self.assertIn("Unknown transition guard", step.failure_summary or "")
        self.assertIsNone(self.ledger.get_stage_run("instance-unknown-guard:apply:1"))

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

    def test_external_effect_surface_adapter_blocks_before_publish(self) -> None:
        surface = LocalFakeSurfaceAdapter(created_at=self.now.isoformat())
        runtime = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        registry = AdapterRegistry(
            (
                AdapterRegistration.from_runtime_adapter(runtime),
                AdapterRegistration.from_surface_adapter(
                    surface,
                    side_effects=(RiskClass.EXTERNAL_EFFECT,),
                ),
            )
        )
        kernel = WorkflowKernel(
            self.ledger,
            self.workflow_with_surface_lifecycle_gate(surface.adapter_id),
            KernelRuntimeConfig(owner_id="kernel-test", adapter_registry=registry),
        )
        self._run_to_waiting_gate(kernel, instance_id="instance-external-surface")

        with self.assertRaisesRegex(AdapterRegistryError, "surface adapter policy blocked"):
            kernel.publish_waiting_human_gate(
                instance_id="instance-external-surface",
                test_only=True,
                non_live=True,
                now=self.now,
            )

        self.assertEqual(surface.receipts, [])
        events = [event["event_type"] for event in self.ledger.list_events()]
        self.assertNotIn("human_gate_surface_published", events)

    def test_prompt_backed_choice_gate_routes_selected_option_to_downstream(self) -> None:
        class CapturingRuntimeAdapter(LocalFakeRuntimeAdapter):
            def __init__(self, *, created_at: str) -> None:
                super().__init__(created_at=created_at)
                self.inputs_by_stage: dict[str, list[dict]] = {}

            def invoke(self, invocation, runtime_input):
                stage_id = str(runtime_input["stage"]["id"])
                self.inputs_by_stage.setdefault(stage_id, []).append(dict(runtime_input))
                return super().invoke(invocation, runtime_input)

        with tempfile.TemporaryDirectory() as notes_dir:
            runtime = CapturingRuntimeAdapter(created_at=self.now.isoformat())
            surface = LocalMarkdownHumanReviewSurfaceAdapter(
                notes_dir,
                created_at=self.now.isoformat(),
            )
            registry = AdapterRegistry(
                (
                    AdapterRegistration.from_runtime_adapter(runtime),
                    AdapterRegistration.from_surface_adapter(surface),
                )
            )
            workflow = self.workflow_with_prompt_backed_choice_gate(surface.adapter_id)
            kernel = WorkflowKernel(
                self.ledger,
                workflow,
                KernelRuntimeConfig(
                    owner_id="kernel-test",
                    adapter_registry=registry,
                    prompt_registry=PromptRegistry.load(ROOT / "prompts"),
                ),
            )
            kernel.start(instance_id="instance-choice", inputs={}, now=self.now)
            propose = kernel.run_once(now=self.now)
            wait = kernel.run_once(now=self.now)

            self.assertEqual(propose.decision, "succeeded")
            self.assertEqual(wait.decision, "waiting_on_human")
            prompt_row = self.ledger.connection.execute(
                """
                SELECT prompt_hash, context_packet_ref, rendered_context_hash
                FROM stage_runs
                WHERE stage_run_id = ?
                """,
                ("instance-choice:choose:1",),
            ).fetchone()
            self.assertIsNotNone(prompt_row)
            assert prompt_row is not None
            self.assertTrue(prompt_row["prompt_hash"].startswith("sha256:"))
            self.assertTrue(prompt_row["context_packet_ref"])
            self.assertTrue(prompt_row["rendered_context_hash"].startswith("sha256:"))

            publish = kernel.publish_waiting_human_gate(
                instance_id="instance-choice",
                test_only=True,
                non_live=True,
                now=self.now,
            )
            note_path = Path(publish.outputs["note_path"])
            note_text = note_path.read_text(encoding="utf-8")
            note_path.write_text(
                note_text.replace("- [ ] `option_2`", "- [x] `option_2`"),
                encoding="utf-8",
            )
            ingest = kernel.ingest_human_gate_surface_decision(
                instance_id="instance-choice",
                now=self.now,
            )
            apply = kernel.run_once(now=self.now)

        self.assertEqual(publish.status, "succeeded")
        self.assertEqual(publish.outputs["choice_options"][1]["id"], "option_2")
        self.assertTrue(publish.outputs["choice_manifest_hash"].startswith("sha256:"))
        self.assertEqual(
            publish.outputs["prompt_provenance"]["refs"][0]["id"],
            "stage.choice_gate",
        )
        self.assertEqual(ingest.status, "succeeded")
        self.assertIsNotNone(ingest.decision_result)
        assert ingest.decision_result is not None
        self.assertEqual(ingest.decision_result.outcome, "option_2")
        self.assertEqual(apply.decision, "succeeded")
        apply_input = runtime.inputs_by_stage["apply"][-1]
        latest_decision = apply_input["latest_human_decision"]
        self.assertEqual(latest_decision["decision"], "option_2")
        self.assertEqual(latest_decision["action_fingerprint"], publish.outputs["action_fingerprint"])
        self.assertEqual(latest_decision["choice_manifest_hash"], publish.outputs["choice_manifest_hash"])
        self.assertEqual(latest_decision["selected_option"]["id"], "option_2")
        self.assertEqual(latest_decision["selected_option"]["budget_profile"], "balanced")
        stored = self.ledger.get_workflow_instance("instance-choice")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.DONE)

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
        stage_policy: dict | None = None,
        defaults: dict | None = None,
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
                    policy=stage_policy or {},
                ),
            ),
            transitions=(),
            defaults=defaults or {},
        )

    def workflow_with_lease_policy(
        self,
        *,
        default_seconds: int | None = None,
        actor_seconds: int | None = None,
        stage_seconds: int | None = None,
    ) -> WorkflowDef:
        actor_config = {
            "adapter": "runtime.local_fake",
            "role": "worker",
        }
        if actor_seconds is not None:
            actor_config["lease"] = {"seconds": actor_seconds}
        return WorkflowDef(
            id="toy-lease-policy",
            version="0.1.0",
            name="Toy lease policy workflow",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "actors.worker"},
                    lease={"seconds": stage_seconds} if stage_seconds is not None else {},
                ),
            ),
            transitions=(),
            defaults={"lease": {"seconds": default_seconds}} if default_seconds is not None else {},
            actors={"worker": actor_config},
        )

    def workflow_with_policy_approved_runtime_transition(self) -> WorkflowDef:
        return WorkflowDef(
            id="toy-policy-guard-runtime",
            version="0.1.0",
            name="Toy policy guard runtime workflow",
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
                    id="apply",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("applied",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                ),
            ),
            transitions=(
                Transition(from_stage="draft", on="done", to_stage="apply", guard="policy_approved"),
                Transition(from_stage="apply", on="applied", terminal="done"),
            ),
        )

    def workflow_with_policy_approved_human_gate(self) -> WorkflowDef:
        workflow = self.workflow_with_human_gate()
        return WorkflowDef(
            id="toy-policy-approved-human",
            version=workflow.version,
            name="Toy policy approved human workflow",
            stages=workflow.stages,
            transitions=(
                Transition(from_stage="draft", on="done", to_stage="approve"),
                Transition(
                    from_stage="approve",
                    on="approval_granted",
                    to_stage="apply",
                    guard="policy_approved",
                ),
                Transition(from_stage="approve", on="reject", terminal="policy_denied"),
                Transition(from_stage="approve", on="revise_plan", to_stage="draft"),
                Transition(from_stage="apply", on="applied", terminal="done"),
            ),
        )

    def workflow_with_required_artifact_guard(self) -> WorkflowDef:
        return WorkflowDef(
            id="toy-artifact-guard",
            version="0.1.0",
            name="Toy artifact guard workflow",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    outputs={
                        "artifacts": (
                            {"role": "review_packet", "required": True},
                        )
                    },
                    actors={"worker": "kernel-test"},
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
                Transition(
                    from_stage="draft",
                    on="done",
                    to_stage="apply",
                    guard="has_required_artifacts",
                ),
                Transition(from_stage="apply", on="applied", terminal="done"),
            ),
        )

    def workflow_with_retry_guard_stub(self) -> WorkflowDef:
        return WorkflowDef(
            id="toy-retry-guard",
            version="0.1.0",
            name="Toy retry guard workflow",
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
                    id="apply",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("applied",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                ),
            ),
            transitions=(
                Transition(from_stage="draft", on="done", to_stage="apply", guard="within_retry_budget"),
                Transition(from_stage="apply", on="applied", terminal="done"),
            ),
        )

    def workflow_with_retry_budget_guard(self) -> WorkflowDef:
        return WorkflowDef(
            id="toy-retry-budget-guard",
            version="0.1.0",
            name="Toy retry budget workflow",
            stages=(
                StageDef(
                    id="run",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("retry_needed", "done"),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                    retry={"max_attempts": 2},
                ),
            ),
            transitions=(
                Transition(from_stage="run", on="retry_needed", to_stage="run", guard="within_retry_budget"),
                Transition(from_stage="run", on="done", terminal="done"),
            ),
        )

    def workflow_with_revision_budget_guard(self) -> WorkflowDef:
        return WorkflowDef(
            id="toy-revision-budget-guard",
            version="0.1.0",
            name="Toy revision budget workflow",
            stages=(
                StageDef(
                    id="review",
                    type=StageType.A2A_REVIEW_LOOP,
                    adapter="runtime.local_fake",
                    outcomes=("needs_revision", "accepted"),
                    inputs={"operation": "invoke"},
                    actors={"reviewer": "kernel-test"},
                    budget={"max_revision_turns": 1},
                ),
                StageDef(
                    id="revise",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("revised",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                    budget={"max_revision_turns": 1},
                ),
            ),
            transitions=(
                Transition(from_stage="review", on="needs_revision", to_stage="revise", guard="within_revision_budget"),
                Transition(from_stage="review", on="accepted", terminal="done"),
                Transition(from_stage="revise", on="revised", to_stage="review", guard="within_revision_budget"),
            ),
        )

    def workflow_with_resume_budget_guard(self) -> WorkflowDef:
        return WorkflowDef(
            id="toy-resume-budget-guard",
            version="0.1.0",
            name="Toy resume budget workflow",
            stages=(
                StageDef(
                    id="run_or_resume",
                    type=StageType.SYSTEM_ACTION,
                    adapter="runtime.local_fake",
                    outcomes=("retry_needed", "package_ready"),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                    budget={"max_resume_attempts": 1},
                ),
                StageDef(
                    id="recover",
                    type=StageType.RECOVERY,
                    adapter="runtime.local_fake",
                    outcomes=("resumed", "blocked"),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                ),
            ),
            transitions=(
                Transition(from_stage="run_or_resume", on="retry_needed", to_stage="recover", guard="within_resume_budget"),
                Transition(from_stage="run_or_resume", on="package_ready", terminal="done"),
                Transition(from_stage="recover", on="resumed", to_stage="run_or_resume", guard="within_resume_budget"),
                Transition(from_stage="recover", on="blocked", terminal="blocked"),
            ),
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

    def workflow_with_prompt_backed_choice_gate(self, surface_adapter: str) -> WorkflowDef:
        return WorkflowDef(
            id="toy-choice-gate",
            version="0.1.0",
            name="Toy prompt-backed choice gate workflow",
            stages=(
                StageDef(
                    id="propose_options",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("options_ready",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                ),
                StageDef(
                    id="choose",
                    type=StageType.HUMAN_GATE,
                    adapter=surface_adapter,
                    outcomes=("option_1", "option_2", "option_3", "ignore"),
                    inputs={"decision_action": "choose_token_optimization_option"},
                    actors={"operator": "Suman(test)"},
                    prompt_refs=(
                        PromptRef(id="stage.choice_gate", kind="stage", version="1.0.0"),
                    ),
                    surface={
                        "title": "Token optimizer safe-choice gate",
                        "human_ask": "Select exactly one local fixture option.",
                        "evidence_refs": ("fixture://token-optimizer/options",),
                        "choice_options": (
                            {
                                "id": "option_1",
                                "label": "Conservative",
                                "budget_profile": "low",
                                "summary": "Use the smallest token budget.",
                            },
                            {
                                "id": "option_2",
                                "label": "Balanced",
                                "budget_profile": "balanced",
                                "summary": "Use a moderate token budget.",
                            },
                            {
                                "id": "option_3",
                                "label": "Deep",
                                "budget_profile": "high",
                                "summary": "Use the largest local fixture budget.",
                            },
                            {
                                "id": "ignore",
                                "label": "Ignore",
                                "budget_profile": "none",
                                "summary": "Do not route optimization work.",
                            },
                        ),
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
                Transition(from_stage="propose_options", on="options_ready", to_stage="choose"),
                Transition(from_stage="choose", on="option_1", terminal="final_approval_required"),
                Transition(from_stage="choose", on="option_2", to_stage="apply"),
                Transition(from_stage="choose", on="option_3", terminal="final_approval_required"),
                Transition(from_stage="choose", on="ignore", terminal="cancelled"),
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
