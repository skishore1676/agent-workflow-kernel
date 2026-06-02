import sys
import tomllib
import unittest
from fnmatch import fnmatchcase
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagingDiscoveryTest(unittest.TestCase):
    def test_root_package_discovery_includes_kernel_and_openclaw_adapter(self) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        find_config = config["tool"]["setuptools"]["packages"]["find"]
        discovered: set[str] = set()
        for package_root in find_config["where"]:
            discovered.update(_discover_packages(ROOT / package_root, tuple(find_config["include"])))

        self.assertIn("agent_workflow_kernel", discovered)
        self.assertIn("agent_workflow_kernel_openclaw", discovered)

    def test_openclaw_telegram_adapter_imports_from_adapter_package(self) -> None:
        sys.path.insert(0, str(ROOT / "packages" / "kernel"))
        sys.path.insert(0, str(ROOT / "packages" / "adapters" / "openclaw"))

        import agent_workflow_kernel
        from agent_workflow_kernel_openclaw import OpenClawTelegramSurfaceAdapter

        self.assertFalse(hasattr(agent_workflow_kernel, "OpenClawTelegramSurfaceAdapter"))
        self.assertEqual(OpenClawTelegramSurfaceAdapter.adapter_id, "surface.openclaw_telegram")


def _discover_packages(package_root: Path, include: tuple[str, ...]) -> set[str]:
    packages: set[str] = set()
    for init_file in package_root.rglob("__init__.py"):
        package = ".".join(init_file.parent.relative_to(package_root).parts)
        if package and any(fnmatchcase(package, pattern) for pattern in include):
            packages.add(package)
    return packages


if __name__ == "__main__":
    unittest.main()
