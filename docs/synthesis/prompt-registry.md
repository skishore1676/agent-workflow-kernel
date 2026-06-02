# Prompt Registry And Context Packets

## Purpose

The prompt registry makes agent inputs reproducible without making the kernel
care which host, lane, or agent runtime produced them. It owns prompt identity,
prompt versions, content hashes, render inputs, rendered-input digests, and the
receipt fields needed to prove what a stage saw when it ran.

The registry is not a hidden domain engine. Domain facts, artifacts, human
decisions, and lane-specific instructions travel as explicit context or
registered lane prompts. Host mechanisms such as OpenClaw `AGENTS.md` files or
Codex skills can feed the registry through an adapter, but they are not kernel
primitives.

## Contract Summary

Each executable `StageRun` resolves a `PromptRef` set, renders a
`ContextPacket`, invokes a runtime or script, and records immutable prompt
provenance in the receipt.

```text
StageDef.prompt_refs
  -> PromptRegistry.resolve(refs)
  -> PromptBundle(identity + policy + lane + stage)
  -> ContextPacket.render(bundle, workflow state, artifacts, approvals)
  -> RuntimeAdapter.invoke(rendered_input, permissions)
  -> Receipt(prompt provenance + rendered digest + runtime/tool evidence)
```

The kernel owns the shape of these contracts. Adapters own how host-native
prompt sources are discovered, normalized, and rendered into them.

## Prompt Layers

Prompt layers must stay separate so that receipts can explain whether behavior
came from a standing identity, a lane rule, a stage task, a policy envelope, or
the per-run context.

| Layer | Kernel Role | Typical Owner | Versioned | Receipt Requirement |
| --- | --- | --- | --- | --- |
| Standing identity prompt | Stable worker identity, collaboration style, output discipline, global constraints | Kernel distribution or host adapter | yes | prompt id, version, content hash |
| Policy envelope | Risk class, approval boundaries, tool allowlist, budget, redaction rules | Kernel policy plus workflow override | yes | policy id, version, content hash, permissions digest |
| Lane prompt | Domain or workflow-family guidance such as quality review, editorial review, or research gating | Workflow/lane package | yes | prompt id, version, content hash |
| Stage prompt | Concrete instructions for a specific stage type or named stage | Workflow definition | yes | prompt id, version, content hash |
| Context packet | Per-run facts: goal, artifacts, state, prior receipts, human decisions, variables | Runner at `StageRun` time | no, but schema-versioned | rendered input digest and context packet digest |

Recommended composition order:

1. standing identity prompt;
2. policy envelope;
3. lane prompt;
4. stage prompt;
5. context packet.

The order is part of the rendered input digest. A runtime adapter may translate
the bundle into messages, files, command arguments, or structured script input,
but the receipt must still record the canonical ordered bundle the adapter was
given.

## PromptRef

`PromptRef` is the workflow-facing pointer to versioned prompt content.

```yaml
prompt_ref:
  id: stage.quality_review.propose_fix
  kind: stage
  version: 1.2.0
  registry: local
  render_mode: markdown
  required: true
```

Fields:

- `id`: stable logical identifier, unique within a registry namespace.
- `kind`: one of `identity`, `policy`, `lane`, `stage`, or `adapter_source`.
- `version`: exact semantic version or immutable revision. Stage runs should
  resolve to an exact version before execution.
- `registry`: namespace such as `local`, `kernel`, `host.openclaw`, or a package
  name. This prevents portable workflow IDs from colliding with host imports.
- `render_mode`: `markdown`, `json`, `yaml`, `text`, or adapter-declared mode.
- `required`: whether missing content blocks execution.

Prompt references in workflow definitions should be exact for production
workflows. Floating selectors such as `latest` are allowed only before a
`WorkflowInstance` starts; the runner must pin them to exact versions and hashes
before the first stage run.

## Prompt Registry Layout

A concrete local registry can live under `prompts/` while still allowing other
registries later.

```text
prompts/
  registry.yaml
  identities/
    portable-worker/
      v1.0.0.md
      v1.1.0.md
  policies/
    default-human-gates/
      v1.0.0.yaml
    no-external-effects/
      v1.0.0.yaml
  lanes/
    quality-review/
      v1.0.0.md
    editorial-a2a/
      v1.0.0.md
    trading-research/
      v1.0.0.md
  stages/
    propose/
      v1.0.0.md
    review/
      v1.0.0.md
    human-approval-summary/
      v1.0.0.md
  adapters/
    openclaw/
      imported-agents/
        v2026-05-31.yaml
      imported-skills/
        v2026-05-31.yaml
```

`prompts/registry.yaml` is an index, not the source of truth for prompt content:

```yaml
schema_version: prompt-registry.v1
registry_id: local
prompts:
  - id: identity.portable_worker
    kind: identity
    version: 1.0.0
    path: identities/portable-worker/v1.0.0.md
    content_hash: sha256:6b3a...
    status: active
  - id: policy.no_external_effects
    kind: policy
    version: 1.0.0
    path: policies/no-external-effects/v1.0.0.yaml
    content_hash: sha256:2f91...
    status: active
  - id: lane.quality_review
    kind: lane
    version: 1.0.0
    path: lanes/quality-review/v1.0.0.md
    content_hash: sha256:ab42...
    status: active
  - id: stage.review
    kind: stage
    version: 1.0.0
    path: stages/review/v1.0.0.md
    content_hash: sha256:401f...
    status: active
```

Content hashes are computed over canonical bytes of the referenced file after
line-ending normalization and before variable rendering. The registry index can
cache the hash, but the runner should verify it before execution.

## Policy Envelope

The policy envelope is a prompt-adjacent contract, not a prose-only reminder. It
contains the risk and permission data the runner can enforce before invoking a
runtime adapter.

```yaml
schema_version: policy-envelope.v1
id: policy.no_external_effects
version: 1.0.0
risk_class: low
approval_required_for:
  - public_publish
  - external_send
  - auth_change
  - live_trade
  - money_movement
  - destructive_change
tool_permissions:
  shell:
    allowed: true
    network: false
    write_paths:
      - ${workflow.workspace}
  browser:
    allowed: false
  connectors:
    allowed: []
budget:
  max_runtime_seconds: 1800
  max_tokens: 200000
redaction:
  secrets: block
  receipts: summarize_sensitive_values
```

Adapters may apply stronger host-local policy. They may not weaken the resolved
policy envelope without a recorded human approval receipt.

## Context Packet

The context packet is the bounded, schema-versioned input for one stage run. It
contains facts and pointers, not standing behavior that should live in a prompt
layer. It should be small enough to inspect, hash, and replay.

```yaml
schema_version: context-packet.v1
packet_id: ctx_01HX5A7P9TR4
workflow:
  id: wf.quality_review
  version: 0.3.0
  instance_id: wi_2026_05_31_001
stage:
  id: review_patch
  type: agent_work
  run_id: sr_2026_05_31_001_02
  attempt: 1
actor:
  role: reviewer
  runtime_target: codex
prompt_bundle:
  identity:
    id: identity.portable_worker
    version: 1.0.0
    content_hash: sha256:6b3a...
  policy:
    id: policy.no_external_effects
    version: 1.0.0
    content_hash: sha256:2f91...
  lane:
    id: lane.quality_review
    version: 1.0.0
    content_hash: sha256:ab42...
  stage:
    id: stage.review
    version: 1.0.0
    content_hash: sha256:401f...
inputs:
  objective: Review the proposed patch and return blocking findings first.
  artifacts:
    - id: artifact.patch
      kind: git_diff
      uri: artifact://wi_2026_05_31_001/patch.diff
      digest: sha256:d120...
    - id: artifact.test_log
      kind: command_output
      uri: artifact://wi_2026_05_31_001/test-log.txt
      digest: sha256:ee83...
  prior_receipts:
    - receipt_id: rcpt_sr_2026_05_31_001_01
      digest: sha256:9930...
  human_decisions: []
  variables:
    repo_name: agent-workflow-kernel
    branch: codex/example
constraints:
  required_outputs:
    - review_verdict
    - findings
  max_findings: 10
permissions:
  tool_permissions_digest: sha256:c774...
  effective_tools:
    - id: shell.read_only
      allowed: true
    - id: git.diff
      allowed: true
rendering:
  canonical_bundle_digest: sha256:91df...
  rendered_input_digest: sha256:5ab0...
```

`canonical_bundle_digest` covers the normalized prompt bundle and context packet
before adapter-specific rendering. `rendered_input_digest` covers the exact
runtime input after the adapter renders messages, command arguments, or script
payload. Both belong in receipts because they answer different questions:

- bundle digest: did the kernel assemble the same logical input?
- rendered digest: did the runtime receive the same concrete input?

## Rendering Rules

Rendering must be deterministic for a given registry snapshot and workflow
state.

- Normalize prompt bytes and context packet JSON/YAML before hashing.
- Resolve all template variables before runtime invocation.
- Record missing optional values as explicit `null` or empty arrays.
- Sort object keys in canonical digests.
- Include composition order in the digest input.
- Redact or block secrets before hashing rendered runtime input when policy
  requires it; record the redaction mode in the receipt.
- Do not include volatile data such as wall-clock timestamps unless the stage
  explicitly declares them as inputs.

Runtime-specific message formats are adapter details. The kernel receipt should
record enough canonical data to reproduce the adapter call even if the host
adapter later changes implementation.

## Receipt Fields

Every `StageRun` receipt that invokes an agent, reviewer, or script must include
prompt provenance and effective permissions.

```yaml
prompt_provenance:
  registry_snapshot_digest: sha256:a61f...
  refs:
    - layer: identity
      id: identity.portable_worker
      version: 1.0.0
      content_hash: sha256:6b3a...
    - layer: policy
      id: policy.no_external_effects
      version: 1.0.0
      content_hash: sha256:2f91...
    - layer: lane
      id: lane.quality_review
      version: 1.0.0
      content_hash: sha256:ab42...
    - layer: stage
      id: stage.review
      version: 1.0.0
      content_hash: sha256:401f...
context:
  packet_id: ctx_01HX5A7P9TR4
  packet_schema_version: context-packet.v1
  packet_digest: sha256:ff4d...
  canonical_bundle_digest: sha256:91df...
  rendered_input_digest: sha256:5ab0...
runtime:
  adapter_id: runtime.codex
  adapter_version: 0.1.0
  model: gpt-5-codex
  model_version: 2026-05-31
  host_runtime: codex-desktop
tool_permissions:
  policy_id: policy.no_external_effects
  policy_version: 1.0.0
  content_hash: sha256:2f91...
  effective_permissions_digest: sha256:c774...
  granted:
    - shell.read_only
    - git.diff
  denied:
    - external_send
    - live_trade
```

Required receipt fields:

- prompt id for every resolved prompt layer;
- prompt version for every resolved prompt layer;
- prompt content hash for every resolved prompt layer;
- context packet digest;
- rendered input digest;
- model and runtime adapter identity;
- runtime adapter version when available;
- tool permissions, including the effective permissions digest;
- policy envelope id, version, and content hash;
- redaction mode if any input was redacted before rendering or hashing.

Receipts should also record the command, connector, or surface invocation
metadata appropriate to the adapter family, but those fields belong to the
broader adapter/receipt contract rather than this prompt registry document.

## OpenClaw Mapping Without Kernel Coupling

OpenClaw can be the first reference host without leaking OpenClaw concepts into
the kernel. The mapping should happen in the OpenClaw host adapter:

| OpenClaw/Codex Source | Registry Mapping | Kernel Sees |
| --- | --- | --- |
| repo or directory `AGENTS.md` | imported identity, lane, or adapter-source prompt depending on scope | `PromptRef` plus source metadata |
| Codex skill `SKILL.md` | adapter-source prompt or lane prompt with capability metadata | prompt id, version, content hash |
| tool/plugin allowlists | policy envelope and effective tool permissions | policy id, permissions digest |
| OpenClaw lane instructions | lane prompt | generic `kind: lane` |
| OpenClaw task packet | context packet inputs and artifact refs | `ContextPacket` |
| Blackboard/Northstar/Telegram receipts | artifact refs, human decisions, surface receipts | generic receipt/artifact fields |

The adapter may build an import manifest such as:

```yaml
schema_version: adapter-prompt-import.v1
adapter_id: host.openclaw
snapshot_id: openclaw-prompts-2026-05-31
sources:
  - source_kind: agents_file
    source_uri: openclaw://repo/AGENTS.md
    source_hash: sha256:019a...
    mapped_prompt:
      id: host.openclaw.agents.root
      kind: adapter_source
      version: 2026.05.31+019a
      content_hash: sha256:019a...
  - source_kind: skill
    source_uri: openclaw://skills/review/SKILL.md
    source_hash: sha256:77c1...
    mapped_prompt:
      id: host.openclaw.skill.review
      kind: adapter_source
      version: 2026.05.31+77c1
      content_hash: sha256:77c1...
```

Portable workflow definitions should not reference raw OpenClaw paths. They
reference generic prompts such as `identity.portable_worker` or
`lane.quality_review`. The OpenClaw adapter can satisfy those references from
OpenClaw-native files during incubation, then later swap in packaged registry
prompts without changing workflow definitions.

This keeps the boundary clean:

- kernel: prompt refs, prompt versions, hashes, context packet schema, receipt
  requirements;
- OpenClaw adapter: how to discover `AGENTS.md`, skills, oldmac paths, and
  operator surfaces;
- lane package: which lane prompts and stage prompts a workflow chooses.

## Script And Deterministic Stage Inputs

Scripts also receive context packets. They may ignore prose prompt layers, but
their receipts still need context and policy provenance.

For a `system_action` stage, the `PromptBundle` can contain only a policy
envelope and an optional stage contract. The rendered input digest then covers
the structured script payload instead of chat messages. This prevents scripts
from becoming an untracked escape hatch around approval gates or prompt/context
auditing.

## Compatibility And Migration Notes

The first implementation can be file-backed:

- `prompts/registry.yaml` for prompt metadata;
- prompt body files under `prompts/{identities,policies,lanes,stages}`;
- `context-packets/` or run-local artifact storage for rendered packets;
- receipt JSON with the required prompt/context/runtime fields.

Later implementations can move the registry into a package store or database if
needed. The stable contract should remain `PromptRef`, resolved prompt hashes,
`ContextPacket`, rendered input digest, and receipt provenance.

## Acceptance Checklist

- Standing identity prompts, policy envelope, lane prompts, stage prompts, and
  context packets are separate layers.
- Prompt ids, exact versions, content hashes, rendered input digest,
  model/runtime, and tool permissions are receipt-required.
- OpenClaw `AGENTS.md` files and skills map through the host adapter as imported
  prompt sources rather than kernel concepts.
- The registry layout works as a local file-backed implementation.
- The example context packet is concrete enough for agent, reviewer, and script
  stages to share the same provenance model.
