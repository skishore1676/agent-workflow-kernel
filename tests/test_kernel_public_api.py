"""Frozen public-API surface guard.

The lane-host program vendors in this kernel; its public surface
(``agent_workflow_kernel.__all__``) is a contract. Import-preserving refactors
must not silently drop or rename an exported name. This test pins the surface so
a regression fails loudly.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "kernel"))

import agent_workflow_kernel as awk  # noqa: E402

# Pinned at the baseline of the kernel-vendoring-readiness re-architecture.
# Changing the public surface is a deliberate act: update this number with intent.
# Bumped 135 -> 136 by the lane-host feature merge, which exports SessionBudget
# from sessions.py as a deliberate addition to the public API surface.
EXPECTED_EXPORT_COUNT = 136


class PublicApiSurfaceTest(unittest.TestCase):
    def test_all_is_defined(self) -> None:
        self.assertTrue(hasattr(awk, "__all__"))
        self.assertGreater(len(awk.__all__), 0)

    def test_no_duplicate_exports(self) -> None:
        names = list(awk.__all__)
        dupes = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(dupes, [], f"duplicate names in __all__: {dupes}")

    def test_export_count_is_frozen(self) -> None:
        self.assertEqual(
            len(awk.__all__),
            EXPECTED_EXPORT_COUNT,
            "Public export count changed. If intentional, update "
            "EXPECTED_EXPORT_COUNT; if not, a refactor dropped/added a name.",
        )

    def test_every_exported_name_resolves(self) -> None:
        missing = [name for name in awk.__all__ if not hasattr(awk, name)]
        self.assertEqual(missing, [], f"names in __all__ not importable: {missing}")


if __name__ == "__main__":
    unittest.main()
