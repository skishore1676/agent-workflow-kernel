"""Read-only OpenClaw compatibility adapter boundary."""

from .mapping import (
    OpenClawReferenceMapping,
    OpenClawWorkLedgerIds,
    mapping_from_fixture,
    runtime_refs_from_fixture,
    surface_refs_from_fixture,
)
from .readonly import (
    OpenClawMutationBlocked,
    OpenClawReadOnlyAdapter,
    OpenClawReadOnlyInspection,
    artifact_refs_from_fixture,
    guard_read_only_operation,
    invocation_from_fixture,
)

__all__ = [
    "OpenClawMutationBlocked",
    "OpenClawReadOnlyAdapter",
    "OpenClawReadOnlyInspection",
    "OpenClawReferenceMapping",
    "OpenClawWorkLedgerIds",
    "artifact_refs_from_fixture",
    "guard_read_only_operation",
    "invocation_from_fixture",
    "mapping_from_fixture",
    "runtime_refs_from_fixture",
    "surface_refs_from_fixture",
]
