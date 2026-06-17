"""Deterministic local adapter implementations for tests and fixtures.

Split from a single 4200-line module into a package; this facade re-exports
every previously module-level name so existing imports keep working.
"""

from ._shared import *  # noqa: F401,F403 (constants + helpers, compat re-export)
from ._shared import __all__ as _shared_all
from .fakes import (
    LocalFakeHostAdapter,
    LocalFakeLaneAdapter,
    LocalFakeRuntimeAdapter,
    LocalFakeSurfaceAdapter,
)
from .dry_run import (
    DryRunObsidianSurfaceAdapter,
    DryRunSheetsSurfaceAdapter,
    DryRunSurfaceAdapter,
    DryRunTelegramSurfaceAdapter,
)
from .sandbox import (
    SandboxObsidianMarkdownSurfaceAdapter,
    SandboxTelegramOutboxSurfaceAdapter,
)
from .live import LiveObsidianMarkdownSurfaceAdapter
from .review import LocalMarkdownHumanReviewSurfaceAdapter

__all__ = list(_shared_all) + [
    "DryRunObsidianSurfaceAdapter",
    "DryRunSheetsSurfaceAdapter",
    "DryRunSurfaceAdapter",
    "DryRunTelegramSurfaceAdapter",
    "LiveObsidianMarkdownSurfaceAdapter",
    "LocalFakeHostAdapter",
    "LocalFakeLaneAdapter",
    "LocalFakeRuntimeAdapter",
    "LocalFakeSurfaceAdapter",
    "LocalMarkdownHumanReviewSurfaceAdapter",
    "SandboxObsidianMarkdownSurfaceAdapter",
    "SandboxTelegramOutboxSurfaceAdapter",
]
