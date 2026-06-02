# Codex SDK Session Runtime Adapter

## Design

AWK should prefer the official Codex Python SDK for native Codex worker
sessions. The SDK adapter avoids subprocess-wrapping `codex exec` and uses the
SDK thread lifecycle directly:

- `Codex.thread_start(...)` for a new bounded worker thread.
- `Codex.thread_resume(thread_id, ...)` for continuity.
- `Thread.run(prompt, ...)` for each AWK stage turn.

Adapter id:

- `runtime.codex_sdk_session`: preferred bounded Codex SDK session.

Fallback adapter:

- `runtime.codex_cli_session`: retained subprocess CLI fallback.

## Workflow Config Shape

```yaml
stages:
  - id: draft
    type: agent_work
    adapter: runtime.codex_sdk_session
    actors:
      primary: codex_worker
    inputs:
      objective: "Build the requested packet."
    policy:
      risk_classes: ["read_only", "local_draft"]
    lease:
      lease_seconds: 900
```

Runtime options can be passed in the stage packet:

```yaml
codex_sdk:
  cwd: "/Users/suman/code/agent-workflow-kernel"
  sandbox: read-only
  approval_mode: deny_all
  timeout_seconds: 900
  max_session_turns: 20
  artifact_dir: ".awk-live/codex-sdk/my-run"
```

Runner code can register the package adapter with:

```python
from agent_workflow_kernel import AdapterRegistry
from agent_workflow_kernel_codex_sdk import codex_sdk_runtime_registrations

registry = AdapterRegistry(codex_sdk_runtime_registrations())
```

## Receipts And Proof

Each invocation captures:

- final assistant message;
- SDK turn result JSON;
- SDK/session metadata JSON;
- token usage when the SDK reports it;
- session key, thread id, turn id, reuse flag, and turn count.

If the SDK dependency, auth, or runtime fails, the adapter returns a failed AWK
receipt with artifacts under `.awk-live/` rather than silently falling back. The
CLI adapter remains separately registrable for hosts that intentionally choose
that fallback.

## Smoke

Run deterministic fake-SDK tests:

```bash
python3 -m unittest tests/test_codex_sdk_runtime_adapter.py tests/test_packaging_discovery.py
```

Install the optional SDK only in a local venv when needed:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[codex-sdk]'
```

Run a real two-turn SDK thread smoke only when token spend is intentional:

```bash
.venv/bin/python scripts/codex_sdk_runtime_smoke.py --run-real --timeout-seconds 300
```

The smoke creates a local fixture and writes all proof under
`.awk-live/codex-sdk-smoke/`. It does not mutate OpenClaw production behavior,
Northstar/Obsidian, Telegram, auth, trading, public publish, launchd, cron, or
production prompts.
