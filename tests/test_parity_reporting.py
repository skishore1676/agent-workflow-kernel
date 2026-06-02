import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    PARITY_REPORT_SCHEMA,
    compare_receipts,
    load_parity_fixture,
    report_from_fixture,
)


PARITY_FIXTURES = ROOT / "fixtures" / "parity"


class ParityReportingTest(unittest.TestCase):
    def test_equivalent_receipts_produce_stable_success_report(self) -> None:
        receipt = {
            "receipt_id": "receipt-1",
            "kind": "stage_run",
            "status": "succeeded",
            "summary": "Done.",
            "runtime_provenance": {
                "adapter_id": "runtime.fake",
                "metadata": {
                    "attempt": 1,
                    "readback": True,
                },
            },
        }

        report = compare_receipts(receipt, dict(reversed(list(receipt.items()))))
        data = report.to_data()

        self.assertEqual(report.schema, PARITY_REPORT_SCHEMA)
        self.assertEqual(report.status, "equivalent")
        self.assertEqual(data["summary"]["different"], 0)
        self.assertEqual(data["summary"]["missing"], 0)
        self.assertEqual(data["summary"]["extra"], 0)
        self.assertEqual(
            [field["path"] for field in data["fields"]["equivalent"]],
            sorted(field["path"] for field in data["fields"]["equivalent"]),
        )

    def test_bumblebee_fixture_names_documented_delta(self) -> None:
        fixture = load_parity_fixture(PARITY_FIXTURES / "bumblebee_quality_review.json")

        report = report_from_fixture(fixture)
        data = report.to_data()

        self.assertEqual(report.status, "different")
        self.assertEqual(
            data["fields"]["different"],
            [
                {
                    "path": "$.runtime_provenance.adapter_id",
                    "expected": "openclaw.work_ledger.bumblebee",
                    "actual": "kernel.runtime.quality_review",
                }
            ],
        )
        self.assertEqual(data["summary"]["missing"], 0)
        self.assertEqual(data["summary"]["extra"], 0)
        self.assertEqual(data["summary"]["ignored"], 3)
        self.assertGreater(data["summary"]["equivalent"], 20)

    def test_human_gate_surface_readback_fixture_is_equivalent_after_ignored_refs(self) -> None:
        fixture = load_parity_fixture(PARITY_FIXTURES / "human_gate_surface_readback.json")

        report = report_from_fixture(fixture)
        data = report.to_data()

        self.assertEqual(report.status, "equivalent")
        self.assertEqual(data["summary"]["different"], 0)
        self.assertEqual(data["summary"]["missing"], 0)
        self.assertEqual(data["summary"]["extra"], 0)
        self.assertEqual(
            [field["path"] for field in data["fields"]["ignored"]],
            [
                "$.runtime_provenance.metadata.host_surface_ref",
                "$.runtime_provenance.metadata.kernel_surface_ref",
            ],
        )

    def test_missing_extra_and_ignored_fields_are_classified(self) -> None:
        expected = {
            "receipt_id": "receipt-1",
            "status": "succeeded",
            "summary": None,
            "runtime_provenance": {
                "adapter_id": "host.adapter",
                "session_key": "host-local-only",
            },
            "policy_snapshot": {
                "denied": ["external_send"],
            },
        }
        actual = {
            "receipt_id": "receipt-1",
            "status": "failed",
            "runtime_provenance": {
                "adapter_id": "kernel.adapter",
                "session_key": "kernel-local-only",
            },
            "policy_snapshot": {
                "denied": ["external_send"],
                "granted": ["readback"],
            },
        }

        report = compare_receipts(
            expected,
            actual,
            ignored_fields={"$.runtime_provenance.session_key": "session ids are runtime-local"},
        )
        data = report.to_data()

        self.assertEqual(
            data["fields"]["equivalent"],
            [
                {"path": "$.policy_snapshot.denied[0]", "expected": "external_send", "actual": "external_send"},
                {"path": "$.receipt_id", "expected": "receipt-1", "actual": "receipt-1"},
            ],
        )
        self.assertEqual(
            data["fields"]["different"],
            [
                {"path": "$.runtime_provenance.adapter_id", "expected": "host.adapter", "actual": "kernel.adapter"},
                {"path": "$.status", "expected": "succeeded", "actual": "failed"},
            ],
        )
        self.assertEqual(data["fields"]["missing"], [{"path": "$.summary", "expected": None}])
        self.assertEqual(data["fields"]["extra"], [{"path": "$.policy_snapshot.granted[0]", "actual": "readback"}])
        self.assertEqual(
            data["fields"]["ignored"],
            [
                {
                    "path": "$.runtime_provenance.session_key",
                    "expected": "host-local-only",
                    "actual": "kernel-local-only",
                    "reason": "session ids are runtime-local",
                }
            ],
        )

    def test_report_json_is_deterministic_for_shuffled_input(self) -> None:
        expected = {
            "z": 3,
            "a": {
                "b": 1,
                "a": 0,
            },
            "list": [
                {"b": 2, "a": 1},
            ],
        }
        actual = {
            "list": [
                {"a": 1, "b": 2},
            ],
            "a": {
                "a": 0,
                "b": 1,
            },
            "z": 3,
        }

        first = compare_receipts(expected, actual).to_json()
        second = compare_receipts(actual, expected).to_json()

        self.assertEqual(first, second)
        loaded = json.loads(first)
        self.assertEqual(loaded["status"], "equivalent")
        self.assertEqual(
            [field["path"] for field in loaded["fields"]["equivalent"]],
            ["$.a.a", "$.a.b", "$.list[0].a", "$.list[0].b", "$.z"],
        )


if __name__ == "__main__":
    unittest.main()
