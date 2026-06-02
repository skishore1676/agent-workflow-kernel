"""Workflow DSL loading and canonicalization."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

from .contracts import PromptRef, StageDef, StageType, Transition, WorkflowDef, to_plain_data
from .validation import validate_workflow_def, validate_workflow_mapping


def load_workflow_yaml(source: str | bytes | TextIO) -> WorkflowDef:
    """Load a YAML workflow definition into a normalized ``WorkflowDef``."""

    loaded = _load_yaml(source)
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, Mapping):
        raise TypeError("workflow YAML must parse to a mapping")
    return workflow_from_mapping(_normalize_yaml_quirks(loaded))


def load_workflow_file(path: str | Path) -> WorkflowDef:
    """Load a workflow definition from a YAML file."""

    with Path(path).open("r", encoding="utf-8") as handle:
        return load_workflow_yaml(handle)


def workflow_from_mapping(data: Mapping[str, Any]) -> WorkflowDef:
    """Normalize raw DSL data into the kernel contract dataclasses."""

    validate_workflow_mapping(data)

    workflow_info = data["workflow"]
    assert isinstance(workflow_info, Mapping)

    workflow = WorkflowDef(
        id=str(workflow_info["id"]),
        version=str(workflow_info["version"]),
        name=str(workflow_info["name"]),
        stages=tuple(_stage_from_mapping(stage) for stage in data["stages"]),
        transitions=tuple(
            _transition_from_mapping(transition) for transition in data["transitions"]
        ),
        schema=str(data["schema"]),
        owner=_optional_str(workflow_info.get("owner")),
        description=_optional_str(workflow_info.get("description")),
        inputs=dict(data.get("inputs") or {}),
        defaults=dict(data.get("defaults") or {}),
        actors=dict(data.get("actors") or {}),
        artifacts=dict(data.get("artifacts") or {}),
        policies=dict(data.get("policies") or {}),
        compatibility=dict(data.get("compatibility") or {}),
    )
    validate_workflow_def(workflow)
    return workflow


def workflow_to_canonical_json(workflow: WorkflowDef) -> str:
    """Compile a workflow definition to deterministic canonical JSON."""

    return canonical_json(workflow)


def workflow_to_canonical_json_bytes(workflow: WorkflowDef) -> bytes:
    """Compile a workflow definition to deterministic canonical JSON bytes."""

    return workflow_to_canonical_json(workflow).encode("utf-8")


def canonical_json(value: Any) -> str:
    """Serialize a supported kernel value deterministically."""

    return json.dumps(
        to_plain_data(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _stage_from_mapping(data: Any) -> StageDef:
    stage = _require_mapping(data, "stage")
    return StageDef(
        id=str(stage["id"]),
        type=StageType(stage["type"]),
        adapter=str(stage["adapter"]),
        outcomes=tuple(str(outcome) for outcome in stage["outcomes"]),
        actors=dict(stage.get("actors") or {}),
        inputs=dict(stage.get("inputs") or {}),
        outputs=dict(stage.get("outputs") or {}),
        prompt_refs=tuple(_prompt_ref_from_mapping(item) for item in stage.get("prompt_refs") or ()),
        policy=dict(stage.get("policy") or {}),
        budget=dict(stage.get("budget") or {}),
        retry=dict(stage.get("retry") or {}),
        lease=dict(stage.get("lease") or {}),
        timeout_seconds=_optional_int(stage.get("timeout_seconds")),
        surface=dict(stage.get("surface") or {}),
    )


def _transition_from_mapping(data: Any) -> Transition:
    transition = _require_mapping(data, "transition")
    return Transition(
        from_stage=str(transition["from"]),
        on=str(transition["on"]),
        to_stage=_optional_str(transition.get("to")),
        terminal=_optional_str(transition.get("terminal")),
        guard=_optional_str(transition.get("guard")),
        label=_optional_str(transition.get("label")),
    )


def _prompt_ref_from_mapping(data: Any) -> PromptRef:
    prompt_ref = _require_mapping(data, "prompt_ref")
    return PromptRef(
        id=str(prompt_ref["id"]),
        kind=str(prompt_ref["kind"]),
        version=str(prompt_ref["version"]),
        registry=str(prompt_ref.get("registry", "local")),
        render_mode=str(prompt_ref.get("render_mode", "markdown")),
        required=bool(prompt_ref.get("required", True)),
        content_hash=_optional_str(prompt_ref.get("content_hash")),
    )


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return value


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _load_yaml(source: str | bytes | TextIO) -> Any:
    if hasattr(source, "read"):
        text = source.read()
    else:
        text = source
    if isinstance(text, bytes):
        text = text.decode("utf-8")

    try:
        import yaml  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return _load_simple_yaml(text)
    return yaml.safe_load(text)


def _normalize_yaml_quirks(value: Any) -> Any:
    """Normalize common YAML parser surprises in operator-authored files.

    PyYAML follows YAML 1.1 boolean rules, where an unquoted ``on:`` key parses
    as ``True``. Workflow transition maps naturally use ``on`` as a field name,
    so repair that key before validation while leaving all other values intact.
    """

    if isinstance(value, Mapping):
        normalized: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = "on" if key is True and "on" not in value else key
            normalized[normalized_key] = _normalize_yaml_quirks(item)
        return normalized
    if isinstance(value, list):
        return [_normalize_yaml_quirks(item) for item in value]
    return value


def _load_simple_yaml(text: str) -> Any:
    """Parse the small YAML subset used by workflow definitions.

    PyYAML remains the preferred parser when installed. This fallback keeps the
    checked-in unittest command working in a bare stdlib environment and only
    supports indentation-based mappings/lists, inline scalar lists, booleans,
    integers, strings, and nulls.
    """

    lines = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        lines.append((indent, raw_line.strip()))
    if not lines:
        return None

    value, index = _parse_yaml_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError("could not parse full YAML document")
    return value


def _parse_yaml_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    current_indent, text = lines[index]
    if current_indent < indent:
        return {}, index
    if current_indent != indent:
        raise ValueError(f"unexpected indentation at line: {text}")
    if text.startswith("- "):
        return _parse_yaml_list(lines, index, indent)
    return _parse_yaml_mapping(lines, index, indent)


def _parse_yaml_mapping(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        current_indent, text = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"unexpected nested mapping line: {text}")
        if text.startswith("- "):
            break

        key, raw_value = _split_yaml_pair(text)
        index += 1
        if raw_value:
            result[key] = _parse_yaml_scalar(raw_value)
        elif index < len(lines) and lines[index][0] > indent:
            result[key], index = _parse_yaml_block(lines, index, lines[index][0])
        else:
            result[key] = {}
    return result, index


def _parse_yaml_list(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        current_indent, text = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or not text.startswith("- "):
            break

        item_text = text[2:].strip()
        index += 1
        if not item_text:
            if index >= len(lines) or lines[index][0] <= indent:
                result.append({})
                continue
            item, index = _parse_yaml_block(lines, index, lines[index][0])
            result.append(item)
            continue

        if _looks_like_yaml_pair(item_text):
            key, raw_value = _split_yaml_pair(item_text)
            item_map: dict[str, Any] = {}
            if raw_value:
                item_map[key] = _parse_yaml_scalar(raw_value)
            elif index < len(lines) and lines[index][0] > indent:
                item_map[key], index = _parse_yaml_block(lines, index, lines[index][0])
            else:
                item_map[key] = {}

            if index < len(lines) and lines[index][0] > indent:
                continuation, index = _parse_yaml_mapping(lines, index, lines[index][0])
                item_map.update(continuation)
            result.append(item_map)
        else:
            result.append(_parse_yaml_scalar(item_text))
            if index < len(lines) and lines[index][0] > indent:
                raise ValueError(f"unexpected nested scalar line: {lines[index][1]}")
    return result, index


def _split_yaml_pair(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise ValueError(f"expected YAML mapping pair: {text}")
    key, raw_value = text.split(":", 1)
    key = key.strip()
    if key.startswith(("'", '"')) and key.endswith(("'", '"')):
        key = key[1:-1]
    if not key:
        raise ValueError(f"expected YAML mapping key: {text}")
    return key, raw_value.strip()


def _looks_like_yaml_pair(text: str) -> bool:
    return ":" in text and not text.startswith(("'", '"'))


def _parse_yaml_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"null", "none", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(part.strip()) for part in inner.split(",")]
    if value.startswith(("'", '"')) and value.endswith(("'", '"')):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value
