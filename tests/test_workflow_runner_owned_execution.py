import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AUTOMATED_SUMAN_REVIEWER_HUMAN_REF,
    AdapterRegistration,
    AdapterRegistry,
    AutomatedSumanReviewer,
    KernelRuntimeConfig,
    LocalFakeRuntimeAdapter,
    LocalMarkdownHumanReviewSurfaceAdapter,
    StageDef,
    StageRunStatus,
    StageType,
    Transition,
    WorkflowDef,
    WorkflowKernel,
    WorkflowLedger,
    WorkflowRunner,
    WorkflowStatus,
)


UTC = timezone.utc


class WorkflowRunnerOwnedExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "kernel.sqlite3"
        self.notes_dir = Path(self.tmpdir.name) / "review-notes"
        self.ledger = WorkflowLedger(self.db_path)
        self.now = datetime(2026, 5, 31, 15, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.ledger.close()
        self.tmpdir.cleanup()

    def test_owned_runner_discovers_and_executes_queued_stage(self) -> None:
        kernel = self.kernel_for(self.single_stage_workflow())
        kernel.start(instance_id="instance-queued", inputs={}, now=self.now)

        summary = self.runner().run_kernel_until_idle(kernel, now=self.now)

        self.assertEqual(summary.status, "done")
        self.assertEqual(summary.instance_id, "instance-queued")
        self.assertEqual(summary.stages_run, 1)
        run = self.ledger.get_stage_run("instance-queued:draft:1")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status, StageRunStatus.SUCCEEDED)
        stored = self.ledger.get_workflow_instance("instance-queued")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.DONE)

    def test_owned_runner_publishes_waiting_human_gate_review_note(self) -> None:
        kernel = self.kernel_for(self.human_gate_workflow())
        kernel.start(instance_id="instance-review", inputs={}, now=self.now)

        summary = self.runner().run_kernel_until_idle(
            kernel,
            publish_human_gate=True,
            now=self.now,
        )

        self.assertEqual(summary.status, "waiting_on_human")
        self.assertEqual(summary.stages_run, 2)
        self.assertEqual([result.operation for result in summary.surface_results], ["publish", "readback"])
        publish = summary.surface_results[0]
        note_path = Path(publish.outputs["note_path"])
        self.assertTrue(note_path.exists())
        self.assertIn("Check exactly one allowed decision", note_path.read_text(encoding="utf-8"))
        publish_events = [
            event for event in self.ledger.list_events(stage_run_id="instance-review:approve:1")
            if event["event_type"] == "human_gate_surface_published"
        ]
        self.assertEqual(len(publish_events), 1)

    def test_owned_runner_ingests_checked_approval_and_resumes_to_done(self) -> None:
        kernel = self.kernel_for(self.human_gate_workflow())
        kernel.start(instance_id="instance-approve", inputs={}, now=self.now)
        first = self.runner().run_kernel_until_idle(
            kernel,
            publish_human_gate=True,
            now=self.now,
        )
        note_path = Path(first.surface_results[0].outputs["note_path"])
        self._check_decision(note_path, "approved")

        resumed = self.runner().run_kernel_until_idle(
            kernel,
            publish_human_gate=True,
            ingest_human_decision=True,
            now=self.now,
        )

        self.assertEqual(resumed.status, "done")
        self.assertEqual(resumed.stages_run, 1)
        approve = self.ledger.get_stage_run("instance-approve:approve:1")
        apply = self.ledger.get_stage_run("instance-approve:apply:1")
        self.assertIsNotNone(approve)
        self.assertIsNotNone(apply)
        assert approve is not None
        assert apply is not None
        self.assertEqual(approve.status, StageRunStatus.SUCCEEDED)
        self.assertEqual(apply.status, StageRunStatus.SUCCEEDED)
        stored = self.ledger.get_workflow_instance("instance-approve")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.DONE)

    def test_owned_runner_blocks_mismatched_surface_decision_without_resuming(self) -> None:
        kernel = self.kernel_for(self.human_gate_workflow())
        kernel.start(instance_id="instance-mismatch", inputs={}, now=self.now)
        first = self.runner().run_kernel_until_idle(
            kernel,
            publish_human_gate=True,
            now=self.now,
        )
        note_path = Path(first.surface_results[0].outputs["note_path"])
        text = note_path.read_text(encoding="utf-8")
        note_path.write_text(
            text.replace("- [ ] `approved`", "- [x] `approved`").replace(
                "- Action fingerprint: `",
                "- Action fingerprint: `edited-",
            ),
            encoding="utf-8",
        )

        blocked = self.runner().run_kernel_until_idle(
            kernel,
            publish_human_gate=True,
            ingest_human_decision=True,
            now=self.now,
        )

        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(blocked.stop_reason, "human_gate_decision_blocked")
        approve = self.ledger.get_stage_run("instance-mismatch:approve:1")
        self.assertIsNotNone(approve)
        assert approve is not None
        self.assertEqual(approve.status, StageRunStatus.BLOCKED)
        self.assertIsNone(self.ledger.get_stage_run("instance-mismatch:apply:1"))

    def test_owned_runner_reuses_published_gate_after_interruption(self) -> None:
        kernel = self.kernel_for(self.human_gate_workflow())
        kernel.start(instance_id="instance-interrupt", inputs={}, now=self.now)

        first = self.runner().run_kernel_until_idle(
            kernel,
            publish_human_gate=True,
            now=self.now,
        )
        second = self.runner().run_kernel_until_idle(
            kernel,
            publish_human_gate=True,
            now=self.now,
        )

        self.assertEqual(first.status, "waiting_on_human")
        self.assertEqual(second.status, "waiting_on_human")
        self.assertEqual([result.operation for result in second.surface_results], ["readback"])
        publish_events = [
            event for event in self.ledger.list_events(stage_run_id="instance-interrupt:approve:1")
            if event["event_type"] == "human_gate_surface_published"
        ]
        self.assertEqual(len(publish_events), 1)
        notes = list(self.notes_dir.glob("review_cards/*.md"))
        self.assertEqual(len(notes), 1)

    def test_owned_runner_automated_suman_reviewer_approves_safe_shadow_gate(self) -> None:
        kernel = self.kernel_for(self.human_gate_workflow())
        kernel.start(instance_id="instance-auto-approve", inputs={}, now=self.now)
        artifact = Path(self.tmpdir.name) / "draft-package.json"
        artifact.write_text('{"ok": true}\n', encoding="utf-8")

        summary = self.runner().run_kernel_until_idle(
            kernel,
            publish_human_gate=True,
            ingest_human_decision=True,
            automated_reviewer=AutomatedSumanReviewer(created_at=self.now.isoformat()),
            reviewer_context={
                "required_artifacts": (str(artifact),),
                "public_publish_blocked": True,
                "adoption_blockers": (),
            },
            now=self.now,
        )

        self.assertEqual(summary.status, "done")
        self.assertEqual(
            [result.operation for result in summary.surface_results],
            ["publish", "readback", "automated_review", "ingest_decisions"],
        )
        review = summary.surface_results[2]
        self.assertEqual(review.decision, "approved")
        self.assertEqual(review.human_ref, AUTOMATED_SUMAN_REVIEWER_HUMAN_REF)
        self.assertTrue(Path(review.receipt_path).exists())
        note_text = Path(review.note_path).read_text(encoding="utf-8")
        self.assertIn("- [x] `approved`", note_text)
        row = self.ledger.connection.execute(
            "SELECT decision, human_ref, canonical_surface FROM human_decisions WHERE instance_id = ?",
            ("instance-auto-approve",),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["decision"], "approved")
        self.assertEqual(row["human_ref"], AUTOMATED_SUMAN_REVIEWER_HUMAN_REF)
        self.assertNotEqual(row["human_ref"], "Suman")
        self.assertEqual(row["canonical_surface"], "local_test_review")

    def test_automated_suman_reviewer_requests_revision_when_artifacts_are_missing(self) -> None:
        kernel = self.kernel_for(self.revise_blocks_workflow())
        kernel.start(instance_id="instance-auto-revise", inputs={}, now=self.now)

        summary = self.runner().run_kernel_until_idle(
            kernel,
            publish_human_gate=True,
            ingest_human_decision=True,
            automated_reviewer=AutomatedSumanReviewer(created_at=self.now.isoformat()),
            reviewer_context={
                "required_artifacts": (str(Path(self.tmpdir.name) / "missing-draft.json"),),
                "public_publish_blocked": True,
            },
            now=self.now,
        )

        self.assertEqual(summary.status, "blocked")
        review = summary.surface_results[2]
        self.assertEqual(review.operation, "automated_review")
        self.assertEqual(review.decision, "revise")
        self.assertFalse(review.outputs["checks"]["required_artifacts_present"])
        approve = self.ledger.get_stage_run("instance-auto-revise:approve:1")
        self.assertIsNotNone(approve)
        assert approve is not None
        self.assertEqual(approve.status, StageRunStatus.SUCCEEDED)
        stored = self.ledger.get_workflow_instance("instance-auto-revise")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, WorkflowStatus.BLOCKED)

    def test_automated_suman_reviewer_refuses_to_approve_public_publish_or_external_send(self) -> None:
        kernel = self.kernel_for(self.public_publish_gate_workflow())
        kernel.start(instance_id="instance-auto-public-publish", inputs={}, now=self.now)

        summary = self.runner().run_kernel_until_idle(
            kernel,
            publish_human_gate=True,
            ingest_human_decision=True,
            automated_reviewer=AutomatedSumanReviewer(created_at=self.now.isoformat()),
            reviewer_context={
                "override_decision": "approved",
                "hard_gates": ("external_send",),
                "required_artifacts": (),
                "public_publish_blocked": False,
            },
            now=self.now,
        )

        self.assertEqual(summary.status, "blocked")
        review = summary.surface_results[2]
        self.assertEqual(review.operation, "automated_review")
        self.assertEqual(review.decision, "park")
        self.assertFalse(review.outputs["checks"]["unsafe_effect_absent"])
        self.assertIn("action:public_publish", review.outputs["checks"]["unsafe_reasons"])
        self.assertIn("hard_gates:external_send", review.outputs["checks"]["unsafe_reasons"])
        rows = self.ledger.connection.execute(
            "SELECT decision, human_ref FROM human_decisions WHERE instance_id = ?",
            ("instance-auto-public-publish",),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "park")
        self.assertEqual(rows[0]["human_ref"], AUTOMATED_SUMAN_REVIEWER_HUMAN_REF)

    def runner(self) -> WorkflowRunner:
        return WorkflowRunner(self.ledger, owner_id="owned-runner-test")

    def kernel_for(self, workflow: WorkflowDef) -> WorkflowKernel:
        runtime = LocalFakeRuntimeAdapter(created_at=self.now.isoformat())
        surface = LocalMarkdownHumanReviewSurfaceAdapter(
            self.notes_dir,
            created_at=self.now.isoformat(),
            canonical_surface="local_test_review",
        )
        registry = AdapterRegistry(
            (
                AdapterRegistration.from_runtime_adapter(runtime),
                AdapterRegistration.from_surface_adapter(surface),
            )
        )
        return WorkflowKernel(
            self.ledger,
            workflow,
            KernelRuntimeConfig(
                owner_id="kernel-test",
                adapter_registry=registry,
            ),
        )

    def single_stage_workflow(self) -> WorkflowDef:
        return WorkflowDef(
            id="owned-single-stage",
            version="0.1.0",
            name="Owned single stage",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.local_fake",
                    outcomes=("done",),
                    inputs={"operation": "invoke"},
                    actors={"worker": "kernel-test"},
                ),
            ),
            transitions=(Transition(from_stage="draft", on="done", terminal="done"),),
        )

    def human_gate_workflow(self) -> WorkflowDef:
        return WorkflowDef(
            id="owned-human-gate",
            version="0.1.0",
            name="Owned human gate workflow",
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
                    adapter="surface.local_markdown_human_review",
                    outcomes=("approved", "rejected", "revise"),
                    inputs={"decision_action": "review_clear"},
                    actors={"operator": "operator(test)"},
                    surface={
                        "title": "Review packet",
                        "human_ask": "Choose the next workflow state.",
                        "allowed_decisions": ("approved", "rejected", "revise"),
                        "evidence_refs": ("fixture://runner-owned",),
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

    def revise_blocks_workflow(self) -> WorkflowDef:
        return WorkflowDef(
            id="owned-human-gate-revise-blocks",
            version="0.1.0",
            name="Owned human gate revision blocks",
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
                    adapter="surface.local_markdown_human_review",
                    outcomes=("approved", "revise", "park"),
                    inputs={"decision_action": "review_clear"},
                    actors={"operator": "operator(test)"},
                    surface={
                        "title": "Review packet",
                        "human_ask": "Choose the next workflow state.",
                        "allowed_decisions": ("approved", "revise", "park"),
                        "evidence_refs": ("fixture://runner-owned",),
                    },
                ),
            ),
            transitions=(
                Transition(from_stage="draft", on="done", to_stage="approve"),
                Transition(from_stage="approve", on="approved", terminal="done"),
                Transition(from_stage="approve", on="revise", terminal="blocked"),
                Transition(from_stage="approve", on="park", terminal="blocked"),
            ),
        )

    def public_publish_gate_workflow(self) -> WorkflowDef:
        return WorkflowDef(
            id="owned-public-publish-gate",
            version="0.1.0",
            name="Owned public publish gate",
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
                    adapter="surface.local_markdown_human_review",
                    outcomes=("approved", "revise", "park"),
                    inputs={"decision_action": "public_publish"},
                    actors={"operator": "operator(test)"},
                    surface={
                        "title": "Public publish gate",
                        "human_ask": "Choose the next workflow state.",
                        "allowed_decisions": ("approved", "revise", "park"),
                        "evidence_refs": ("fixture://runner-owned-public",),
                    },
                ),
            ),
            transitions=(
                Transition(from_stage="draft", on="done", to_stage="approve"),
                Transition(from_stage="approve", on="approved", terminal="done"),
                Transition(from_stage="approve", on="revise", terminal="blocked"),
                Transition(from_stage="approve", on="park", terminal="blocked"),
            ),
        )

    def _check_decision(self, note_path: Path, decision: str) -> None:
        note_path.write_text(
            note_path.read_text(encoding="utf-8").replace(
                f"- [ ] `{decision}`",
                f"- [x] `{decision}`",
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
