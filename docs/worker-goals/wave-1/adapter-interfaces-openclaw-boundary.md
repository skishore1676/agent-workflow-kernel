# Worker Goal: Adapter Interfaces And OpenClaw Boundary

## Goal

Design the adapter interfaces and the OpenClaw host-adapter boundary.

## Scope

Own:

- runtime adapter interface;
- surface adapter interface;
- host adapter interface;
- lane adapter interface;
- OpenClaw-specific boundary;
- how current Work Ledger/Blackboard/A2A concepts map without contaminating the
  portable kernel.

Do not own:

- implementation of adapters;
- workflow DSL internals;
- prompt registry internals.

## Expected Artifact

Write or update:

- `docs/synthesis/adapter-interfaces.md`
- `docs/synthesis/openclaw-adapter.md`

## Acceptance Criteria

- Kernel has no `/Users/sunny`, Northstar, Telegram, or OpenClaw agent path
  assumptions.
- OpenClaw adapter can call current Work Ledger/A2A/Blackboard compatibility
  paths.
- Identifies code that should be reused, wrapped, or avoided.

