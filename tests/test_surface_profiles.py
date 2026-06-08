import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

from agent_workflow_kernel import (  # noqa: E402
    AdapterFamily,
    AdapterRegistration,
    AdapterRegistry,
    CapabilitySet,
    DryRunObsidianSurfaceAdapter,
    DryRunTelegramSurfaceAdapter,
    SurfaceProfileError,
    load_surface_profile,
    surface_profile_from_mapping,
)


class PublishOnlySurfaceAdapter:
    adapter_id = "surface.publish_only"
    family = AdapterFamily.SURFACE
    operations = ("publish",)

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.adapter_id,
            family=self.family,
            operations=self.operations,
        )


class SurfaceProfileTest(unittest.TestCase):
    def test_profile_resolves_semantic_human_review_to_obsidian_adapter(self) -> None:
        registry = _surface_registry()
        profile = surface_profile_from_mapping(
            {
                "schema": "surface.profile.v1",
                "profile": {
                    "id": "openclaw-local",
                    "bindings": [
                        {
                            "semantic_ref": "surface.human_review",
                            "adapter_id": "surface.obsidian_dry_run",
                            "surface_kind": "obsidian_note",
                            "fallback_adapter_ids": ["surface.telegram_dry_run"],
                        }
                    ],
                },
            }
        )

        resolved = profile.resolve("surface.human_review", registry)

        self.assertEqual(resolved.semantic_ref, "surface.human_review")
        self.assertEqual(resolved.adapter_id, "surface.obsidian_dry_run")
        self.assertEqual(resolved.fallback_registrations[0].adapter_id, "surface.telegram_dry_run")
        self.assertEqual(resolved.to_metadata()["registration"]["family"], "surface")

    def test_same_semantic_ref_can_resolve_to_different_host_surface(self) -> None:
        registry = _surface_registry()
        obsidian_profile = surface_profile_from_mapping(
            {
                "schema": "surface.profile.v1",
                "profile_id": "openclaw-obsidian",
                "bindings": [
                    {"semantic_ref": "surface.human_review", "adapter_id": "surface.obsidian_dry_run"}
                ],
            }
        )
        telegram_profile = surface_profile_from_mapping(
            {
                "schema": "surface.profile.v1",
                "profile_id": "openclaw-telegram",
                "bindings": [
                    {"semantic_ref": "surface.human_review", "adapter_id": "surface.telegram_dry_run"}
                ],
            }
        )

        self.assertEqual(
            obsidian_profile.resolve("surface.human_review", registry).adapter_id,
            "surface.obsidian_dry_run",
        )
        self.assertEqual(
            telegram_profile.resolve("surface.human_review", registry).adapter_id,
            "surface.telegram_dry_run",
        )

    def test_profile_loads_from_json_and_validates_all_bindings(self) -> None:
        registry = _surface_registry()
        payload: dict[str, Any] = {
            "schema": "surface.profile.v1",
            "profile": {
                "id": "json-profile",
                "description": "Fixture profile",
                "bindings": [
                    {
                        "semantic_ref": "surface.human_review",
                        "adapter_id": "surface.obsidian_dry_run",
                    }
                ],
            },
        }
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "surface-profile.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            profile = load_surface_profile(path)

        resolved = profile.validate(registry)
        self.assertEqual(profile.profile_id, "json-profile")
        self.assertEqual(resolved[0].adapter_id, "surface.obsidian_dry_run")

    def test_missing_binding_and_missing_adapter_fail_closed(self) -> None:
        registry = _surface_registry()
        profile = surface_profile_from_mapping(
            {
                "schema": "surface.profile.v1",
                "profile_id": "missing-fixture",
                "bindings": [
                    {"semantic_ref": "surface.human_review", "adapter_id": "surface.not_registered"}
                ],
            }
        )

        with self.assertRaisesRegex(SurfaceProfileError, "missing adapter registration"):
            profile.resolve("surface.human_review", registry)
        with self.assertRaisesRegex(SurfaceProfileError, "missing surface binding"):
            profile.resolve("surface.final_publish", registry)

    def test_required_operations_are_checked_before_surface_use(self) -> None:
        registry = AdapterRegistry(
            (AdapterRegistration.from_surface_adapter(PublishOnlySurfaceAdapter()),)
        )
        profile = surface_profile_from_mapping(
            {
                "schema": "surface.profile.v1",
                "profile_id": "operation-check",
                "bindings": [
                    {"semantic_ref": "surface.human_review", "adapter_id": "surface.publish_only"}
                ],
            }
        )

        with self.assertRaisesRegex(SurfaceProfileError, "missing required operations"):
            profile.resolve("surface.human_review", registry)


def _surface_registry() -> AdapterRegistry:
    return AdapterRegistry(
        (
            AdapterRegistration.from_surface_adapter(DryRunObsidianSurfaceAdapter()),
            AdapterRegistration.from_surface_adapter(DryRunTelegramSurfaceAdapter()),
        )
    )


if __name__ == "__main__":
    unittest.main()
