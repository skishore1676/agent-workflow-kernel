# Wave 21 Review Surface Hardening

Date: 2026-06-01

## Objective

Make AWK-generated human review cards match the older OpenClaw/Northstar review
experience: the operator should see the actual artifact being approved, not only
filesystem evidence paths.

## Older Card Pattern

Good historical cards placed the review payload directly in the note:

- OR P5 cards used `## Article To Review` with the full article body inline.
- Attention cards used `## Source Artifact Excerpt`.
- OpenClaw/Bumblebee cards included the actual recommendation/proposal body near
  the approval boundary.

The AWK cutover cards were structurally valid but too thin: they exposed
`Review Context`, raw `Evidence`, and `Decision` without the human-review cargo.

## Implemented Fix

- Added generic `artifact_title`, `artifact_intro`, `artifact_link`, and
  `artifact_markdown` support to Local Markdown, sandbox Obsidian, and live
  Obsidian surface adapters.
- Review cards now render `## Artifact To Review` before `## Evidence` and
  `## Decision`.
- Adapter receipts include an `artifact_review` summary with title, link, and
  embedded/body presence.
- OpenClaw live cutover now embeds lane-specific review artifacts for:
  - Jarvis weekly update
  - Ivy/Jonah editorial
- Blackboard pointer records now carry `source_artifact_path` and `summary_path`
  in addition to the review note link.

## Verification

Local:

```bash
python3 -m unittest tests.test_live_operator_surface_adapters tests.test_openclaw_live_cutover
scripts/check.sh
```

Result: 199 unit tests passed and 199 pytest tests passed.

oldmac:

```bash
cd /Users/sunny/code/agent-workflow-kernel
.venv/bin/python -m unittest tests.test_live_operator_surface_adapters tests.test_openclaw_live_cutover
scripts/check.sh
```

Result: 199 unit tests passed and 199 pytest tests passed.

## Live Restart Packet

Generated on oldmac:

- Receipt:
  `/Users/sunny/code/agent-workflow-kernel/.awk-live/review-surface-hardening/20260601T154823Z/cutover_receipt.json`
- Ivy review note:
  `/Users/sunny/vaults/northstar/03 Agent Org/main/OpenClaw/Reviews/AWK/restart-20260601T154823Z/ivy/cutover-review.md`
- Weekly review note:
  `/Users/sunny/vaults/northstar/03 Agent Org/main/OpenClaw/Reviews/AWK/restart-20260601T154823Z/weekly/cutover-review.md`

Readback confirmed:

- both notes exist;
- both notes include `## Artifact To Review`, `## Evidence`, and `## Decision`;
- Ivy note includes `### Ivy/Jonah Boundary`;
- Weekly note includes `### Jarvis Weekly Boundary`;
- Blackboard records were refreshed and point to the new review notes;
- Telegram remained sandboxed/not sent.

