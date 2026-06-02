# Wave 24: Codex SDK Session Runtime Adapter

## Verdict

PASS. AWK now has a preferred `runtime.codex_sdk_session` adapter using the
official `openai-codex` SDK, while `runtime.codex_cli_session` remains the
explicit fallback.

## Built

- `runtime.codex_sdk_session` for bounded reusable Codex SDK threads.
- `codex_sdk_runtime_registrations()` for runner installation.
- Optional `codex-sdk` dependency extra for `openai-codex`.
- Durable `.awk-live/codex-sdk/` style artifacts for final message, turn result,
  and SDK metadata.
- Explicit failure artifacts when `openai-codex` is unavailable or the SDK
  runtime/auth path fails.
- Two-turn live smoke script under `scripts/codex_sdk_runtime_smoke.py`.

## Verification Plan

Commands:

```bash
python3 -m unittest tests/test_codex_sdk_runtime_adapter.py tests/test_packaging_discovery.py
python3 -m unittest tests/test_codex_cli_runtime_adapter.py tests/test_packaging_discovery.py
.venv/bin/python -m pip install -e '.[dev,codex-sdk]'
./scripts/check.sh
.venv/bin/python scripts/codex_sdk_runtime_smoke.py --run-real --timeout-seconds 300
```

Results:

- Focused SDK plus packaging tests: 8 passed.
- Existing CLI plus packaging tests: 8 passed.
- Full check: 226 unittest cases passed and 226 pytest cases passed.
- Local SDK package install: `openai-codex==0.1.0b2` and
  `openai-codex-cli-bin==0.132.0` installed in `.venv`.
- Live SDK smoke: two-turn thread succeeded under
  `.awk-live/codex-sdk-smoke/20260602T043253Z`.
- Live SDK thread id: `019e869b-2038-7f10-bd74-282f57b807b7`.
- Live turn ids: `019e869b-2116-70f3-9693-ea26e8125199` then
  `019e869b-9b20-72b1-8821-979a8c8be125`.
- Live smoke total usage reported by the SDK adapter: 74,786 tokens for turn 1
  and 102,013 tokens for turn 2, with cached input included in SDK totals.

## Risks

- The SDK package is beta (`openai-codex` 0.1.0b2 during implementation), so
  method signatures may drift.
- The SDK adapter intentionally does not hide SDK/auth failures by silently
  invoking the CLI fallback. Hosts should register and choose the CLI adapter
  explicitly when fallback behavior is desired.
