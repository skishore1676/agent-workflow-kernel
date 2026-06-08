# Portable Surface Profiles

AWK should not become an integration hub for Obsidian, Telegram, Apple Notes,
Google Keep, X, or any other physical surface. The kernel owns workflow state,
policy gates, action fingerprints, receipts, artifact hashes, and adapter
contracts. Host projects own local paths, credentials, app automation, and
operator preferences.

The useful abstraction is a semantic surface profile:

```text
workflow stage says: surface.human_review
host profile maps:   surface.human_review -> surface.obsidian_live_markdown
host can remap:      surface.human_review -> surface.apple_notes_review
```

The workflow does not change when Suman changes surfaces. The host profile and
registered surface adapter change.

## Boundary

AWK owns:

- `SurfaceAdapter` and `SurfaceRef` contracts.
- `SurfaceCapabilityContract` metadata.
- semantic refs such as `surface.human_review`, `surface.notification`, and
  `surface.public_publish`.
- human-gate packets, action fingerprints, and approval receipts.
- fail-closed resolution when a semantic ref has no host binding or a concrete
  adapter lacks required operations.

Host repositories such as OpenClaw own:

- Obsidian vault paths and note formats.
- Telegram bot/chat configuration and send policy.
- Apple Notes, Google Keep, or other local app automation.
- OAuth, tokens, secrets, and account-specific setup.
- launchd scheduling and live readback for Suman's machines.

## Profile Shape

```yaml
schema: surface.profile.v1
profile:
  id: openclaw-obsidian-primary
  description: Suman local host profile.
  bindings:
    - semantic_ref: surface.human_review
      adapter_id: surface.obsidian_live_markdown
      surface_kind: obsidian_note
      mode: live
      required_operations:
        - publish
        - readback
        - ingest_decisions
      fallback_adapter_ids:
        - surface.telegram_dry_run
```

The binding says which concrete adapter should satisfy a semantic surface need.
It does not contain secrets, tokens, or app-specific code.

## Workflow Pattern

Workflow YAML should prefer semantic surface refs:

```yaml
- id: p5_final_approval
  type: human_gate
  adapter: surface.human_review
  policy:
    class: public_publish
    requires_explicit_approval: true
  outcomes:
    - approve_packet
    - revise
    - park
    - reject
```

A runner or host bootstrap can resolve `surface.human_review` through a
`SurfaceProfile` before registering or invoking concrete adapters. This keeps
the workflow portable while still letting a host choose Obsidian today and a
different notes surface tomorrow.

## Protected Gates

Surface profiles do not grant permission to mutate live surfaces. They only
resolve adapter identity. Live adapters still need policy checks, exact action
fingerprints, idempotency keys, and readback receipts.

Public posting should be a separate semantic ref, for example
`surface.public_publish`, with a stricter concrete adapter than the review
surface. A draft approval surface must not double as an external publish
permission unless the policy gate binds to that exact public-send action.

## OpenClaw Implication

OpenClaw should move scattered surface scripts toward production
implementations of AWK surface contracts:

- `surface.human_review` -> Obsidian review note or another operator surface.
- `surface.notification` -> Telegram or another notification channel.
- `surface.public_publish` -> explicit, approval-bound public-send adapter.

This keeps AWK portable and lets OpenClaw remain the Suman-specific host layer.
