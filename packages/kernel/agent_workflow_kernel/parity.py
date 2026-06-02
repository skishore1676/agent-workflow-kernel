"""Deterministic parity reporting for host and kernel receipts."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .prompts import canonical_json, canonicalize_data, digest_data


PARITY_REPORT_SCHEMA = "workflow.kernel.parity-report.v1"
PARITY_FIXTURE_SCHEMA = "workflow.kernel.parity-fixture.v1"
_ABSENT = object()


@dataclass(frozen=True, slots=True)
class ParityField:
    """One classified field in a parity report."""

    path: str
    expected: Any = _ABSENT
    actual: Any = _ABSENT
    reason: str | None = None

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"path": self.path}
        if self.expected is not _ABSENT:
            data["expected"] = canonicalize_data(self.expected)
        if self.actual is not _ABSENT:
            data["actual"] = canonicalize_data(self.actual)
        if self.reason is not None:
            data["reason"] = self.reason
        return data


@dataclass(frozen=True, slots=True)
class ParityReport:
    """Receipt parity result with stable field ordering."""

    schema: str
    report_id: str
    status: str
    expected_label: str
    actual_label: str
    equivalent: tuple[ParityField, ...] = ()
    different: tuple[ParityField, ...] = ()
    missing: tuple[ParityField, ...] = ()
    extra: tuple[ParityField, ...] = ()
    ignored: tuple[ParityField, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "report_id": self.report_id,
            "status": self.status,
            "expected_label": self.expected_label,
            "actual_label": self.actual_label,
            "summary": {
                "equivalent": len(self.equivalent),
                "different": len(self.different),
                "missing": len(self.missing),
                "extra": len(self.extra),
                "ignored": len(self.ignored),
            },
            "fields": {
                "equivalent": [field.to_data() for field in self.equivalent],
                "different": [field.to_data() for field in self.different],
                "missing": [field.to_data() for field in self.missing],
                "extra": [field.to_data() for field in self.extra],
                "ignored": [field.to_data() for field in self.ignored],
            },
            "metadata": canonicalize_data(self.metadata),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_data()) + "\n"


def compare_receipts(
    expected_host_receipt: Mapping[str, Any] | Any,
    actual_kernel_receipt: Mapping[str, Any] | Any,
    *,
    ignored_fields: Sequence[str] | Mapping[str, str] = (),
    expected_label: str = "expected_host_receipt",
    actual_label: str = "actual_kernel_receipt",
    metadata: Mapping[str, Any] | None = None,
    report_id: str | None = None,
) -> ParityReport:
    """Compare host and kernel receipt fields without calling any live adapter."""

    expected = _flatten(canonicalize_data(expected_host_receipt))
    actual = _flatten(canonicalize_data(actual_kernel_receipt))
    ignore_reasons = _ignore_reason_map(ignored_fields)
    all_paths = sorted(set(expected) | set(actual))
    equivalent: list[ParityField] = []
    different: list[ParityField] = []
    missing: list[ParityField] = []
    extra: list[ParityField] = []
    ignored: list[ParityField] = []

    for path in all_paths:
        ignore_reason = _ignored_reason(path, ignore_reasons)
        expected_present = path in expected
        actual_present = path in actual
        if ignore_reason is not None:
            ignored.append(
                ParityField(
                    path=path,
                    expected=expected[path] if expected_present else _ABSENT,
                    actual=actual[path] if actual_present else _ABSENT,
                    reason=ignore_reason,
                )
            )
        elif expected_present and actual_present:
            if expected[path] == actual[path]:
                equivalent.append(ParityField(path=path, expected=expected[path], actual=actual[path]))
            else:
                different.append(ParityField(path=path, expected=expected[path], actual=actual[path]))
        elif expected_present:
            missing.append(ParityField(path=path, expected=expected[path]))
        else:
            extra.append(ParityField(path=path, actual=actual[path]))

    status = "equivalent" if not different and not missing and not extra else "different"
    report_data = {
        "expected_label": expected_label,
        "actual_label": actual_label,
        "expected_digest": digest_data(expected_host_receipt),
        "actual_digest": digest_data(actual_kernel_receipt),
        "ignored_fields": ignore_reasons,
        "metadata": metadata or {},
    }
    return ParityReport(
        schema=PARITY_REPORT_SCHEMA,
        report_id=report_id or digest_data(report_data),
        status=status,
        expected_label=expected_label,
        actual_label=actual_label,
        equivalent=tuple(equivalent),
        different=tuple(different),
        missing=tuple(missing),
        extra=tuple(extra),
        ignored=tuple(ignored),
        metadata=metadata or {},
    )


def load_parity_fixture(path: str | Path) -> dict[str, Any]:
    fixture_path = Path(path)
    with fixture_path.open("r", encoding="utf-8") as handle:
        fixture = json.load(handle)
    if fixture.get("schema") != PARITY_FIXTURE_SCHEMA:
        raise ValueError(f"Unsupported parity fixture schema in {fixture_path}: {fixture.get('schema')}")
    return fixture


def report_from_fixture(fixture: Mapping[str, Any]) -> ParityReport:
    return compare_receipts(
        fixture["expected_host_receipt"],
        fixture["actual_kernel_receipt"],
        ignored_fields=fixture.get("ignored_fields", {}),
        expected_label=str(fixture.get("expected_label", "expected_host_receipt")),
        actual_label=str(fixture.get("actual_label", "actual_kernel_receipt")),
        metadata=fixture.get("metadata", {}),
        report_id=fixture.get("report_id"),
    )


def _flatten(value: Any, path: str = "$") -> dict[str, Any]:
    if isinstance(value, Mapping):
        if not value:
            return {path: {}}
        flattened: dict[str, Any] = {}
        for key in sorted(value, key=str):
            flattened.update(_flatten(value[key], f"{path}.{key}"))
        return flattened
    if isinstance(value, list):
        if not value:
            return {path: []}
        flattened = {}
        for index, item in enumerate(value):
            flattened.update(_flatten(item, f"{path}[{index}]"))
        return flattened
    return {path: value}


def _ignore_reason_map(ignored_fields: Sequence[str] | Mapping[str, str]) -> dict[str, str]:
    if isinstance(ignored_fields, Mapping):
        return {str(path): str(reason) for path, reason in sorted(ignored_fields.items())}
    return {str(path): "ignored by parity fixture" for path in ignored_fields}


def _ignored_reason(path: str, ignore_reasons: Mapping[str, str]) -> str | None:
    for pattern, reason in ignore_reasons.items():
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            if path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "["):
                return reason
        elif path == pattern:
            return reason
    return None
