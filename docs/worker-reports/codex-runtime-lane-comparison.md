# Codex Runtime Lane Comparison

## Verdict

PASS. The experiment wired the concrete `ivy_jonah_editorial.build_draft_package`
stage to `runtime.codex_cli_session`, ran it against a local Ivy/Jonah fixture,
and compared it with the read-only OpenClaw fixture path and a deterministic
local-script alternative.

For this stage shape, `runtime.codex_cli_session` produced the richest editorial
draft package but was token-heavy. The deterministic local script was most
efficient and equally accurate for structured source-trail packaging. The
OpenClaw fixture path remains the right owner for adoption/parity receipts, but
it lacks token accounting and generated draft text, so it cannot answer stage
economics on equal footing yet.

## Stage And Fixture

- Workflow: `workflows/ivy_jonah_editorial.yaml`
- Stage: `build_draft_package`
- Original adapter: `runtime.agent`
- Experiment adapter: `runtime.codex_cli_session`
- Fixture: `fixtures/openclaw/ivy_jonah/p3_approval_to_p5_shadow.json`
- Artifact packet: `.awk-live/runtime-comparison-20260601/`

## Commands Run

```bash
python3 -m unittest tests/test_codex_runtime_lane_comparison.py
python3 scripts/codex_runtime_lane_comparison.py --run-real-codex --timeout-seconds 300
```

Focused tests passed: 2 tests OK.

## Metrics

| Path | Status | Wall time | Tokens | Quality | Receipt clarity |
| --- | --- | ---: | ---: | --- | --- |
| `codex_cli_session` | succeeded | 66.290s | 203493 total | 10/10 excellent | High: session id, reuse flag, usage, last message, stderr, JSONL events |
| `openclaw_fixture` | succeeded | 0.001s | unavailable | 6/10 receipt-parity-only | Medium-high: mapped stage receipts and artifact roles, no native token/text primitive |
| `direct_script` | succeeded | 0.000s | 0 model tokens | 10/10 excellent | High for deterministic fields, no reasoning transcript |

Codex session facts:

- Session id: `019e8699-a920-7fd2-ad3c-fc761f4a91a0`
- Session reused for the stage turn: true
- Turn count: 2
- Seed turn: 24554 total tokens
- Stage turn: 178939 total tokens

OpenClaw fixture facts:

- Runtime refs were present for Ivy and Jonah native sessions.
- The read-only fixture mapped `build_draft_package` to a succeeded stage with
  `draft_package` and `source_trail` artifact roles.
- Missing primitive: no fixture/read-only receipt currently exposes native
  OpenClaw token accounting or generated draft text for direct quality scoring.

## Runtime Ownership Recommendation

- Use `direct_script` for deterministic fixture extraction, source-trail
  packaging, hash validation, and readback classification.
- Use `runtime.codex_cli_session` for drafting, revision synthesis, nuanced
  judgment, or multi-turn continuity that is worth the context overhead.
- Prefer one-shot/direct model/script paths over Codex CLI session for small
  isolated stages where continuity is not valuable.
- Keep OpenClaw-backed fixture/live-readonly paths as the owner for legacy
  parity, adoption evidence, and production-boundary receipts until a cutover is
  explicitly approved.
- No runtime owns public publish, external send, auth, trading, or destructive
  actions without an explicit Suman approval gate.

## Smallest Next Action

Add token accounting to the OpenClaw live-readonly exporter or receipts so future
comparisons can measure OpenClaw native sessions against Codex CLI sessions with
the same token fields.

## Safety

No production OpenClaw behavior, Northstar/Obsidian, Telegram, auth, trading,
public publish, launchd, cron, or production prompts were mutated. The generated
comparison artifacts are ignored under `.awk-live/`.
