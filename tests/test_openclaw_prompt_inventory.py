import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import PromptRegistry, StageType, load_workflow_file  # noqa: E402


WORKFLOW_EXPECTATIONS = {
    "ivy_jonah_editorial.yaml": {
        "accept_source_approval": {
            "policy.openclaw.review_only_human_gate",
            "lane.ivy_jonah_editorial",
            "stage.ivy_jonah.accept_source_approval",
        },
        "build_draft_package": {
            "identity.ivy_or_research",
            "policy.openclaw.editorial_public_boundary",
            "lane.ivy_jonah_editorial",
            "stage.ivy_jonah.build_draft_package",
        },
        "editor_review": {
            "identity.ivy_or_research",
            "identity.jonah_editor",
            "policy.openclaw.editorial_public_boundary",
            "lane.ivy_jonah_editorial",
            "stage.ivy_jonah.editor_review",
        },
        "revise_draft": {
            "identity.ivy_or_research",
            "policy.openclaw.editorial_public_boundary",
            "lane.ivy_jonah_editorial",
            "stage.ivy_jonah.revise_draft",
        },
        "validate_editorial_state": {
            "policy.openclaw.editorial_public_boundary",
            "lane.ivy_jonah_editorial",
            "stage.ivy_jonah.validate_editorial_state",
        },
        "p5_final_approval": {
            "policy.openclaw.review_only_human_gate",
            "lane.ivy_jonah_editorial",
            "stage.ivy_jonah.p5_final_approval",
        },
    },
    "jarvis_weekly_update_shadow.yaml": {
        "discover_weekly_artifact": {
            "policy.openclaw.read_only_shadow",
            "lane.jarvis_weekly_update_shadow",
            "stage.jarvis_weekly.discover_artifact",
        },
        "readback_blackboard_card": {
            "policy.openclaw.read_only_shadow",
            "lane.jarvis_weekly_update_shadow",
            "stage.jarvis_weekly.blackboard_readback",
        },
        "suman_review_gate": {
            "policy.openclaw.review_only_human_gate",
            "lane.jarvis_weekly_update_shadow",
            "stage.jarvis_weekly.suman_review_gate",
        },
        "route_follow_up": {
            "identity.jarvis_weekly_shadow_worker",
            "policy.openclaw.read_only_shadow",
            "lane.jarvis_weekly_update_shadow",
            "stage.jarvis_weekly.route_follow_up",
        },
    },
}


class OpenClawPromptInventoryTest(unittest.TestCase):
    def test_target_workflow_stages_have_resolvable_prompt_contracts(self) -> None:
        registry = PromptRegistry.load(ROOT / "prompts")

        for workflow_name, expected_by_stage in WORKFLOW_EXPECTATIONS.items():
            workflow = load_workflow_file(ROOT / "workflows" / workflow_name)
            stages = {stage.id: stage for stage in workflow.stages}
            self.assertEqual(set(stages), set(expected_by_stage))

            for stage_id, expected_prompt_ids in expected_by_stage.items():
                with self.subTest(workflow=workflow.id, stage_id=stage_id):
                    stage = stages[stage_id]
                    self.assertTrue(stage.prompt_refs, f"{stage_id} must not be prompt-anonymous")
                    actual_prompt_ids = {ref.id for ref in stage.prompt_refs}
                    self.assertEqual(actual_prompt_ids, expected_prompt_ids)

                    bundle = registry.resolve(stage.prompt_refs)
                    resolved_ids = {prompt.ref.id for prompt in bundle.prompts}
                    self.assertEqual(resolved_ids, expected_prompt_ids)
                    self.assertTrue(all(prompt.status == "active" for prompt in bundle.prompts))
                    self.assertTrue(all(prompt.content_hash.startswith("sha256:") for prompt in bundle.prompts))
                    self.assertEqual(
                        sum(1 for prompt in bundle.prompts if prompt.ref.kind == "policy"),
                        1,
                    )
                    self.assertEqual(
                        sum(1 for prompt in bundle.prompts if prompt.ref.kind == "lane"),
                        1,
                    )
                    self.assertEqual(
                        sum(1 for prompt in bundle.prompts if prompt.ref.kind == "stage"),
                        1,
                    )

    def test_system_actions_and_human_gates_are_prompt_bound_too(self) -> None:
        target_types = {StageType.SYSTEM_ACTION, StageType.HUMAN_GATE}

        for workflow_name in WORKFLOW_EXPECTATIONS:
            workflow = load_workflow_file(ROOT / "workflows" / workflow_name)
            for stage in workflow.stages:
                if stage.type in target_types:
                    with self.subTest(workflow=workflow.id, stage_id=stage.id):
                        self.assertTrue(stage.prompt_refs)
                        self.assertNotIn("prompt_context_exempt", stage.policy.keys() | stage.inputs.keys())

    def test_workflows_do_not_embed_local_openclaw_paths(self) -> None:
        for workflow_name in WORKFLOW_EXPECTATIONS:
            text = (ROOT / "workflows" / workflow_name).read_text(encoding="utf-8")
            with self.subTest(workflow=workflow_name):
                self.assertNotIn("/Users/", text)
                self.assertNotIn("/Users/sunny", text)
                self.assertNotIn("/Users/suman", text)

    def test_openclaw_import_manifest_uses_logical_uris_and_source_hashes(self) -> None:
        manifest_path = ROOT / "prompts" / "adapters" / "openclaw" / "imported-sources" / "v2026-06-01.yaml"
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = yaml.safe_load(handle)

        self.assertEqual(manifest["schema_version"], "adapter-prompt-import.v1")
        self.assertEqual(manifest["adapter_id"], "host.openclaw")
        self.assertGreaterEqual(len(manifest["sources"]), 5)

        mapped_prompt_ids = set()
        for source in manifest["sources"]:
            self.assertTrue(source["source_uri"].startswith("openclaw://"))
            self.assertTrue(source["source_hash"].startswith("sha256:"))
            self.assertEqual(len(source["source_hash"]), len("sha256:") + 64)
            self.assertNotIn("/Users/", source["source_uri"])
            mapped_prompt_ids.update(source["mapped_prompt_ids"])

        expected_mapped = {
            prompt_id
            for stage_prompts in WORKFLOW_EXPECTATIONS.values()
            for prompt_ids in stage_prompts.values()
            for prompt_id in prompt_ids
            if not prompt_id.startswith("policy.")
        }
        self.assertTrue(
            {f"{prompt_id}@1.0.0" for prompt_id in expected_mapped}.issubset(mapped_prompt_ids)
        )

    def test_openclaw_cutover_and_live_cargo_prompts_are_resolvable(self) -> None:
        registry = PromptRegistry.load(ROOT / "prompts")
        profiles = [
            [
                ("policy.openclaw.review_only_human_gate", "policy"),
                ("lane.jarvis_weekly_update_shadow", "lane"),
                ("stage.openclaw.cutover_review_artifact", "stage"),
            ],
            [
                ("identity.jarvis_weekly_shadow_worker", "identity"),
                ("policy.openclaw.read_only_shadow", "policy"),
                ("lane.jarvis_weekly_update_shadow", "lane"),
                ("stage.jarvis_weekly.improvement_cargo", "stage"),
            ],
        ]

        for refs in profiles:
            with self.subTest(refs=[prompt_id for prompt_id, _ in refs]):
                bundle = registry.resolve([script_ref(prompt_id, kind) for prompt_id, kind in refs])
                self.assertEqual({prompt.ref.id for prompt in bundle.prompts}, {prompt_id for prompt_id, _ in refs})
                self.assertTrue(bundle.prompt_bundle_digest.startswith("sha256:"))


def script_ref(prompt_id: str, kind: str):
    from agent_workflow_kernel import PromptRef

    return PromptRef(
        id=prompt_id,
        kind=kind,
        version="1.0.0",
        render_mode="yaml" if kind == "policy" else "markdown",
    )


if __name__ == "__main__":
    unittest.main()
