"""Native AWK adapters for generic agent-to-agent review loops."""

from .registrations import a2a_runtime_registrations
from .runtime import (
    A2AReviewRuntimeAdapter,
    A2A_REVIEW_SCHEMA,
    A2ATurnProvider,
)

__all__ = [
    "A2AReviewRuntimeAdapter",
    "A2A_REVIEW_SCHEMA",
    "A2ATurnProvider",
    "a2a_runtime_registrations",
]
