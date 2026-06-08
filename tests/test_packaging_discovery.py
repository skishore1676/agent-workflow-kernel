import sys
import tomllib
import unittest
from fnmatch import fnmatchcase
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagingDiscoveryTest(unittest.TestCase):
    def test_root_package_discovery_includes_kernel_and_runtime_adapters(self) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        find_config = config["tool"]["setuptools"]["packages"]["find"]
        discovered: set[str] = set()
        for package_root in find_config["where"]:
            discovered.update(_discover_packages(ROOT / package_root, tuple(find_config["include"])))

        self.assertIn("agent_workflow_kernel", discovered)
        self.assertIn("agent_workflow_kernel_openclaw", discovered)
        self.assertIn("agent_workflow_kernel_codex_cli", discovered)
        self.assertIn("agent_workflow_kernel_codex_sdk", discovered)
        self.assertIn("agent_workflow_kernel_a2a", discovered)
        self.assertIn("agent_workflow_kernel_artifact_validation", discovered)
        self.assertIn("agent_workflow_kernel_ivy", discovered)

    def test_openclaw_telegram_adapter_imports_from_adapter_package(self) -> None:
        sys.path.insert(0, str(ROOT / "packages" / "kernel"))
        sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

        import agent_workflow_kernel
        from agent_workflow_kernel_openclaw import OpenClawTelegramSurfaceAdapter

        self.assertFalse(hasattr(agent_workflow_kernel, "OpenClawTelegramSurfaceAdapter"))
        self.assertEqual(OpenClawTelegramSurfaceAdapter.adapter_id, "surface.openclaw_telegram")

    def test_codex_cli_adapter_imports_from_adapter_package(self) -> None:
        sys.path.insert(0, str(ROOT / "packages" / "kernel"))
        sys.path.insert(0, str(ROOT / "packages" / "adapters" / "codex_cli"))

        import agent_workflow_kernel
        from agent_workflow_kernel_codex_cli import CodexCliSessionRuntimeAdapter

        self.assertFalse(hasattr(agent_workflow_kernel, "CodexCliSessionRuntimeAdapter"))
        self.assertEqual(CodexCliSessionRuntimeAdapter.adapter_id, "runtime.codex_cli_session")

    def test_codex_sdk_adapter_imports_from_adapter_package_without_sdk_import(self) -> None:
        sys.path.insert(0, str(ROOT / "packages" / "kernel"))
        sys.path.insert(0, str(ROOT / "packages" / "adapters" / "codex_sdk"))

        import agent_workflow_kernel
        from agent_workflow_kernel_codex_sdk import CodexSdkSessionRuntimeAdapter

        self.assertFalse(hasattr(agent_workflow_kernel, "CodexSdkSessionRuntimeAdapter"))
        self.assertEqual(CodexSdkSessionRuntimeAdapter.adapter_id, "runtime.codex_sdk_session")

    def test_ivy_editorial_adapters_import_from_adapter_package(self) -> None:
        sys.path.insert(0, str(ROOT / "packages" / "kernel"))
        sys.path.insert(0, str(ROOT / "packages" / "adapters" / "a2a"))
        sys.path.insert(0, str(ROOT / "packages" / "adapters" / "artifact_validation"))
        sys.path.insert(0, str(ROOT / "packages" / "adapters" / "ivy"))

        import agent_workflow_kernel
        from agent_workflow_kernel_ivy import A2AReviewRuntimeAdapter, ArtifactHashValidatorAdapter

        self.assertFalse(hasattr(agent_workflow_kernel, "A2AReviewRuntimeAdapter"))
        self.assertEqual(A2AReviewRuntimeAdapter.adapter_id, "runtime.a2a")
        self.assertEqual(ArtifactHashValidatorAdapter.adapter_id, "lane.artifact_hash_validator")

    def test_a2a_adapters_import_from_generic_adapter_package(self) -> None:
        sys.path.insert(0, str(ROOT / "packages" / "kernel"))
        sys.path.insert(0, str(ROOT / "packages" / "adapters" / "a2a"))

        import agent_workflow_kernel
        from agent_workflow_kernel_a2a import (
            A2AReviewRuntimeAdapter,
            a2a_runtime_registrations,
        )

        self.assertFalse(hasattr(agent_workflow_kernel, "A2AReviewRuntimeAdapter"))
        self.assertEqual(A2AReviewRuntimeAdapter.adapter_id, "runtime.a2a")
        self.assertIsInstance(a2a_runtime_registrations(), tuple)

    def test_artifact_hash_validator_import_from_generic_adapter_package(self) -> None:
        sys.path.insert(0, str(ROOT / "packages" / "kernel"))
        sys.path.insert(0, str(ROOT / "packages" / "adapters" / "artifact_validation"))

        import agent_workflow_kernel
        from agent_workflow_kernel_artifact_validation import (
            ArtifactHashValidatorAdapter,
            artifact_hash_validator_registrations,
        )

        self.assertFalse(hasattr(agent_workflow_kernel, "ArtifactHashValidatorAdapter"))
        self.assertEqual(ArtifactHashValidatorAdapter.adapter_id, "lane.artifact_hash_validator")
        self.assertIsInstance(artifact_hash_validator_registrations(), tuple)

    def test_openclaw_runtime_adapter_import_from_adapter_package(self) -> None:
        sys.path.insert(0, str(ROOT / "packages" / "kernel"))
        sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

        import agent_workflow_kernel
        from agent_workflow_kernel_openclaw import (
            OpenClawAgentRuntimeAdapter,
            openclaw_agent_runtime_registrations,
        )

        self.assertFalse(hasattr(agent_workflow_kernel, "OpenClawAgentRuntimeAdapter"))
        self.assertEqual(OpenClawAgentRuntimeAdapter.adapter_id, "runtime.openclaw_agent")
        self.assertIsInstance(openclaw_agent_runtime_registrations(), tuple)


def _discover_packages(package_root: Path, include: tuple[str, ...]) -> set[str]:
    packages: set[str] = set()
    for init_file in package_root.rglob("__init__.py"):
        package = ".".join(init_file.parent.relative_to(package_root).parts)
        if package and any(fnmatchcase(package, pattern) for pattern in include):
            packages.add(package)
    return packages


if __name__ == "__main__":
    unittest.main()
