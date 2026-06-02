import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    ACTOR_SESSION_KEY_PREFIX,
    ActorSessionBinding,
    ActorSessionScope,
    canonical_actor_session_binding,
    canonical_actor_session_key,
    digest_actor_session_profile,
)


class ActorSessionKeyTest(unittest.TestCase):
    def test_workflow_instance_key_is_canonical_and_stable(self) -> None:
        profile_digest = digest_actor_session_profile(
            {
                "identity_prompt": "identity.ivy_or_research@1.0.0",
                "policy": {"public_publish": "blocked"},
                "runtime": {"adapter_id": "runtime.agent"},
            }
        )
        binding = ActorSessionBinding(
            scope=ActorSessionScope.WORKFLOW_INSTANCE,
            scope_id="wi-editorial-001",
            workflow_id="ivy_jonah_editorial",
            workflow_version="0.1.0",
            actor_ref="actors.writer",
            adapter_id="runtime.agent",
            runtime_namespace="local",
            profile_binding_digest=profile_digest,
        )
        same_binding_different_order = {
            "profile_binding_digest": profile_digest,
            "runtime_namespace": "local",
            "adapter_id": "runtime.agent",
            "actor_ref": "actors.writer",
            "workflow_version": "0.1.0",
            "workflow_id": "ivy_jonah_editorial",
            "scope_id": "wi-editorial-001",
            "scope": "workflow_instance",
        }

        first_key = canonical_actor_session_key(binding)
        second_key = canonical_actor_session_key(same_binding_different_order)

        self.assertTrue(first_key.startswith(ACTOR_SESSION_KEY_PREFIX))
        self.assertEqual(first_key, second_key)
        self.assertEqual(len(first_key), len(ACTOR_SESSION_KEY_PREFIX) + 64)
        canonical = canonical_actor_session_binding(binding)
        self.assertEqual(canonical["schema_version"], "actor-session-key.v1")
        self.assertEqual(canonical["scope"], "workflow_instance")
        self.assertEqual(canonical["profile_binding_digest"], profile_digest)

    def test_key_changes_across_scope_instance_actor_or_profile(self) -> None:
        profile_digest = digest_actor_session_profile({"identity": "writer"})
        base = ActorSessionBinding(
            scope=ActorSessionScope.WORKFLOW_INSTANCE,
            scope_id="wi-1",
            workflow_id="bumblebee_quality_review",
            workflow_version="0.1.0",
            actor_ref="actors.producer",
            adapter_id="runtime.agent",
            profile_binding_digest=profile_digest,
        )

        keys = {
            canonical_actor_session_key(base),
            canonical_actor_session_key(
                ActorSessionBinding(
                    scope=ActorSessionScope.WORKFLOW_INSTANCE,
                    scope_id="wi-2",
                    workflow_id=base.workflow_id,
                    workflow_version=base.workflow_version,
                    actor_ref=base.actor_ref,
                    adapter_id=base.adapter_id,
                    profile_binding_digest=base.profile_binding_digest,
                )
            ),
            canonical_actor_session_key(
                ActorSessionBinding(
                    scope=ActorSessionScope.WORKFLOW_INSTANCE,
                    scope_id=base.scope_id,
                    workflow_id=base.workflow_id,
                    workflow_version=base.workflow_version,
                    actor_ref="actors.reviewer",
                    adapter_id=base.adapter_id,
                    profile_binding_digest=base.profile_binding_digest,
                )
            ),
            canonical_actor_session_key(
                ActorSessionBinding(
                    scope=ActorSessionScope.WORKFLOW_INSTANCE,
                    scope_id=base.scope_id,
                    workflow_id=base.workflow_id,
                    workflow_version=base.workflow_version,
                    actor_ref=base.actor_ref,
                    adapter_id=base.adapter_id,
                    profile_binding_digest=digest_actor_session_profile({"identity": "reviewer"}),
                )
            ),
        }

        self.assertEqual(len(keys), 4)

    def test_program_instance_key_is_not_bound_to_one_workflow_occurrence(self) -> None:
        profile_digest = digest_actor_session_profile({"identity": "jarvis-weekly"})
        binding = ActorSessionBinding(
            scope=ActorSessionScope.PROGRAM_INSTANCE,
            scope_id="program-instance:jarvis-weekly",
            program_id="jarvis_weekly_update",
            workflow_id="jarvis_weekly_update_shadow",
            actor_ref="actors.jarvis",
            adapter_id="runtime.agent",
            profile_binding_digest=profile_digest,
        )

        canonical = canonical_actor_session_binding(binding)

        self.assertEqual(canonical["scope"], "program_instance")
        self.assertEqual(canonical["program_id"], "jarvis_weekly_update")
        self.assertIsNone(canonical["workflow_version"])
        self.assertEqual(
            canonical_actor_session_key(binding),
            canonical_actor_session_key({**canonical, "workflow_instance_id": "ignored-occurrence"}),
        )

    def test_invalid_bindings_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "workflow_version"):
            canonical_actor_session_key(
                {
                    "scope": "workflow_instance",
                    "scope_id": "wi-1",
                    "workflow_id": "wf",
                    "actor_ref": "actors.worker",
                    "adapter_id": "runtime.agent",
                    "profile_binding_digest": "sha256:" + "a" * 64,
                }
            )

        with self.assertRaisesRegex(ValueError, "program_id"):
            canonical_actor_session_key(
                {
                    "scope": "program_instance",
                    "scope_id": "program-instance",
                    "workflow_id": "wf",
                    "actor_ref": "actors.worker",
                    "adapter_id": "runtime.agent",
                    "profile_binding_digest": "sha256:" + "a" * 64,
                }
            )

        with self.assertRaisesRegex(ValueError, "profile_binding_digest"):
            canonical_actor_session_key(
                {
                    "scope": "workflow_instance",
                    "scope_id": "wi-1",
                    "workflow_id": "wf",
                    "workflow_version": "0.1.0",
                    "actor_ref": "actors.worker",
                    "adapter_id": "runtime.agent",
                    "profile_binding_digest": "not-a-digest",
                }
            )


if __name__ == "__main__":
    unittest.main()
