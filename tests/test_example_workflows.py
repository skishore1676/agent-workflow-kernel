import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    PromptRegistry,
    StageDef,
    StageType,
    Transition,
    WorkflowDef,
    load_workflow_file,
    to_plain_data,
)


WORKFLOW_DIR = ROOT / "workflows"
VALIDATION_REPORT = ROOT / "fixtures" / "example_workflow_validation_report.json"

EXPECTED_WORKFLOWS = {
    "bumblebee_quality_review",
    "jarvis_weekly_update_shadow",
    "ivy_jonah_editorial",
    "trading_research_gate",
    "radhe_review_pipeline",
    "deterministic_system_action",
}

CORE_CAPABILITIES = {
    "versioned_workflow_def",
    "typed_stage_run",
    "artifact_refs",
    "immutable_receipts",
    "human_gate",
    "system_action",
    "policy_gates",
}

FORBIDDEN_PORTABILITY_TOKENS = (
    "/Users/",
    "oldmac",
    "sessions_send",
    "sessions_spawn",
    "broker.place_order",
    "broker.cancel_order",
    "launchd.",
)

ADAPTER_REF = re.compile(r"^(runtime|surface|host|lane|human)\.[a-z0-9_.-]+$")
IVY_JONAH_EXECUTABLE_PROMPT_STAGES = {
    "build_draft_package": {
        "identities": {"identity.ivy_or_research"},
        "policy": "policy.openclaw.editorial_public_boundary",
        "stage": "stage.ivy_jonah.build_draft_package",
    },
    "editor_review": {
        "identities": {"identity.ivy_or_research", "identity.jonah_editor"},
        "policy": "policy.openclaw.editorial_public_boundary",
        "stage": "stage.ivy_jonah.editor_review",
    },
    "revise_draft": {
        "identities": {"identity.ivy_or_research"},
        "policy": "policy.openclaw.editorial_public_boundary",
        "stage": "stage.ivy_jonah.revise_draft",
    },
}


def load_workflows() -> dict[str, dict[str, Any]]:
    workflows: dict[str, dict[str, Any]] = {}
    for path in sorted(WORKFLOW_DIR.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        workflows[data["workflow"]["id"]] = data
    return workflows


def stage_by_id(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {stage["id"]: stage for stage in workflow["stages"]}


def transitions_from(workflow: dict[str, Any], stage_id: str) -> list[dict[str, Any]]:
    return [transition for transition in workflow["transitions"] if transition["from"] == stage_id]


def workflow_to_contract(data: dict[str, Any]) -> WorkflowDef:
    """Temporary fixture-shape bridge until the DSL compiler lands."""

    stages = tuple(
        StageDef(
            id=stage["id"],
            type=StageType(stage["type"]),
            adapter=stage["adapter"],
            actors=stage.get("actors", {}),
            inputs=stage.get("inputs", {}),
            outputs=stage.get("outputs", {}),
            policy=stage.get("policy", {}),
            budget=stage.get("budget", {}),
            retry=stage.get("retry", {}),
            outcomes=tuple(stage["outcomes"]),
        )
        for stage in data["stages"]
    )
    transitions = tuple(
        Transition(
            from_stage=transition["from"],
            on=transition["on"],
            to_stage=transition.get("to"),
            terminal=transition.get("terminal"),
            guard=transition.get("guard"),
        )
        for transition in data["transitions"]
    )
    return WorkflowDef(
        schema=data["schema"],
        id=data["workflow"]["id"],
        version=data["workflow"]["version"],
        name=data["workflow"]["name"],
        owner=data["workflow"].get("owner"),
        description=data["workflow"].get("description"),
        inputs=data.get("inputs", {}),
        defaults=data.get("defaults", {}),
        actors=data.get("actors", {}),
        compatibility=data.get("compatibility", {}),
        stages=stages,
        transitions=transitions,
    )


class ExampleWorkflowFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflows = load_workflows()

    def test_all_expected_workflow_fixtures_exist(self) -> None:
        self.assertEqual(set(self.workflows), EXPECTED_WORKFLOWS)

    def test_current_contracts_accept_fixture_shape_until_dsl_loader_lands(self) -> None:
        for workflow_id, data in self.workflows.items():
            with self.subTest(workflow_id=workflow_id):
                contract = workflow_to_contract(data)
                plain = to_plain_data(contract)

                self.assertEqual(plain["schema"], "workflow.kernel.v1")
                self.assertEqual(plain["id"], workflow_id)
                self.assertGreaterEqual(len(plain["stages"]), 3)
                self.assertGreaterEqual(len(plain["transitions"]), len(plain["stages"]))

    def test_workflow_graphs_are_static_and_valid(self) -> None:
        for workflow_id, data in self.workflows.items():
            with self.subTest(workflow_id=workflow_id):
                self.assertEqual(data["schema"], "workflow.kernel.v1")
                self.assertIn("version", data["workflow"])
                self.assertIn("name", data["workflow"])
                self.assertIn("required", data["inputs"])

                stages = stage_by_id(data)
                self.assertEqual(len(stages), len(data["stages"]))

                for stage in data["stages"]:
                    StageType(stage["type"])
                    self.assertTrue(stage["outcomes"])
                    self.assertRegex(stage["adapter"], ADAPTER_REF)

                for transition in data["transitions"]:
                    source = transition["from"]
                    self.assertIn(source, stages)
                    self.assertIn(transition["on"], stages[source]["outcomes"])
                    self.assertNotEqual("to" in transition, "terminal" in transition)
                    if "to" in transition:
                        self.assertIn(transition["to"], stages)

    def test_fixtures_remain_portable_and_adapter_bound(self) -> None:
        for workflow_id, data in self.workflows.items():
            with self.subTest(workflow_id=workflow_id):
                rendered = json.dumps(data, sort_keys=True)
                for token in FORBIDDEN_PORTABILITY_TOKENS:
                    self.assertNotIn(token, rendered)

                for actor in data.get("actors", {}).values():
                    self.assertRegex(actor["adapter"], ADAPTER_REF)

    def test_validation_report_maps_every_example_to_kernel_capabilities(self) -> None:
        with VALIDATION_REPORT.open("r", encoding="utf-8") as handle:
            report = json.load(handle)

        self.assertEqual(report["schema"], "workflow.kernel.validation-report.v1")
        self.assertEqual(set(report["workflow_ids"]), EXPECTED_WORKFLOWS)
        self.assertTrue(CORE_CAPABILITIES.issubset(set(report["core_capabilities"])))
        self.assertEqual(
            {entry["workflow_id"] for entry in report["workflows"]},
            EXPECTED_WORKFLOWS,
        )

        for entry in report["workflows"]:
            self.assertGreaterEqual(len(entry["proves"]), 3)
            self.assertGreaterEqual(len(entry["acceptance_checks"]), 3)
            self.assertTrue(entry["must_not_depend_on"])

    def test_trading_research_gate_forbids_live_execution(self) -> None:
        workflow = self.workflows["trading_research_gate"]
        capability_policy = workflow["defaults"]["capability_policy"]
        forbidden = set(capability_policy["forbidden"])

        self.assertTrue(
            {
                "place_order",
                "cancel_order",
                "modify_order",
                "transfer_money",
                "change_broker_auth",
                "enable_live_strategy",
            }.issubset(forbidden)
        )

        rendered = json.dumps(workflow, sort_keys=True)
        for forbidden_action in forbidden:
            self.assertIn(forbidden_action, rendered)

        for stage in workflow["stages"]:
            self.assertNotIn("broker", stage["adapter"])
            self.assertNotEqual(stage["policy"].get("class"), "money_or_broker_action")

        decision_stage = stage_by_id(workflow)["human_research_decision"]
        self.assertTrue(decision_stage["policy"]["execution_requires_separate_workflow"])

    def test_ivy_editorial_fixture_blocks_stale_review(self) -> None:
        workflow = self.workflows["ivy_jonah_editorial"]
        validate_stage = stage_by_id(workflow)["validate_editorial_state"]
        self.assertIn("stale_review", validate_stage["outcomes"])

        stale_transition = [
            transition
            for transition in transitions_from(workflow, "validate_editorial_state")
            if transition["on"] == "stale_review"
        ]
        self.assertEqual(stale_transition, [{"from": "validate_editorial_state", "on": "stale_review", "terminal": "blocked"}])

        final_gate = stage_by_id(workflow)["p5_final_approval"]
        self.assertEqual(final_gate["type"], StageType.HUMAN_GATE.value)
        self.assertFalse(final_gate["policy"]["external_publish_allowed"])

    def test_ivy_jonah_executable_agent_stages_resolve_prompt_refs(self) -> None:
        workflow = load_workflow_file(WORKFLOW_DIR / "ivy_jonah_editorial.yaml")
        registry = PromptRegistry.load(ROOT / "prompts")

        executable_stages = {
            stage.id: stage
            for stage in workflow.stages
            if stage.type in (StageType.AGENT_WORK, StageType.A2A_REVIEW_LOOP)
        }
        self.assertEqual(
            set(executable_stages),
            set(IVY_JONAH_EXECUTABLE_PROMPT_STAGES),
        )

        for stage_id, stage in executable_stages.items():
            with self.subTest(stage_id=stage_id):
                self.assertTrue(stage.prompt_refs, f"{stage_id} must declare prompt_refs")
                bundle = registry.resolve(stage.prompt_refs)
                prompt_ids = {prompt.ref.id for prompt in bundle.prompts}
                prompt_kinds = {prompt.ref.kind for prompt in bundle.prompts}

                expected = IVY_JONAH_EXECUTABLE_PROMPT_STAGES[stage_id]
                self.assertTrue(expected["identities"].issubset(prompt_ids))
                self.assertIn(expected["policy"], prompt_ids)
                self.assertIn("lane.ivy_jonah_editorial", prompt_ids)
                self.assertIn(expected["stage"], prompt_ids)
                self.assertTrue({"identity", "policy", "lane", "stage"}.issubset(prompt_kinds))

    def test_radhe_pipeline_represents_long_running_resume_states(self) -> None:
        workflow = self.workflows["radhe_review_pipeline"]
        stages = stage_by_id(workflow)

        self.assertEqual(stages["wait_for_schedule"]["type"], StageType.WAIT_SCHEDULE.value)
        self.assertEqual(stages["recover_pipeline_state"]["type"], StageType.RECOVERY.value)
        self.assertIn("running", stages["run_or_resume_pipeline"]["outcomes"])
        self.assertIn("retry_needed", stages["run_or_resume_pipeline"]["outcomes"])

        recovery_terminals = {
            transition["terminal"]
            for transition in transitions_from(workflow, "recover_pipeline_state")
            if "terminal" in transition
        }
        self.assertTrue({"waiting_on_schedule", "blocked"}.issubset(recovery_terminals))

    def test_deterministic_apply_is_between_exact_approval_and_readback(self) -> None:
        workflow = self.workflows["deterministic_system_action"]
        stages = stage_by_id(workflow)

        approval_stage = stages["approval"]
        self.assertEqual(approval_stage["type"], StageType.HUMAN_GATE.value)
        self.assertTrue(approval_stage["policy"]["requires_explicit_approval"])
        self.assertEqual(approval_stage["policy"]["binds_to"], "artifacts.dry_run.dry_run_plan")

        apply_stage = stages["apply_action"]
        self.assertTrue(apply_stage["policy"]["requires_prior_approval"])
        self.assertEqual(apply_stage["policy"]["approval_ref"], "receipts.approval")
        self.assertEqual(
            apply_stage["policy"]["apply_fingerprint"],
            approval_stage["policy"]["approval_fingerprint"],
        )

        approval_to_apply = [
            transition
            for transition in transitions_from(workflow, "approval")
            if transition.get("to") == "apply_action"
        ]
        self.assertEqual(approval_to_apply, [{"from": "approval", "on": "approval_granted", "to": "apply_action"}])

        apply_to_readback = [
            transition
            for transition in transitions_from(workflow, "apply_action")
            if transition.get("to") == "readback_verify"
        ]
        self.assertEqual(apply_to_readback, [{"from": "apply_action", "on": "applied", "to": "readback_verify"}])

        verified_done = [
            transition
            for transition in transitions_from(workflow, "readback_verify")
            if transition["on"] == "verified"
        ]
        self.assertEqual(verified_done, [{"from": "readback_verify", "on": "verified", "terminal": "done"}])


if __name__ == "__main__":
    unittest.main()
