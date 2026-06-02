import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import StageType  # noqa: E402
from agent_workflow_kernel.dsl import (  # noqa: E402
    _load_simple_yaml,
    load_workflow_yaml,
    workflow_from_mapping,
    workflow_to_canonical_json,
    workflow_to_canonical_json_bytes,
)
from agent_workflow_kernel.validation import WorkflowValidationError  # noqa: E402


VALID_WORKFLOW_YAML = """
schema: workflow.kernel.v1
workflow:
  id: portable_review
  version: 0.1.0
  name: Portable Review
  owner: local_fixture
  description: Generic producer/reviewer flow.
inputs:
  required:
    - work_item
  optional:
    - acceptance_criteria
defaults:
  policy_class: read_only_review
  lease:
    seconds: 300
  retry:
    max_attempts: 1
actors:
  producer:
    adapter: runtime.fake
    role: producer
    lease:
      seconds: 600
  reviewer:
    adapter: runtime.fake
    role: reviewer
stages:
  - id: prepare_packet
    type: system_action
    adapter: lane.generic.prepare_packet
    inputs:
      work_item: input.work_item
    outputs:
      artifacts:
        - role: review_packet
          required: true
    lease:
      seconds: 120
    outcomes: [ready, blocked]
  - id: review_loop
    type: a2a_review_loop
    adapter: runtime.a2a
    actors:
      producer: actors.producer
      reviewer: actors.reviewer
    inputs:
      packet: artifacts.prepare_packet.review_packet
    budget:
      max_questions: 3
      max_revision_turns: 2
    outcomes: [pass, refine, block, needs_human]
  - id: operator_gate
    type: human_gate
    adapter: surface.operator_review
    policy:
      class: external_send
      requires_explicit_approval: true
    inputs:
      verdict: receipts.review_loop
    outcomes: [approval_granted, approval_denied]
transitions:
  - from: prepare_packet
    on: ready
    to: review_loop
  - from: prepare_packet
    on: blocked
    terminal: blocked
  - from: review_loop
    on: pass
    terminal: done
  - from: review_loop
    on: refine
    to: prepare_packet
    guard: within_revision_budget
  - from: review_loop
    on: block
    terminal: blocked
  - from: review_loop
    on: needs_human
    to: operator_gate
  - from: operator_gate
    on: approval_granted
    terminal: done
  - from: operator_gate
    on: approval_denied
    terminal: policy_denied
"""


class CoreSchemaDslTest(unittest.TestCase):
    def test_loads_valid_yaml_into_workflow_def(self) -> None:
        workflow = load_workflow_yaml(VALID_WORKFLOW_YAML)

        self.assertEqual(workflow.id, "portable_review")
        self.assertEqual(workflow.schema, "workflow.kernel.v1")
        self.assertEqual(workflow.stages[0].type, StageType.SYSTEM_ACTION)
        self.assertEqual(workflow.defaults["lease"]["seconds"], 300)
        self.assertEqual(workflow.actors["producer"]["lease"]["seconds"], 600)
        self.assertEqual(workflow.stages[0].lease["seconds"], 120)
        self.assertEqual(workflow.stages[1].adapter, "runtime.a2a")
        self.assertEqual(workflow.transitions[3].guard, "within_revision_budget")
        self.assertEqual(workflow.transitions[-1].terminal, "policy_denied")

    def test_stdlib_yaml_fallback_strips_quoted_mapping_keys(self) -> None:
        quoted = VALID_WORKFLOW_YAML.replace("    on:", '    "on":')

        parsed = _load_simple_yaml(quoted)
        workflow = workflow_from_mapping(parsed)

        self.assertEqual(workflow.transitions[0].on, "ready")
        self.assertEqual(workflow.transitions[-1].on, "approval_denied")

    def test_rejects_unknown_stage_type(self) -> None:
        invalid = VALID_WORKFLOW_YAML.replace("type: system_action", "type: bespoke_magic")

        with self.assertRaisesRegex(WorkflowValidationError, "unknown stage type"):
            load_workflow_yaml(invalid)

    def test_rejects_duplicate_stage_id(self) -> None:
        invalid = VALID_WORKFLOW_YAML.replace("id: review_loop", "id: prepare_packet")

        with self.assertRaisesRegex(WorkflowValidationError, "duplicate stage id"):
            load_workflow_yaml(invalid)

    def test_rejects_missing_transition_target(self) -> None:
        invalid = VALID_WORKFLOW_YAML.replace("to: operator_gate", "to: missing_gate", 1)

        with self.assertRaisesRegex(WorkflowValidationError, "unknown target stage"):
            load_workflow_yaml(invalid)

    def test_rejects_transition_for_undeclared_outcome(self) -> None:
        invalid = VALID_WORKFLOW_YAML.replace("on: ready", "on: skipped", 1)

        with self.assertRaisesRegex(WorkflowValidationError, "not declared"):
            load_workflow_yaml(invalid)

    def test_rejects_duplicate_transition_keys(self) -> None:
        invalid = VALID_WORKFLOW_YAML.replace(
            "on: blocked\n    terminal: blocked",
            "on: ready\n    terminal: blocked",
            1,
        )

        with self.assertRaisesRegex(WorkflowValidationError, "duplicate transition"):
            load_workflow_yaml(invalid)

    def test_rejects_unknown_transition_guard(self) -> None:
        invalid = VALID_WORKFLOW_YAML.replace("guard: within_revision_budget", "guard: typo_guard")

        with self.assertRaisesRegex(WorkflowValidationError, "unknown transition guard"):
            load_workflow_yaml(invalid)

    def test_rejects_invalid_lease_shape(self) -> None:
        invalid = VALID_WORKFLOW_YAML.replace("seconds: 120", "seconds: 0", 1)

        with self.assertRaisesRegex(WorkflowValidationError, "lease.seconds"):
            load_workflow_yaml(invalid)

    def test_rejects_unknown_terminal_status(self) -> None:
        invalid = VALID_WORKFLOW_YAML.replace("terminal: done", "terminal: shipped", 1)

        with self.assertRaisesRegex(WorkflowValidationError, "unknown terminal status"):
            load_workflow_yaml(invalid)

    def test_rejects_missing_required_top_level_section(self) -> None:
        invalid = VALID_WORKFLOW_YAML.replace("inputs:\n  required:", "declared_inputs:\n  required:")

        with self.assertRaisesRegex(WorkflowValidationError, "missing required"):
            load_workflow_yaml(invalid)

    def test_canonicalization_is_deterministic(self) -> None:
        workflow_a = load_workflow_yaml(VALID_WORKFLOW_YAML)
        workflow_b = load_workflow_yaml(VALID_WORKFLOW_YAML)

        canonical_a = workflow_to_canonical_json(workflow_a)
        canonical_b = workflow_to_canonical_json(workflow_b)

        self.assertEqual(canonical_a, canonical_b)
        self.assertEqual(canonical_a.encode("utf-8"), workflow_to_canonical_json_bytes(workflow_b))
        self.assertIn('"adapter":"lane.generic.prepare_packet"', canonical_a)


if __name__ == "__main__":
    unittest.main()
