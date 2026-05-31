# Worker Goal: Runner, Storage, And Recovery

## Goal

Design the kernel runner loop, SQLite state model, leases, retries, validation,
and recovery behavior.

## Scope

Own:

- execution loop;
- claim/lease semantics;
- SQLite tables;
- JSON export/import role;
- stage attempts;
- stale child sessions;
- validation hooks;
- idempotency;
- recovery and blocked states.

Do not own:

- concrete OpenClaw subprocess/session implementation;
- human review UI;
- domain-specific artifacts.

## Expected Artifact

Write or update:

- `docs/synthesis/runner-recovery.md`

## Acceptance Criteria

- Distinguishes runtime failure, invalid output, human rejection, policy denial,
  dependency unavailable, and deterministic test failure.
- Defines when retries are safe and when human approval is required.
- Gives a minimal SQLite table sketch.
- Includes recovery after supervisor/thread interruption.

