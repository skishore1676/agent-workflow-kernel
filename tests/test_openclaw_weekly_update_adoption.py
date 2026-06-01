import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

from agent_workflow_kernel import (  # noqa: E402
    ActionRequest,
    PromptRegistry,
    RiskClass,
    StageType,
    build_test_only_suman_approval,
)
from agent_workflow_kernel.dsl import load_workflow_file  # noqa: E402
from agent_workflow_kernel_openclaw import (  # noqa: E402
    WEEKLY_UPDATE_ADOPTION_REPORT_SCHEMA,
    load_weekly_update_fixture,
    adoption_report_from_fixture,
    receipts_from_weekly_update,
    weekly_blackboard_bucket,
    weekly_checked_state,
    weekly_evidence_link,
    weekly_item_id,
    weekly_mode,
    weekly_note_path,
    weekly_owner,
    weekly_source_artifact,
)


WORKFLOW_PATH = ROOT / "workflows" / "jarvis_weekly_update_shadow.yaml"
FIXTURE_DIR = ROOT / "fixtures" / "openclaw" / "weekly_update"
READY_FIXTURE = FIXTURE_DIR / "weekly_check_in_ready.json"
CLEARED_FIXTURE = FIXTURE_DIR / "weekly_check_in_cleared.json"


class OpenClawWeeklyUpdateAdoptionTest(unittest.TestCase):
    def test_workflow_validates_and_keeps_suman_gate_explicit(self) -> None:
        workflow = load_workflow_file(WORKFLOW_PATH)
        stages = {stage.id: stage for stage in workflow.stages}

        self.assertEqual(workflow.id, "jarvis_weekly_update_shadow")
        self.assertEqual(stages["suman_review_gate"].type, StageType.HUMAN_GATE)
        self.assertTrue(stages["suman_review_gate"].policy["requires_explicit_approval"])
        self.assertEqual(stages["suman_review_gate"].policy["binds_to"], "receipts.readback_blackboard_card")
        self.assertFalse(stages["route_follow_up"].policy["external_effects"])
        self.assertTrue(stages["route_follow_up"].policy["shadow_only"])

        transitions = {(transition.from_stage, transition.on): transition for transition in workflow.transitions}
        self.assertEqual(transitions[("readback_blackboard_card", "needs_review")].to_stage, "suman_review_gate")
        self.assertEqual(transitions[("readback_blackboard_card", "read_clear")].to_stage, "route_follow_up")

    def test_jarvis_executable_agent_stages_have_resolvable_prompt_refs(self) -> None:
        workflow = load_workflow_file(WORKFLOW_PATH)
        registry = PromptRegistry.load(ROOT / "prompts")
        jarvis_stages = [
            stage for stage in workflow.stages if "actors.jarvis" in stage.actors.values()
        ]

        self.assertEqual([stage.id for stage in jarvis_stages], ["route_follow_up"])
        for stage in jarvis_stages:
            with self.subTest(stage_id=stage.id):
                self.assertTrue(stage.prompt_refs, f"{stage.id} must not be prompt-anonymous")
                self.assertEqual(
                    [ref.id for ref in stage.prompt_refs],
                    [
                        "identity.jarvis_weekly_shadow_worker",
                        "policy.no_external_effects",
                        "lane.jarvis_weekly_update_shadow",
                        "stage.jarvis_weekly.route_follow_up",
                    ],
                )

                bundle = registry.resolve(stage.prompt_refs)

                self.assertEqual(
                    [prompt.ref.kind for prompt in bundle.prompts],
                    ["identity", "policy", "lane", "stage"],
                )
                self.assertTrue(all(prompt.content_hash.startswith("sha256:") for prompt in bundle.prompts))

    def test_fixture_helpers_expose_weekly_update_blackboard_fields(self) -> None:
        fixture = load_weekly_update_fixture(READY_FIXTURE)

        self.assertEqual(weekly_mode(fixture), "weekly-personal")
        self.assertEqual(
            weekly_note_path(fixture),
            "05 Knowledge Vault/Fast Memory/Personal/Weekly/Suman Jarvis Improvement Loop/2026-W22.md",
        )
        self.assertEqual(weekly_item_id(fixture), "vault-intelligence-weekly-personal-2026-w22")
        self.assertTrue(weekly_source_artifact(fixture).startswith("vault://"))
        self.assertEqual(weekly_blackboard_bucket(fixture), "Read / Clear")
        self.assertEqual(weekly_owner(fixture), "Suman")
        self.assertIn("Suman + Jarvis Weekly Check-in", weekly_evidence_link(fixture))
        self.assertEqual(weekly_checked_state(fixture), {"checked": False, "state": "unread"})

    def test_ready_fixture_maps_to_stage_observations_receipts_and_human_wait(self) -> None:
        fixture = load_weekly_update_fixture(READY_FIXTURE)

        report = adoption_report_from_fixture(fixture)

        self.assertEqual(report.schema, WEEKLY_UPDATE_ADOPTION_REPORT_SCHEMA)
        self.assertEqual(report.status, "waiting_on_human")
        self.assertEqual(report.current_stage_id, "suman_review_gate")
        self.assertIsNone(report.terminal_status)
        self.assertEqual(
            [observation.stage_id for observation in report.observations],
            ["discover_weekly_artifact", "readback_blackboard_card", "suman_review_gate"],
        )
        self.assertEqual(report.observations[1].outcome, "needs_review")
        self.assertEqual(report.observations[2].status, "approval_required")
        self.assertEqual(report.receipts[-1].policy_snapshot["external_effects"], False)
        self.assertEqual(report.receipts[-1].runtime_provenance["outputs"]["checked"], False)
        self.assertIn("human_gate_explicit", report.checks)

    def test_cleared_fixture_maps_to_read_clear_and_no_follow_up(self) -> None:
        fixture = load_weekly_update_fixture(CLEARED_FIXTURE)

        report = adoption_report_from_fixture(fixture)

        self.assertEqual(report.status, "done")
        self.assertIsNone(report.current_stage_id)
        self.assertEqual(report.terminal_status, "done")
        self.assertEqual(
            [observation.stage_id for observation in report.observations],
            ["discover_weekly_artifact", "readback_blackboard_card", "route_follow_up"],
        )
        self.assertEqual(report.observations[1].outcome, "read_clear")
        self.assertEqual(report.observations[2].outcome, "no_follow_up")
        self.assertNotIn("suman_review_gate", {observation.stage_id for observation in report.observations})
        self.assertEqual(report.receipts[-1].runtime_provenance["outputs"]["shadow_only"], True)

    def test_receipts_are_deterministic_and_shadow_only(self) -> None:
        fixture = load_weekly_update_fixture(READY_FIXTURE)

        first = receipts_from_weekly_update(fixture)
        second = receipts_from_weekly_update(fixture)

        self.assertEqual([receipt.receipt_id for receipt in first], [receipt.receipt_id for receipt in second])
        for receipt in first:
            self.assertEqual(receipt.workflow_id, "jarvis_weekly_update_shadow")
            self.assertEqual(receipt.policy_snapshot["risk_class"], "read_only")
            self.assertTrue(receipt.policy_snapshot["shadow_only"])
            self.assertFalse(receipt.policy_snapshot["external_effects"])

    def test_ready_fixture_can_record_test_only_suman_read_clear(self) -> None:
        fixture = load_weekly_update_fixture(READY_FIXTURE)
        report = adoption_report_from_fixture(fixture)
        human_gate_receipt = report.receipts[-1]
        request = ActionRequest(
            action="weekly_read_clear",
            target_ref=f"fixture://{report.fixture_id}",
            arguments={
                "decision": "read_clear",
                "stage_id": "suman_review_gate",
                "receipt_id": human_gate_receipt.receipt_id,
            },
            risk_classes=(RiskClass.REVIEW_ONLY,),
            evidence_refs=(human_gate_receipt.receipt_id,),
        )

        approval = build_test_only_suman_approval(
            request,
            evidence_refs=request.evidence_refs,
            created_at="2026-05-31T12:00:00Z",
        )

        self.assertEqual(report.status, "waiting_on_human")
        self.assertEqual(approval.human_ref, "Suman(test)")
        self.assertTrue(approval.constraints["test_only"])
        self.assertEqual(approval.constraints["allowed_scope"], "fixtures/tests/local_review_packets")
        self.assertIn("external_send", approval.constraints["forbidden_live_effects"])


if __name__ == "__main__":
    unittest.main()
