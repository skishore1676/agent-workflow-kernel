# Wave 22 Prompt Rails Hardening

Date: 2026-06-01

## Trigger

Suman noticed that the Jarvis weekly improvement cargo artifact had been
created from an ad hoc operator prompt stored in an OpenClaw session log, not
from a clean AWK prompt-registry contract. The review card worked, but the
prompt intent was not reusable or auditable from AWK.

## Implemented

- Added versioned prompt contracts:
  - `stage.jarvis_weekly.improvement_cargo`
  - `stage.openclaw.cutover_review_artifact`
- Added `scripts/render_openclaw_prompt_profile.py` so a caller can render an
  injectable, versioned prompt profile for:
  - `jarvis_weekly_improvement_cargo`
  - `openclaw_cutover_review_weekly`
  - `openclaw_cutover_review_ivy`
- OpenClaw cutover review notes now include prompt provenance:
  - prompt bundle hash;
  - context packet ref;
  - rendered input hash;
  - resolved prompt refs.
- Blackboard pointer records now preserve prompt provenance under the `awk`
  record block.
- Weekly and Ivy cutover cards now use lane-specific decision labels instead of
  the generic `acknowledged`, `needs_follow_up`, `blocked` tuple:
  - Weekly: `approve_next_weekly_test`, `request_weekly_follow_up`,
    `block_weekly_cutover`
  - Ivy: `accept_ivy_cutover`, `request_ivy_revision`, `block_ivy_cutover`

## Companion OpenClaw Patch

OpenClaw's `ingest_agent_reviews.py` now maps the new lane-specific AWK labels
to the existing generic actions:

- `approve_next_*` and `accept_*` -> `continue_awk_workflow`
- `request_*` -> `awk_follow_up`
- `block_*` -> `awk_blocked`

This keeps the surface language lane-specific while preserving the generic
handoff machinery.

## Verification

AWK:

```bash
scripts/check.sh
```

Result: 202 unittest tests passed and 202 pytest tests passed.

OpenClaw:

```bash
python3 -m unittest workspace-main/tests/test_ingest_agent_reviews.py
```

Result: 17 tests passed.

## Remaining

- Wire the prompt-profile renderer into the next real Jarvis cargo invocation
  so the generated artifact itself is produced from
  `jarvis_weekly_improvement_cargo`, not an operator-written one-off prompt.
- After syncing oldmac, regenerate a clean live review packet and confirm the
  Blackboard card shows the lane-specific labels and prompt provenance.

