"""Prompt registry and deterministic context packet rendering helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import ArtifactRef, ContextPacket, PromptRef, to_plain_data

try:  # pragma: no cover - exercised by normal environments with PyYAML.
    import yaml
except ImportError:  # pragma: no cover - JSON fallback keeps the module importable.
    yaml = None


CONTEXT_PACKET_SCHEMA_VERSION = "context-packet.v1"
PROMPT_REGISTRY_SCHEMA_VERSION = "prompt-registry.v1"
PROMPT_LAYER_ORDER = ("identity", "policy", "lane", "stage", "adapter_source")


class PromptRegistryError(ValueError):
    """Base error for prompt registry failures."""


class MissingPromptError(PromptRegistryError):
    """Raised when a required prompt reference cannot be resolved."""


class PromptHashMismatchError(PromptRegistryError):
    """Raised when declared prompt hash does not match local content."""


@dataclass(frozen=True, slots=True)
class PromptRecord:
    """Indexed prompt metadata from a local registry."""

    id: str
    kind: str
    version: str
    path: str
    registry: str = "local"
    render_mode: str = "markdown"
    content_hash: str | None = None
    status: str = "active"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedPrompt:
    """Prompt content pinned to an exact version and content hash."""

    ref: PromptRef
    path: str
    content: str
    content_hash: str
    status: str = "active"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def layer(self) -> str:
        return self.ref.kind

    def provenance(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "id": self.ref.id,
            "kind": self.ref.kind,
            "version": self.ref.version,
            "registry": self.ref.registry,
            "render_mode": self.ref.render_mode,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class PromptBundle:
    """Ordered prompt layers for one stage invocation."""

    prompts: tuple[ResolvedPrompt, ...]
    registry_snapshot_digest: str

    @property
    def prompt_refs(self) -> tuple[PromptRef, ...]:
        return tuple(prompt.ref for prompt in self.prompts)

    def provenance_refs(self) -> list[dict[str, Any]]:
        return [prompt.provenance() for prompt in self.prompts]

    def canonical_data(self) -> dict[str, Any]:
        return {
            "schema_version": "prompt-bundle.v1",
            "registry_snapshot_digest": self.registry_snapshot_digest,
            "composition_order": [prompt.ref.kind for prompt in self.prompts],
            "prompts": [
                {
                    **prompt.provenance(),
                    "path": prompt.path,
                }
                for prompt in self.prompts
            ],
        }


@dataclass(frozen=True, slots=True)
class RenderedContext:
    """Rendered context packet plus the canonical runtime input."""

    packet: ContextPacket
    packet_data: Mapping[str, Any]
    packet_digest: str
    canonical_bundle_digest: str
    rendered_input: str
    rendered_input_digest: str
    prompt_bundle: PromptBundle
    tool_permissions_digest: str | None = None


class PromptRegistry:
    """File-backed local prompt registry.

    The registry index points at prompt files; prompt file bytes remain the
    source of truth and are re-hashed every time refs are resolved.
    """

    def __init__(
        self,
        root: str | Path,
        records: Iterable[PromptRecord],
        *,
        schema_version: str = PROMPT_REGISTRY_SCHEMA_VERSION,
        registry_id: str = "local",
    ) -> None:
        self.root = Path(root).resolve()
        self.schema_version = schema_version
        self.registry_id = registry_id
        self._records: dict[tuple[str, str, str, str], PromptRecord] = {}
        for record in records:
            key = (record.registry, record.id, record.kind, record.version)
            self._records[key] = record
        self.snapshot_digest = digest_data(
            {
                "schema_version": self.schema_version,
                "registry_id": self.registry_id,
                "prompts": [
                    to_plain_data(record)
                    for record in sorted(
                        self._records.values(),
                        key=lambda item: (item.registry, item.kind, item.id, item.version),
                    )
                ],
            }
        )

    @classmethod
    def load(cls, root: str | Path, index_name: str = "registry.yaml") -> "PromptRegistry":
        root_path = Path(root).resolve()
        index_path = root_path / index_name
        data = _load_mapping(index_path)
        registry_id = str(data.get("registry_id", "local"))
        schema_version = str(data.get("schema_version", PROMPT_REGISTRY_SCHEMA_VERSION))
        records = []
        for raw in data.get("prompts", []):
            metadata = {
                key: value
                for key, value in raw.items()
                if key
                not in {
                    "id",
                    "kind",
                    "version",
                    "path",
                    "registry",
                    "render_mode",
                    "content_hash",
                    "status",
                }
            }
            records.append(
                PromptRecord(
                    id=str(raw["id"]),
                    kind=str(raw["kind"]),
                    version=str(raw["version"]),
                    path=str(raw["path"]),
                    registry=str(raw.get("registry", registry_id)),
                    render_mode=str(raw.get("render_mode", _default_render_mode(str(raw["path"])))),
                    content_hash=raw.get("content_hash"),
                    status=str(raw.get("status", "active")),
                    metadata=metadata,
                )
            )
        return cls(root_path, records, schema_version=schema_version, registry_id=registry_id)

    def resolve(self, refs: Sequence[PromptRef]) -> PromptBundle:
        resolved: list[ResolvedPrompt] = []
        for ref in refs:
            record = self._records.get((ref.registry, ref.id, ref.kind, ref.version))
            if record is None:
                if ref.required:
                    raise MissingPromptError(
                        f"Missing required prompt {ref.registry}:{ref.kind}:{ref.id}@{ref.version}"
                    )
                continue
            prompt = self._resolve_record(record, ref)
            resolved.append(prompt)
        ordered = tuple(sorted(enumerate(resolved), key=lambda item: (_layer_index(item[1].ref.kind), item[0])))
        return PromptBundle(
            prompts=tuple(prompt for _, prompt in ordered),
            registry_snapshot_digest=self.snapshot_digest,
        )

    def _resolve_record(self, record: PromptRecord, ref: PromptRef) -> ResolvedPrompt:
        path = (self.root / record.path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise PromptRegistryError(f"Prompt path escapes registry root: {record.path}") from exc
        content = path.read_text(encoding="utf-8")
        actual_hash = hash_text(content)
        for declared_hash in (record.content_hash, ref.content_hash):
            if declared_hash and declared_hash != actual_hash:
                raise PromptHashMismatchError(
                    f"Hash mismatch for {record.id}@{record.version}: "
                    f"declared {declared_hash}, actual {actual_hash}"
                )
        pinned_ref = PromptRef(
            id=ref.id,
            kind=ref.kind,
            version=ref.version,
            registry=ref.registry,
            render_mode=ref.render_mode or record.render_mode,
            required=ref.required,
            content_hash=actual_hash,
        )
        return ResolvedPrompt(
            ref=pinned_ref,
            path=record.path,
            content=normalize_text(content),
            content_hash=actual_hash,
            status=record.status,
            metadata=record.metadata,
        )


def render_context_packet(
    *,
    prompt_bundle: PromptBundle,
    workflow_id: str,
    workflow_version: str,
    instance_id: str,
    stage_id: str,
    stage_run_id: str,
    stage_type: str,
    attempt: int = 1,
    workflow_state: Mapping[str, Any] | None = None,
    actor: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
    artifacts: Sequence[ArtifactRef | Mapping[str, Any]] = (),
    prior_receipts: Sequence[Mapping[str, Any] | Any] = (),
    approvals: Sequence[Mapping[str, Any] | Any] = (),
    variables: Mapping[str, Any] | None = None,
    constraints: Mapping[str, Any] | None = None,
    permissions: Mapping[str, Any] | None = None,
    context_id: str | None = None,
) -> RenderedContext:
    """Render a deterministic context packet and canonical runtime input."""

    artifact_data = [to_plain_data(artifact) for artifact in artifacts]
    variables_data = canonicalize_data(variables or {})
    permissions_data = canonicalize_data(permissions or {})
    tool_permissions_digest = digest_data(permissions_data) if permissions is not None else None
    base_packet = {
        "schema_version": CONTEXT_PACKET_SCHEMA_VERSION,
        "workflow": {
            "id": workflow_id,
            "version": workflow_version,
            "instance_id": instance_id,
            "state": canonicalize_data(workflow_state or {}),
        },
        "stage": {
            "id": stage_id,
            "type": str(stage_type),
            "run_id": stage_run_id,
            "attempt": attempt,
        },
        "actor": canonicalize_data(actor or {}),
        "prompt_bundle": prompt_bundle.canonical_data(),
        "inputs": {
            "facts": canonicalize_data(inputs or {}),
            "artifacts": canonicalize_data(artifact_data),
            "prior_receipts": canonicalize_data([_receipt_summary(receipt) for receipt in prior_receipts]),
            "human_decisions": canonicalize_data([to_plain_data(approval) for approval in approvals]),
            "variables": variables_data,
        },
        "constraints": canonicalize_data(constraints or {}),
        "permissions": {
            "tool_permissions_digest": tool_permissions_digest,
            "effective": permissions_data,
        },
    }
    packet_id = context_id or _derived_context_id(base_packet)
    packet_without_rendering = {"packet_id": packet_id, **base_packet}
    packet_digest = digest_data(packet_without_rendering)
    canonical_bundle_digest = digest_data(
        {
            "prompt_bundle": prompt_bundle.canonical_data(),
            "context_packet_digest": packet_digest,
        }
    )
    packet_for_render = {
        **packet_without_rendering,
        "rendering": {
            "packet_digest": packet_digest,
            "canonical_bundle_digest": canonical_bundle_digest,
        },
    }
    rendered_input = render_prompt_bundle_input(prompt_bundle, packet_for_render)
    rendered_input_digest = hash_text(rendered_input)
    packet_data = {
        **packet_without_rendering,
        "rendering": {
            "packet_digest": packet_digest,
            "canonical_bundle_digest": canonical_bundle_digest,
            "rendered_input_digest": rendered_input_digest,
        },
    }
    packet = ContextPacket(
        schema_version=CONTEXT_PACKET_SCHEMA_VERSION,
        context_id=packet_id,
        workflow_id=workflow_id,
        instance_id=instance_id,
        stage_id=stage_id,
        stage_run_id=stage_run_id,
        input_digest=canonical_bundle_digest,
        rendered_digest=rendered_input_digest,
        prompt_refs=prompt_bundle.prompt_refs,
        artifact_refs=tuple(
            artifact for artifact in artifacts if isinstance(artifact, ArtifactRef)
        ),
        variables=dict(variables_data),
    )
    return RenderedContext(
        packet=packet,
        packet_data=packet_data,
        packet_digest=packet_digest,
        canonical_bundle_digest=canonical_bundle_digest,
        rendered_input=rendered_input,
        rendered_input_digest=rendered_input_digest,
        prompt_bundle=prompt_bundle,
        tool_permissions_digest=tool_permissions_digest,
    )


def render_prompt_bundle_input(prompt_bundle: PromptBundle, packet_data: Mapping[str, Any]) -> str:
    """Render ordered prompt layers and context packet into canonical text."""

    sections: list[str] = []
    for prompt in prompt_bundle.prompts:
        sections.append(
            "\n".join(
                [
                    f"--- prompt:{prompt.ref.kind} ---",
                    f"id: {prompt.ref.id}",
                    f"version: {prompt.ref.version}",
                    f"registry: {prompt.ref.registry}",
                    f"content_hash: {prompt.content_hash}",
                    "",
                    prompt.content.rstrip("\n"),
                ]
            )
        )
    sections.append("--- context-packet ---")
    sections.append(canonical_json(packet_data))
    return "\n\n".join(sections) + "\n"


def normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def hash_bytes(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def hash_text(value: str) -> str:
    return hash_bytes(normalize_text(value).encode("utf-8"))


def digest_data(value: Any) -> str:
    return hash_text(canonical_json(value))


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonicalize_data(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def canonicalize_data(value: Any) -> Any:
    plain = to_plain_data(value)
    if isinstance(plain, Mapping):
        return {str(key): canonicalize_data(plain[key]) for key in sorted(plain, key=str)}
    if isinstance(plain, list):
        return [canonicalize_data(item) for item in plain]
    return plain


def _load_mapping(path: Path) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        loaded = yaml.safe_load(text)
    else:
        loaded = _load_simple_yaml_mapping(text)
    if not isinstance(loaded, Mapping):
        raise PromptRegistryError(f"Registry index must be a mapping: {path}")
    return loaded


def _load_simple_yaml_mapping(text: str) -> Mapping[str, Any]:
    """Parse the simple registry.yaml shape without requiring PyYAML.

    This is intentionally narrow: top-level scalar keys plus ``prompts:`` as a
    list of scalar mappings. Full YAML belongs to PyYAML when it is installed.
    """

    stripped = text.strip()
    if stripped.startswith("{"):
        loaded = json.loads(stripped)
        if not isinstance(loaded, Mapping):
            raise PromptRegistryError("JSON registry index must be a mapping")
        return loaded

    data: dict[str, Any] = {}
    current_list: list[dict[str, Any]] | None = None
    current_item: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not raw_line.startswith(" "):
            key, value = _split_yaml_scalar(line)
            if value == "":
                current_list = []
                data[key] = current_list
                current_item = None
            else:
                data[key] = value
                current_list = None
                current_item = None
            continue
        if current_list is None:
            raise PromptRegistryError(f"Unsupported nested YAML line: {raw_line}")
        nested = line.strip()
        if nested.startswith("- "):
            current_item = {}
            current_list.append(current_item)
            nested = nested[2:].strip()
            if nested:
                key, value = _split_yaml_scalar(nested)
                current_item[key] = value
            continue
        if current_item is None:
            raise PromptRegistryError(f"List item field without item: {raw_line}")
        key, value = _split_yaml_scalar(nested)
        current_item[key] = value
    return data


def _split_yaml_scalar(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise PromptRegistryError(f"Unsupported YAML line: {line}")
    key, value = line.split(":", 1)
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    return key.strip(), value


def _default_render_mode(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".json":
        return "json"
    if suffix == ".txt":
        return "text"
    return "markdown"


def _layer_index(kind: str) -> int:
    try:
        return PROMPT_LAYER_ORDER.index(kind)
    except ValueError:
        return len(PROMPT_LAYER_ORDER)


def _derived_context_id(packet_data: Mapping[str, Any]) -> str:
    return f"ctx_{digest_data(packet_data).removeprefix('sha256:')[:20]}"


def _receipt_summary(receipt: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    data = canonicalize_data(receipt)
    if isinstance(data, Mapping) and "receipt_id" in data:
        return {
            "receipt_id": data.get("receipt_id"),
            "kind": data.get("kind"),
            "status": data.get("status"),
            "digest": digest_data(data),
        }
    return {"digest": digest_data(data), "data": data}
