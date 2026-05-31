# Wave 4 OpenClaw Shadow Runner

The OpenClaw shadow runner is an AWK-side proof harness for exported
OpenClaw lane fixtures. It intentionally runs from local JSON only:

```bash
python3 scripts/openclaw_shadow_run.py --fixture fixtures/openclaw/shadow_runner/generic_readonly_fixture.json --report -
```

The runner reads an exported fixture, passes it through the read-only
OpenClaw adapter, compares the generated AWK receipt against the supplied host
receipt when one is present, and emits deterministic JSON. If a fixture does
not include `expected_host_receipt`, the report records that fact and compares
the generated receipt to itself so the parity section remains stable and
machine-readable.

Report shape:

- `fixture_identity`: fixture id, schema, lane, generated timestamp, and source
  root when provided.
- `mapping_summary`: lane id, agent id, host ref, Work Ledger ids, and observed
  surface/runtime reference counts.
- `receipts_generated`: read-only adapter receipts produced by AWK.
- `parity_report`: the existing deterministic parity report payload.
- `adoption`: combined read-only, parity, and adoption status.
- `blocked_external_actions`: explicit effects this shadow run refuses to
  perform.
- `next_recommended_adoption_step`: the next safe takeover step.

Ivy/Jonah and Jarvis weekly fixtures are detected by lane name or by
lane-specific payload keys (`ivy`, `weekly_update`). Until their lane adapter
branches are present in the worktree, the runner produces an `adapter_missing`
adoption report rather than failing. Those reports still preserve the read-only
boundary and add lane-specific blocked actions such as public publish or
Blackboard/Obsidian writes.

The runner does not call OpenClaw, oldmac, Telegram, Obsidian, Northstar,
cron, brokers, credentials, or deployment surfaces. The only write it performs
is the explicit `--report <path>` output.
