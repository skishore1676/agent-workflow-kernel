import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    ActionRequest,
    ApprovalDecision,
    GateDecision,
    HardGate,
    HumanApprovalReceipt,
    PolicyEngine,
    RiskClass,
    action_fingerprint,
    fingerprint_request,
    validate_approval,
)


class PolicyEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PolicyEngine()

    def test_read_only_action_is_allowed_with_receipt(self) -> None:
        gate = self.engine.evaluate(
            ActionRequest(
                action="inspect_logs",
                target_ref="file://logs/runtime.log",
                risk_classes=(RiskClass.READ_ONLY,),
            )
        )

        self.assertEqual(gate.decision, GateDecision.ALLOW_WITH_RECEIPT.value)
        self.assertEqual(gate.risk_classes, (RiskClass.READ_ONLY,))

    def test_global_hard_gate_categories_are_represented(self) -> None:
        self.assertEqual(
            {gate.value for gate in HardGate},
            {
                "public_publish",
                "deploy",
                "live_trade",
                "auth",
                "money",
                "external_send",
                "destructive_change",
            },
        )

    def test_local_draft_action_is_allowed_with_receipt(self) -> None:
        gate = self.engine.evaluate(
            ActionRequest(
                action="write_draft",
                target_ref="artifact://drafts/brief.md",
                arguments={"title": "Draft"},
                risk_classes=(RiskClass.LOCAL_DRAFT,),
            )
        )

        self.assertEqual(gate.decision, GateDecision.ALLOW_WITH_RECEIPT.value)

    def test_hard_gate_requires_human_without_exact_approval(self) -> None:
        request = ActionRequest(
            action="publish",
            target_ref="https://example.com/post",
            hard_gates=(HardGate.PUBLIC_PUBLISH,),
            risk_classes=(RiskClass.EXTERNAL_EFFECT,),
        )

        gate = self.engine.evaluate(request)

        self.assertEqual(gate.decision, GateDecision.REQUIRE_HUMAN.value)
        self.assertIn("missing approval", gate.decision_reason)

    def test_hard_gate_allows_when_human_approval_matches(self) -> None:
        request = ActionRequest(
            action="send_email",
            target_ref="mailto:editor@example.com",
            arguments={"subject": "Review package"},
            hard_gates=(HardGate.EXTERNAL_SEND,),
            risk_classes=(RiskClass.EXTERNAL_EFFECT,),
        )
        approval = HumanApprovalReceipt(
            approval_id="approval-1",
            gate_id="gate-1",
            human_ref="suman",
            canonical_surface="local-receipt",
            decision=ApprovalDecision.APPROVED,
            exact_action_approved=request.action,
            action_fingerprint=fingerprint_request(request),
            expires_at="2026-06-01T00:00:00Z",
        )

        gate = self.engine.evaluate(request, approval=approval, now="2026-05-31T12:00:00Z")

        self.assertEqual(gate.decision, GateDecision.ALLOW.value)
        self.assertEqual(gate.approval_receipt_ref, "approval-1")

    def test_general_approval_text_does_not_approve_hard_gate(self) -> None:
        request = ActionRequest(
            action="deploy",
            target_ref="service://production/kernel",
            hard_gates=(HardGate.DEPLOY,),
            risk_classes=(RiskClass.PRODUCTION_EFFECT,),
        )
        approval = HumanApprovalReceipt(
            approval_id="approval-vague",
            gate_id="gate-vague",
            human_ref="suman",
            canonical_surface="local-receipt",
            decision=ApprovalDecision.APPROVED,
            exact_action_approved="looks good",
            action_fingerprint=fingerprint_request(request),
        )

        gate = self.engine.evaluate(request, approval=approval, now="2026-05-31T12:00:00Z")

        self.assertEqual(gate.decision, GateDecision.REQUIRE_HUMAN.value)
        self.assertIn("exact action", gate.decision_reason)

    def test_fingerprint_is_stable_for_canonical_arguments(self) -> None:
        first = action_fingerprint(
            action="deploy",
            target_ref="service://kernel",
            arguments={"b": [2, 1], "a": {"z": True}},
            artifact_hashes=("sha256:abc",),
            context_packet_digest="ctx-1",
        )
        second = action_fingerprint(
            action="deploy",
            target_ref="service://kernel",
            arguments={"a": {"z": True}, "b": [2, 1]},
            artifact_hashes=("sha256:abc",),
            context_packet_digest="ctx-1",
        )

        self.assertEqual(first, second)

    def test_fingerprint_mismatch_invalidates_approval(self) -> None:
        original = ActionRequest(
            action="place_order",
            target_ref="broker://acct/order",
            arguments={"symbol": "COST", "qty": 1},
            hard_gates=(HardGate.LIVE_TRADE,),
            risk_classes=(RiskClass.FINANCIAL_EFFECT,),
        )
        changed = ActionRequest(
            action="place_order",
            target_ref="broker://acct/order",
            arguments={"symbol": "COST", "qty": 2},
            hard_gates=(HardGate.LIVE_TRADE,),
            risk_classes=(RiskClass.FINANCIAL_EFFECT,),
        )
        approval = HumanApprovalReceipt(
            approval_id="approval-2",
            gate_id="gate-2",
            human_ref="suman",
            canonical_surface="telegram",
            decision=ApprovalDecision.APPROVED,
            exact_action_approved=original.action,
            action_fingerprint=fingerprint_request(original),
        )

        validation = validate_approval(
            approval,
            expected_fingerprint=fingerprint_request(changed),
            now=datetime(2026, 5, 31, tzinfo=UTC),
        )
        gate = self.engine.evaluate(changed, approval=approval, now=datetime(2026, 5, 31, tzinfo=UTC))

        self.assertFalse(validation.valid)
        self.assertEqual(gate.decision, GateDecision.REQUIRE_HUMAN.value)
        self.assertIn("fingerprint", gate.decision_reason)

    def test_expired_approval_invalidates_hard_gate(self) -> None:
        request = ActionRequest(
            action="rotate_token",
            target_ref="auth://github/token",
            hard_gates=(HardGate.AUTH,),
            risk_classes=(RiskClass.AUTH_EFFECT,),
        )
        approval = HumanApprovalReceipt(
            approval_id="approval-3",
            gate_id="gate-3",
            human_ref="suman",
            canonical_surface="obsidian",
            decision=ApprovalDecision.APPROVED,
            exact_action_approved=request.action,
            action_fingerprint=fingerprint_request(request),
            expires_at="2026-05-30T00:00:00Z",
        )

        gate = self.engine.evaluate(request, approval=approval, now="2026-05-31T00:00:00Z")

        self.assertEqual(gate.decision, GateDecision.REQUIRE_HUMAN.value)
        self.assertIn("expired", gate.decision_reason)

    def test_forbidden_action_is_denied(self) -> None:
        gate = self.engine.evaluate(
            ActionRequest(
                action="delete_database",
                target_ref="db://prod",
                risk_classes=(RiskClass.FORBIDDEN,),
                hard_gates=(HardGate.DESTRUCTIVE_CHANGE,),
            )
        )

        self.assertEqual(gate.decision, GateDecision.DENY.value)

    def test_unknown_side_effect_requires_human(self) -> None:
        gate = self.engine.evaluate(
            ActionRequest(
                action="invoke_adapter",
                target_ref="adapter://unknown",
                side_effects_known=False,
            )
        )

        self.assertEqual(gate.decision, GateDecision.REQUIRE_HUMAN.value)


if __name__ == "__main__":
    unittest.main()
