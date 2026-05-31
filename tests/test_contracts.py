import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterInvocation,
    StageDef,
    StageType,
    Transition,
    WorkflowDef,
    to_plain_data,
)


class ContractSmokeTest(unittest.TestCase):
    def test_workflow_contract_serializes_to_plain_data(self) -> None:
        workflow = WorkflowDef(
            id="smoke",
            version="0.1.0",
            name="Smoke Workflow",
            stages=(
                StageDef(
                    id="draft",
                    type=StageType.AGENT_WORK,
                    adapter="runtime.fake",
                    outcomes=("done",),
                ),
            ),
            transitions=(Transition(from_stage="draft", on="done", terminal="done"),),
        )

        data = to_plain_data(workflow)

        self.assertEqual(data["stages"][0]["type"], "agent_work")
        self.assertEqual(data["transitions"][0]["terminal"], "done")

    def test_adapter_invocation_uses_portable_family(self) -> None:
        invocation = AdapterInvocation(
            invocation_id="invoke-1",
            workflow_id="smoke",
            instance_id="instance-1",
            stage_run_id="run-1",
            adapter_family=AdapterFamily.RUNTIME,
            adapter_id="runtime.fake",
            operation="execute",
        )

        data = to_plain_data(invocation)

        self.assertEqual(data["adapter_family"], "runtime")


if __name__ == "__main__":
    unittest.main()
