import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterInvocation,
    LocalMarkdownHumanReviewSurfaceAdapter,
)


def load_script():
    path = ROOT / "scripts" / "openclaw_auto_review_packet.py"
    spec = importlib.util.spec_from_file_location("openclaw_auto_review_packet_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = load_script()


class OpenClawAutoReviewPacketTest(unittest.TestCase):
    def test_auto_review_packet_marks_and_ingests_local_review_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packet_dir = root / "packet"
            output_dir = root / "auto-review"
            note = self._make_note(packet_dir, lane="weekly", stage_id="suman_review_gate")
            self._write_lane(packet_dir, "weekly", [note])
            self._write_lane(packet_dir, "ivy", [])
            for name in ("shadow_report.json", "adoption_report.json", "lane_report.json"):
                path = packet_dir / "lanes" / "weekly" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"ok": true}\n', encoding="utf-8")

            summary = script.auto_review_packet(packet_dir=packet_dir, output_dir=output_dir)

            self.assertEqual(summary["status"], "reviewed")
            self.assertEqual(summary["reviewed_notes"], 1)
            decision = summary["lanes"]["weekly"]["decisions"][0]
            self.assertEqual(decision["review_decision"], "read_clear")
            self.assertEqual(decision["reviewer_human_ref"], "Suman(test automated reviewer)")
            self.assertFalse(decision["mutation_permission_granted"])
            self.assertTrue(Path(decision["review_receipt_path"]).exists())
            self.assertIn("- [x] `read_clear`", Path(note["note_path"]).read_text(encoding="utf-8"))
            self.assertTrue((output_dir / "summary.json").exists())
            self.assertTrue((output_dir / "README.md").exists())

    def _make_note(self, packet_dir: Path, *, lane: str, stage_id: str) -> dict[str, object]:
        adapter = LocalMarkdownHumanReviewSurfaceAdapter(
            packet_dir / "review_notes" / lane,
            canonical_surface=f"local_markdown_{lane}_review",
        )
        invocation = AdapterInvocation(
            invocation_id=f"review:{lane}:{stage_id}",
            workflow_id="jarvis_weekly_update_shadow",
            instance_id="fixture-weekly",
            stage_run_id=stage_id,
            adapter_family=AdapterFamily.SURFACE,
            adapter_id=adapter.adapter_id,
            operation="publish",
            input_ref="fixture",
            context_packet_ref="context",
            idempotency_key=f"{lane}:{stage_id}",
        )
        result = adapter.publish(
            invocation,
            {
                "title": "Weekly review",
                "stage_id": stage_id,
                "allowed_decisions": ("read_clear", "follow_up_requested", "defer", "blocked"),
                "requested_action": "Record a local review decision only.",
                "exact_action": "Record a local review decision only.",
                "action_fingerprint": "sha256:test-weekly",
                "test_only": True,
                "non_live": True,
            },
        )
        note_path = Path(result.outputs["note_path"])
        return {
            "stage_id": stage_id,
            "lane_id": lane,
            "note_path": str(note_path),
            "allowed_decisions": ["read_clear", "follow_up_requested", "defer", "blocked"],
            "exact_action": "Record a local review decision only.",
            "action_fingerprint": "sha256:test-weekly",
            "test_only": True,
            "non_live": True,
            "readback_receipt_id": "receipt:test-readback",
            "readback": {"receipt_id": "receipt:test-readback"},
        }

    def _write_lane(self, packet_dir: Path, lane: str, notes: list[dict[str, object]]) -> None:
        path = packet_dir / "lanes" / lane / "review_notes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(notes, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
