# Worker Goal: Prompt Registry And Context Packets

## Goal

Design the versioned prompt registry and context packet contract for worker
stage execution.

## Scope

Own:

- `PromptRef`;
- prompt versioning;
- prompt content hashes;
- rendered context packet hashes;
- prompt registry layout;
- context packet structure for agents, reviewers, and scripts;
- what receipts must record for prompt provenance.

Do not own:

- workflow graph schema;
- runner retry behavior;
- OpenClaw-specific AGENTS/skill loading mechanics except as adapter examples.

## Expected Artifact

Write or update:

- `docs/synthesis/prompt-registry.md`

## Acceptance Criteria

- Separates standing identity prompts, lane prompts, stage prompts, policy
  envelope, and context packet.
- Defines receipt fields for prompt id, version, content hash, rendered input
  digest, model/runtime, and tool permissions.
- Explains how OpenClaw AGENTS/skills can map into the registry without making
  the kernel OpenClaw-specific.

