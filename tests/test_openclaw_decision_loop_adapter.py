import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))
sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

from agent_workflow_kernel import AdapterFamily, AdapterInvocation  # noqa: E402
from agent_workflow_kernel_openclaw import OpenClawBlackboardDecisionLoopAdapter  # noqa: E402


def invocation(operation: str) -> AdapterInvocation:
    return AdapterInvocation(
        invocation_id=f"test:decision-loop:{operation}",
        workflow_id="openclaw_cutover",
        instance_id="openclaw-test",
        stage_run_id=f"{operation}_stage",
        adapter_family=AdapterFamily.HOST,
        adapter_id="host.openclaw.blackboard_decision_loop",
        operation=operation,
    )


def write_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def scaffold_openclaw(root: Path) -> None:
    workspace_scripts = root / "workspace-main" / "scripts"
    write_script(
        workspace_scripts / "update_review_inbox.py",
        "import json\nprint(json.dumps({'ok': True, 'script': 'update_review_inbox'}))\n",
    )
    write_script(
        workspace_scripts / "ingest_agent_reviews.py",
        "\n".join(
            [
                "import json, sys",
                "print(json.dumps({'ok': True, 'applied': '--apply' in sys.argv, 'argv': sys.argv[1:]}))",
            ]
        ),
    )
    write_script(
        workspace_scripts / "agent_review_runner.py",
        "\n".join(
            [
                "import json, sys",
                "print(json.dumps({'ok': True, 'candidate': None, 'argv': sys.argv[1:]}))",
            ]
        ),
    )
    write_script(
        workspace_scripts / "publish_or_research_attention.py",
        "\n".join(
            [
                "import json, sys",
                "print(json.dumps({'ok': True, 'published': False, 'argv': sys.argv[1:]}))",
            ]
        ),
    )
    write_script(
        root / "scripts" / "run_blackboard_decision_ingester.sh",
        "#!/usr/bin/env bash\nprintf '{\"ok\": true, \"direct_loop\": true}\\n'\n",
    )


class OpenClawBlackboardDecisionLoopAdapterTest(unittest.TestCase):
    def test_refresh_ingest_dry_run_and_plan_wrap_existing_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "openclaw"
            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            scaffold_openclaw(root)
            adapter = OpenClawBlackboardDecisionLoopAdapter(root, vault_root=vault, created_at="2000-01-01T00:00:00Z")

            refresh = adapter.refresh_blackboard(invocation("refresh_blackboard"))
            ingest = adapter.ingest_decisions(invocation("ingest_decisions"))
            plan = adapter.plan_review_runner(invocation("plan_review_runner"))

            self.assertEqual(refresh.status, "succeeded")
            self.assertEqual(ingest.status, "succeeded")
            self.assertEqual(plan.status, "succeeded")
            ingest_json = ingest.runtime_provenance["outputs"]["command_result"]["parsed_json"]
            self.assertFalse(ingest_json["applied"])
            self.assertIn("--refresh-blackboard", ingest_json["argv"])
            self.assertIn("--validate", ingest_json["argv"])

    def test_publish_attention_wraps_existing_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "openclaw"
            scaffold_openclaw(root)
            adapter = OpenClawBlackboardDecisionLoopAdapter(root, created_at="2000-01-01T00:00:00Z")

            receipt = adapter.publish_attention(invocation("publish_attention"))

            self.assertEqual(receipt.status, "succeeded")
            parsed = receipt.runtime_provenance["outputs"]["command_result"]["parsed_json"]
            self.assertTrue(parsed["ok"])
            self.assertFalse(parsed["published"])
            self.assertIn("--if-present", parsed["argv"])
            self.assertIn("--validate", parsed["argv"])

    def test_ingest_apply_requires_explicit_allow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "openclaw"
            scaffold_openclaw(root)
            adapter = OpenClawBlackboardDecisionLoopAdapter(root, created_at="2000-01-01T00:00:00Z")

            blocked = adapter.ingest_decisions(invocation("ingest_decisions"), apply=True)
            allowed = adapter.ingest_decisions(invocation("ingest_decisions"), apply=True, allow_apply=True)

            self.assertEqual(blocked.status, "blocked")
            self.assertIn("allow_apply=True", blocked.summary)
            self.assertEqual(allowed.status, "succeeded")
            parsed = allowed.runtime_provenance["outputs"]["command_result"]["parsed_json"]
            self.assertTrue(parsed["applied"])

    def test_direct_loop_requires_dispatch_allow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "openclaw"
            scaffold_openclaw(root)
            adapter = OpenClawBlackboardDecisionLoopAdapter(root, created_at="2000-01-01T00:00:00Z")

            blocked = adapter.run_decision_loop(invocation("run_decision_loop"))
            allowed = adapter.run_decision_loop(invocation("run_decision_loop"), allow_agent_dispatch=True)

            self.assertEqual(blocked.status, "blocked")
            self.assertIn("allow_agent_dispatch=True", blocked.summary)
            self.assertEqual(allowed.status, "succeeded")
            parsed = allowed.runtime_provenance["outputs"]["command_result"]["parsed_json"]
            self.assertTrue(parsed["direct_loop"])


if __name__ == "__main__":
    unittest.main()
