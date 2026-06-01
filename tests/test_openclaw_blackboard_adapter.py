import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

from agent_workflow_kernel import AdapterFamily, AdapterInvocation  # noqa: E402
from agent_workflow_kernel_openclaw import OpenClawBlackboardReviewAdapter  # noqa: E402


def write_refresh_stub(openclaw_root: Path, *, exits: int = 0) -> None:
    script = openclaw_root / "workspace-main" / "scripts" / "update_review_inbox.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "\n".join(
            [
                "import json, os, re, sys",
                "from pathlib import Path",
                f"sys.exit({exits})" if exits else "",
                "root = Path(__file__).resolve().parents[1]",
                "vault = Path(os.environ['OPENCLAW_OBSIDIAN_VAULT'])",
                "records = root / 'state' / 'artifact_outbox' / 'records'",
                "def safe_id(value):",
                "    return re.sub(r'[^A-Za-z0-9_.-]+', '-', value.strip()).strip('-').lower() or 'item'",
                "lines = ['# Blackboard', '', '## Decide', '']",
                "for path in sorted(records.glob('*.json')):",
                "    data = json.loads(path.read_text())",
                "    note = data.get('review_note') or data.get('draft_path') or str(path)",
                "    note_path = Path(note)",
                "    if note_path.is_absolute():",
                "        try: note = note_path.relative_to(vault).as_posix()",
                "        except ValueError: note = str(note_path)",
                "    item = 'artifact-' + safe_id(data.get('artifact_id') or path.stem)",
                "    lines.append(f\"- [ ] {data.get('title')} <!-- inbox-item:{item} -->\")",
                "    lines.append(f\"  - executive summary: {data.get('why')}\")",
                "    lines.append(f\"  - decision / next action: {data.get('next')}\")",
                "    lines.append(f\"  - owner: {data.get('owner')}\")",
                "    lines.append(f\"  - evidence: [{note}](<{note}>)\")",
                "vault.mkdir(parents=True, exist_ok=True)",
                "(vault / '01 Blackboard.md').write_text('\\n'.join(lines) + '\\n')",
                "print('review-inbox validation: OK')",
            ]
        ),
        encoding="utf-8",
    )


def invocation() -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id="test:blackboard:weekly",
        workflow_id="openclaw_cutover",
        instance_id="openclaw-test",
        stage_run_id="weekly_blackboard_pointer",
        adapter_family=AdapterFamily.SURFACE,
        adapter_id="surface.openclaw.blackboard_review",
        operation="publish_pointer",
    )


class OpenClawBlackboardReviewAdapterTest(unittest.TestCase):
    def test_publish_pointer_writes_record_refreshes_and_reads_back_blackboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw_root = root / "openclaw"
            vault_root = root / "vault"
            review_note = vault_root / "03 Agent Org" / "main" / "OpenClaw" / "Reviews" / "weekly.md"
            review_note.parent.mkdir(parents=True)
            review_note.write_text("# Weekly review\n", encoding="utf-8")
            write_refresh_stub(openclaw_root)

            adapter = OpenClawBlackboardReviewAdapter(
                openclaw_root=openclaw_root,
                vault_root=vault_root,
                created_at="2000-01-01T00:00:00Z",
            )
            receipt = adapter.publish_pointer(
                invocation(),
                {
                    "artifact_id": "awk-weekly-test",
                    "title": "AWK weekly test",
                    "review_note": str(review_note),
                    "why": "A review is ready.",
                    "next_action": "Check one box.",
                },
            )

            self.assertEqual(receipt.status, "succeeded")
            outputs = receipt.runtime_provenance["outputs"]
            record_path = Path(outputs["record_path"])
            self.assertTrue(record_path.exists())
            self.assertEqual(json.loads(record_path.read_text())["artifact_id"], "awk-weekly-test")
            self.assertTrue(outputs["readback"]["found"])
            blackboard = (vault_root / "01 Blackboard.md").read_text(encoding="utf-8")
            self.assertIn("<!-- inbox-item:artifact-awk-weekly-test -->", blackboard)
            self.assertIn("03 Agent Org/main/OpenClaw/Reviews/weekly.md", blackboard)

    def test_publish_pointer_blocks_when_review_note_is_outside_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw_root = root / "openclaw"
            vault_root = root / "vault"
            vault_root.mkdir()
            outside = root / "outside.md"
            outside.write_text("# outside\n", encoding="utf-8")
            write_refresh_stub(openclaw_root)

            adapter = OpenClawBlackboardReviewAdapter(
                openclaw_root=openclaw_root,
                vault_root=vault_root,
                created_at="2000-01-01T00:00:00Z",
            )
            receipt = adapter.publish_pointer(
                invocation(),
                {
                    "artifact_id": "awk-weekly-test",
                    "title": "AWK weekly test",
                    "review_note": str(outside),
                    "why": "A review is ready.",
                    "next_action": "Check one box.",
                },
            )

            self.assertEqual(receipt.status, "blocked")
            self.assertEqual(receipt.runtime_provenance["outputs"]["error"]["error_class"], "review_note_outside_vault")

    def test_publish_pointer_blocks_when_refresh_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            openclaw_root = root / "openclaw"
            vault_root = root / "vault"
            review_note = vault_root / "reviews" / "weekly.md"
            review_note.parent.mkdir(parents=True)
            review_note.write_text("# Weekly review\n", encoding="utf-8")
            write_refresh_stub(openclaw_root, exits=2)

            adapter = OpenClawBlackboardReviewAdapter(
                openclaw_root=openclaw_root,
                vault_root=vault_root,
                created_at="2000-01-01T00:00:00Z",
            )
            receipt = adapter.publish_pointer(
                invocation(),
                {
                    "artifact_id": "awk-weekly-test",
                    "title": "AWK weekly test",
                    "review_note": str(review_note),
                    "why": "A review is ready.",
                    "next_action": "Check one box.",
                },
            )

            self.assertEqual(receipt.status, "blocked")
            outputs = receipt.runtime_provenance["outputs"]
            self.assertEqual(outputs["refresh"]["status"], "blocked")
            self.assertFalse(outputs["readback"]["found"])


if __name__ == "__main__":
    unittest.main()
