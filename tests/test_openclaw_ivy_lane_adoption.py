import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

from agent_workflow_kernel_openclaw import (  # noqa: E402
    IVY_JONAH_ADOPTION_REPORT_SCHEMA,
    IVY_JONAH_WORKFLOW_ID,
    OpenClawMutationBlocked,
    adopt_ivy_jonah_fixture,
    ivy_jonah_fixture_from_mapping,
    load_ivy_jonah_fixture,
)


FIXTURES = ROOT / "fixtures" / "openclaw" / "ivy_jonah"


class OpenClawIvyLaneAdoptionTest(unittest.TestCase):
    def test_p3_approval_fixture_maps_full_ivy_jonah_shadow_path(self) -> None:
        fixture = load_ivy_jonah_fixture(FIXTURES / "p3_approval_to_p5_shadow.json")

        adoption = adopt_ivy_jonah_fixture(fixture)
        report = adoption.report.to_data()

        self.assertEqual(fixture.project_id, "agent-to-agent-communication-live")
        self.assertEqual(fixture.handoff_type, "ivy_writing_ops_p3_approved_to_p4")
        self.assertEqual(fixture.ivy_actor.agent_id, "ivy_writing_ops")
        self.assertEqual(fixture.jonah_actor.agent_id, "jonah_editor")
        self.assertEqual(fixture.mapping.work_ledger_ids.handoff_id, "handoff_shadow_p4_editor")
        self.assertEqual(
            [stage.stage_id for stage in adoption.stage_observations],
            [
                "accept_source_approval",
                "build_draft_package",
                "editor_review",
                "validate_editorial_state",
                "p5_final_approval",
            ],
        )
        self.assertEqual(adoption.stage_observations[2].adapter, "runtime.a2a")
        self.assertEqual(adoption.stage_observations[-1].status, "needs_human")
        self.assertEqual(adoption.stage_observations[-1].outcome, "approve_packet")
        self.assertEqual(report["schema"], IVY_JONAH_ADOPTION_REPORT_SCHEMA)
        self.assertEqual(report["workflow_id"], IVY_JONAH_WORKFLOW_ID)
        self.assertEqual(report["ready_for_shadow"], True)
        self.assertEqual(report["requires_human_gate"], True)
        self.assertEqual(report["public_publish_blocked"], True)
        self.assertIn("native_a2a_transcript", str(report["evidence_refs"]["transcript_refs"]))
        self.assertIn("Shadow takeover still needs live dual-run evidence", report["open_questions"][0])

    def test_p3_approval_receipts_are_kernel_receipts_with_read_only_policy(self) -> None:
        adoption = adopt_ivy_jonah_fixture(
            load_ivy_jonah_fixture(FIXTURES / "p3_approval_to_p5_shadow.json")
        )

        self.assertEqual(len(adoption.receipts), 5)
        self.assertEqual(adoption.receipts[0].workflow_id, IVY_JONAH_WORKFLOW_ID)
        self.assertEqual(adoption.receipts[0].stage_id, "accept_source_approval")
        self.assertEqual(adoption.receipts[0].runtime_provenance["operation"], "map_reference_host")
        self.assertEqual(adoption.receipts[0].runtime_provenance["outputs"]["read_only"], True)
        self.assertEqual(adoption.receipts[-1].status, "needs_human")
        self.assertEqual(adoption.receipts[-1].policy_snapshot["external_effects"], False)
        self.assertEqual(adoption.receipts[-1].policy_snapshot["public_publish_blocked"], True)
        self.assertIn("Public publish remains behind a human gate", adoption.receipts[-1].residual_risk or "")
        self.assertEqual(
            adoption.report.receipt_ids,
            tuple(receipt.receipt_id for receipt in adoption.receipts),
        )

    def test_p5_publish_decision_maps_to_local_packet_only_and_blocks_public_publish(self) -> None:
        adoption = adopt_ivy_jonah_fixture(
            load_ivy_jonah_fixture(FIXTURES / "p5_publish_decision_packet.json")
        )
        report = adoption.report.to_data()

        self.assertEqual(
            [stage.stage_id for stage in adoption.stage_observations],
            ["p5_final_approval"],
        )
        self.assertEqual(adoption.stage_observations[0].artifact_roles, ("publish_bundle", "browser_staging_plan"))
        self.assertEqual(adoption.stage_observations[0].status, "needs_human")
        self.assertEqual(report["ready_for_shadow"], True)
        self.assertEqual(report["requires_human_gate"], True)
        self.assertEqual(report["public_publish_blocked"], True)
        self.assertEqual(
            {packet["external_publish_performed"] for packet in report["evidence_refs"]["publish_packet_refs"]},
            {False},
        )
        self.assertIn("local publish-packet work", report["open_questions"][0])
        self.assertIn("local publish packet preparation only", report["residual_risk"])

    def test_adoption_report_json_is_deterministic(self) -> None:
        path = FIXTURES / "p3_approval_to_p5_shadow.json"
        fixture = load_ivy_jonah_fixture(path)
        with path.open("r", encoding="utf-8") as handle:
            raw_fixture = json.load(handle)

        first = adopt_ivy_jonah_fixture(fixture).report.to_json()
        second = adopt_ivy_jonah_fixture(raw_fixture).report.to_json()

        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["schema"], IVY_JONAH_ADOPTION_REPORT_SCHEMA)

    def test_exported_live_shape_accepts_path_only_transcript_refs(self) -> None:
        raw_fixture = {
            "lane": "ivy",
            "fixture_id": "openclaw-ivy-agent-to-agent-communication-live-6",
            "generated_at": "2026-06-01T00:29:41Z",
            "invocation": {"operation": "inspect_fixture"},
            "ivy": {
                "project": {
                    "project_id": "agent-to-agent-communication-live-6",
                    "title": "Agent-to-Agent Communication",
                    "gate": "P5",
                    "target_channel": "substack_medium",
                },
                "stages": [
                    {"stage": "P3", "next_action": "advance_to_p4"},
                    {"stage": "P5", "human_gate": True},
                ],
                "actors": {"ivy": "Ivy", "jonah": "Jonah", "human": "Suman"},
                "source_config": {
                    "forbidden_actions": ["publish", "send externally", "push", "deploy"],
                    "residual_risk": "Fixture data can drift from live OpenClaw state.",
                },
                "review_surfaces": [
                    {
                        "surface_id": "ivy_writing_ops_review_notes",
                        "kind": "obsidian_review_surface",
                        "status": "observable",
                    }
                ],
                "transcript_refs": [
                    {
                        "kind": "transcript",
                        "path": "workspace/agents/ivy_writing_ops/work_ledger_transcripts/work_6101-native-ivy-jonah-a2a.md",
                        "resolved_path": "/Users/sunny/.openclaw/workspace/agents/ivy_writing_ops/work_ledger_transcripts/work_6101-native-ivy-jonah-a2a.md",
                        "status": "observed",
                    }
                ],
                "publish_packet_refs": [
                    "/Users/sunny/.openclaw/workspace/agents/ivy_writing_ops/projects/agent-to-agent-communication-live-6/published.json"
                ],
                "publish_gate": {
                    "external_publish_allowed_in_source": True,
                    "exporter_action_allowed": False,
                    "read_only_shadow_may_not_publish": True,
                },
            },
            "mapping": {
                "lane_id": "ivy_writing_ops",
                "agent_id": "ivy_writing_ops",
                "host_ref": "oldmac",
                "work_ledger": {
                    "work_item_id": "work_6101b6f3049f4e24",
                    "handoff_id": "ivy-writing-ops-a2a-agent-communication-2026-05-30",
                },
                "surface_refs": [],
                "runtime_refs": [],
            },
            "artifacts": [],
        }

        adoption = adopt_ivy_jonah_fixture(raw_fixture)
        report = adoption.report.to_data()

        self.assertEqual(adoption.fixture.transcript_refs[0].transcript_id, "transcript:work_6101-native-ivy-jonah-a2a")
        self.assertEqual(report["ready_for_shadow"], True)
        self.assertEqual(report["public_publish_blocked"], True)
        self.assertEqual(
            {packet["external_publish_performed"] for packet in report["evidence_refs"]["publish_packet_refs"]},
            {False},
        )

    def test_mutating_fixture_operation_is_rejected_before_mapping(self) -> None:
        path = FIXTURES / "p3_approval_to_p5_shadow.json"
        with path.open("r", encoding="utf-8") as handle:
            raw_fixture = json.load(handle)
        raw_fixture["invocation"]["operation"] = "publish"

        with self.assertRaises(OpenClawMutationBlocked):
            ivy_jonah_fixture_from_mapping(raw_fixture)

    def test_dependency_direction_keeps_kernel_free_of_openclaw_adapter_imports(self) -> None:
        kernel_sources = (ROOT / "packages" / "kernel" / "agent_workflow_kernel").glob("*.py")

        offenders = [
            path.name
            for path in kernel_sources
            if "agent_workflow_kernel_openclaw" in path.read_text(encoding="utf-8")
        ]

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
