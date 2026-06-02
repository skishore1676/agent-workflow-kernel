# Wave 18 Cross-Ledger Crosswalk

Date: 2026-06-01

## Verdict

AWK now records an explicit OpenClaw identity crosswalk for the owned-completion
bridge. The crosswalk is adapter-owned, not portable-kernel schema, and it binds
one AWK workflow instance to the OpenClaw artifact, lane, handoff file, runner
receipt, terminal event, and Work Ledger identifiers that were visible during
the bridge run.

The bridge still performs no OpenClaw, oldmac, Obsidian, Telegram, auth,
trading, deployment, or live runtime mutation. It writes only the selected local
AWK SQLite ledger.

## Implemented Behavior

Code:

- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/mapping.py`
- `packages/adapters/openclaw/agent_workflow_kernel_openclaw/owned_completion.py`
- `tests/test_openclaw_owned_completion_bridge.py`

New adapter model/API:

- `OPENCLAW_IDENTITY_CROSSWALK_SCHEMA`
- `OpenClawIdentityCrosswalk`
- `openclaw_identity_crosswalk_conflicts`

Crosswalk fields include:

- AWK instance ID, workflow ID, and workflow version.
- Current stage ID and terminal stage ID.
- OpenClaw artifact ID and lane ID.
- OpenClaw artifact record path, handoff path, and runner receipt path.
- Work Ledger `work_ledger_id`, `work_id`, `work_item_id`, handoff ID, and
  receipt ID when present in source JSON.
- Source hashes for artifact record, handoff, and runner receipt.
- Terminal `workflow_terminal` event ID when available.

Persistence:

- Successful bridge summaries now include `identity_crosswalk`,
  `identity_crosswalk_hash`, `identity_crosswalk_status`, and
  `identity_crosswalk_errors`.
- The AWK ledger gets append-only `openclaw_identity_crosswalk_recorded` events
  containing the crosswalk metadata and hash.
- Reruns with the same crosswalk hash return `already_recorded` and do not
  append duplicate crosswalk events.
- Reruns that advance from non-terminal to terminal may append a refined
  crosswalk event, because the terminal event ID and terminal stage become known.

Mismatch handling:

- Artifact record, handoff, and runner receipt JSON are checked for mismatched
  `artifact_id` / `openclaw_artifact_id` and lane IDs before importing a
  Blackboard decision.
- Existing recorded crosswalks are compared against candidate crosswalks on
  immutable identity fields. Conflicts are recorded as
  `openclaw_identity_crosswalk_rejected` events.
- A crosswalk rejection makes the bridge result `identity_mismatch` and the
  summary `ok: false`, even if the underlying AWK workflow instance was already
  terminal from a prior valid run. This prevents a stale or different OpenClaw
  artifact/handoff/receipt identity from being silently treated as the same
  terminal work item.

## Test Coverage

Added or strengthened coverage for:

- Terminal owned-completion summaries exposing crosswalk metadata, source
  hashes, terminal stage ID, terminal event ID, and Work Ledger IDs.
- Rerun idempotence: terminal events stay at one, and a same-hash terminal
  crosswalk is not appended again.
- Mismatched artifact record IDs reject the crosswalk.
- Mismatched handoff artifact IDs reject the crosswalk before acknowledgement
  import.
- Mismatched runner receipt artifact IDs reject the crosswalk.
- A terminal rerun with a changed Work Ledger receipt ID rejects the new
  crosswalk while preserving the already-terminal AWK ledger state and avoiding
  duplicate terminal events.

## Verification

Commands run:

```bash
python3 -m unittest tests.test_openclaw_owned_completion_bridge
python3 -m unittest discover -s tests
./scripts/check.sh
```

Results:

- Focused bridge tests: 7 passed.
- Full unittest suite: 188 passed.
- `./scripts/check.sh`: 188 passed under unittest; venv pytest was skipped
  because `.venv/bin/python` is missing.

## Remaining Limits

- The crosswalk records Work Ledger identifiers only when they are present in
  the OpenClaw source JSON. It does not query a live Work Ledger database.
- The bridge persists crosswalks as ledger events rather than a dedicated table.
  That matches current AWK append-only evidence patterns but is not optimized for
  large analytical joins.
- No oldmac live readback was performed in this worker slice by design; the
  safety packet required local repo only and no live mutation.
