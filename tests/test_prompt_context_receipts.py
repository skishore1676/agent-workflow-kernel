import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    ArtifactRef,
    MissingPromptError,
    PromptRef,
    PromptRegistry,
    build_receipt,
    receipt_digest,
    render_context_packet,
)


PROMPT_REFS = (
    PromptRef(id="identity.portable_worker", kind="identity", version="1.0.0"),
    PromptRef(id="policy.no_external_effects", kind="policy", version="1.0.0", render_mode="yaml"),
    PromptRef(id="lane.quality_review", kind="lane", version="1.0.0"),
    PromptRef(id="stage.review", kind="stage", version="1.0.0"),
)


class PromptContextReceiptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = PromptRegistry.load(ROOT / "prompts")

    def test_resolves_exact_prompt_refs_with_content_hashes(self) -> None:
        bundle = self.registry.resolve(PROMPT_REFS)

        self.assertEqual([prompt.ref.kind for prompt in bundle.prompts], ["identity", "policy", "lane", "stage"])
        self.assertTrue(bundle.registry_snapshot_digest.startswith("sha256:"))
        self.assertTrue(all(prompt.content_hash.startswith("sha256:") for prompt in bundle.prompts))
        self.assertEqual(bundle.prompts[0].ref.content_hash, bundle.prompts[0].content_hash)

    def test_missing_required_prompt_raises(self) -> None:
        missing = PromptRef(id="stage.missing", kind="stage", version="9.9.9")

        with self.assertRaises(MissingPromptError):
            self.registry.resolve((missing,))

    def test_context_packet_and_rendered_input_digests_are_deterministic(self) -> None:
        bundle = self.registry.resolve(tuple(reversed(PROMPT_REFS)))
        artifact = ArtifactRef(
            artifact_id="artifact.patch",
            role="git_diff",
            uri="artifact://wi-1/patch.diff",
            content_hash="sha256:" + "a" * 64,
        )

        first = render_context_packet(
            prompt_bundle=bundle,
            workflow_id="wf.quality_review",
            workflow_version="0.3.0",
            instance_id="wi-1",
            stage_id="review_patch",
            stage_run_id="sr-1",
            stage_type="agent_work",
            attempt=1,
            workflow_state={"status": "running"},
            actor={"role": "reviewer", "runtime_target": "codex"},
            inputs={"objective": "Review the proposed patch."},
            artifacts=(artifact,),
            prior_receipts=({"receipt_id": "rcpt-0", "status": "succeeded"},),
            approvals=(),
            variables={"repo_name": "agent-workflow-kernel", "branch": "codex/example"},
            constraints={"required_outputs": ["verdict", "findings"]},
            permissions={"shell.read_only": True, "git.diff": True, "external_send": False},
        )
        second = render_context_packet(
            prompt_bundle=bundle,
            workflow_id="wf.quality_review",
            workflow_version="0.3.0",
            instance_id="wi-1",
            stage_id="review_patch",
            stage_run_id="sr-1",
            stage_type="agent_work",
            attempt=1,
            workflow_state={"status": "running"},
            actor={"runtime_target": "codex", "role": "reviewer"},
            inputs={"objective": "Review the proposed patch."},
            artifacts=(artifact,),
            prior_receipts=({"status": "succeeded", "receipt_id": "rcpt-0"},),
            approvals=(),
            variables={"branch": "codex/example", "repo_name": "agent-workflow-kernel"},
            constraints={"required_outputs": ["verdict", "findings"]},
            permissions={"external_send": False, "git.diff": True, "shell.read_only": True},
        )

        self.assertEqual(first.packet.context_id, second.packet.context_id)
        self.assertEqual(first.packet_digest, second.packet_digest)
        self.assertEqual(first.canonical_bundle_digest, second.canonical_bundle_digest)
        self.assertEqual(first.rendered_input_digest, second.rendered_input_digest)
        self.assertIn("identity.portable_worker", first.rendered_input)
        self.assertEqual(first.packet.input_digest, first.canonical_bundle_digest)
        self.assertEqual(first.packet.rendered_digest, first.rendered_input_digest)

    def test_receipt_provenance_includes_prompt_runtime_and_permissions(self) -> None:
        bundle = self.registry.resolve(PROMPT_REFS)
        rendered = render_context_packet(
            prompt_bundle=bundle,
            workflow_id="wf.quality_review",
            workflow_version="0.3.0",
            instance_id="wi-1",
            stage_id="review_patch",
            stage_run_id="sr-1",
            stage_type="agent_work",
            permissions={"shell.read_only": True, "external_send": False},
        )

        receipt = build_receipt(
            receipt_id="rcpt-sr-1",
            kind="stage_run",
            status="succeeded",
            summary="Review completed.",
            created_at="2026-05-31T00:00:00Z",
            rendered_context=rendered,
            runtime={
                "adapter_id": "runtime.codex",
                "adapter_version": "0.1.0",
                "model": "gpt-5-codex",
                "model_version": "2026-05-31",
                "host_runtime": "codex-desktop",
            },
            granted_permissions=("shell.read_only",),
            denied_permissions=("external_send", "live_trade"),
            residual_risk="No live effects were allowed.",
            next_action="Store review result.",
            redaction_mode="none",
        )

        self.assertEqual(receipt.context_packet_ref, rendered.packet.context_id)
        self.assertEqual(receipt.prompt_provenance["context"]["packet_digest"], rendered.packet_digest)
        self.assertEqual(receipt.prompt_provenance["context"]["rendered_input_digest"], rendered.rendered_input_digest)
        self.assertEqual(receipt.runtime_provenance["adapter_id"], "runtime.codex")
        self.assertEqual(receipt.runtime_provenance["model"], "gpt-5-codex")
        self.assertEqual(receipt.policy_snapshot["policy_id"], "policy.no_external_effects")
        self.assertEqual(receipt.policy_snapshot["effective_permissions_digest"], rendered.tool_permissions_digest)
        self.assertEqual(receipt.policy_snapshot["denied"], ["external_send", "live_trade"])
        self.assertTrue(receipt_digest(receipt).startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
