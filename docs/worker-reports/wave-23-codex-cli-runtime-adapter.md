# Wave 23: Codex CLI Runtime Adapter

## Verdict

PASS for first useful proof. AWK can invoke native Codex CLI directly and can
reuse a bounded Codex session through an explicit captured session id.

## Built

- `runtime.codex_cli_exec` for one-shot `codex exec`.
- `runtime.codex_cli_session` for bounded reusable `codex exec resume`.
- `codex_cli_runtime_registrations()` for runner installation.
- Durable `.awk-live/codex-cli/` style artifacts for last message, JSONL events,
  and stderr.
- Explicit failure if session mode cannot capture a reusable session id.

## Verified

Commands:

```bash
python3 -m unittest tests/test_codex_cli_runtime_adapter.py tests/test_packaging_discovery.py
python3 scripts/codex_cli_runtime_smoke.py --run-real --mode session --timeout-seconds 300
./scripts/check.sh
ssh oldmac 'cd /Users/sunny/code/agent-workflow-kernel && python3 -m unittest tests/test_codex_cli_runtime_adapter.py tests/test_packaging_discovery.py'
ssh oldmac 'cd /Users/sunny/code/agent-workflow-kernel && python3 scripts/codex_cli_runtime_smoke.py --run-real --mode session --timeout-seconds 300'
```

Results:

- Focused tests: 7 passed.
- Full check: 221 unittest cases passed and 221 pytest cases passed.
- Live smoke: captured session id `019e8689-c031-7f62-af8b-cf3ad86005a4`,
  resumed it, and recalled the nonce from the prior turn.
- oldmac focused tests: 7 passed.
- oldmac live smoke: captured session id `019e868c-5ab9-7f70-9d89-72d5147c8dba`,
  resumed it, and recalled the nonce from the prior turn.

## Risks

- Tiny live Codex CLI smokes still carry meaningful context overhead. Use the
  session adapter when continuity has real value, not as a casual ping loop.
- Normal operation should prefer explicit session ids from receipts/ledger
  outputs. `--last` remains a manual escape hatch, not the runner contract.

## Next Action

Wire one non-production AWK lane stage to `runtime.codex_cli_session` and compare
artifact quality plus token usage against the OpenClaw-backed runtime.
