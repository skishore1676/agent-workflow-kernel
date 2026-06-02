# Codex CLI Runtime Adapter

## Design

AWK can use the native Codex CLI as a worker runtime without routing through
OpenClaw. This keeps AWK portable while still using Suman's normal Codex auth
path.

Adapter ids:

- `runtime.codex_cli_exec`: one-shot `codex exec`.
- `runtime.codex_cli_session`: bounded reusable `codex exec` session.

The session adapter is not optional for serious lane adoption. It captures a
concrete session id from Codex JSONL events, stores that id in adapter outputs,
and resumes with `codex exec resume <session_id>`. Normal AWK operation should
not rely on `--last`, because multiple lanes can run concurrently.

## Workflow Config Shape

```yaml
stages:
  - id: draft
    type: agent_work
    adapter: runtime.codex_cli_session
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
codex_cli:
  cwd: "/Users/suman/code/agent-workflow-kernel"
  sandbox: read-only
  ask_for_approval: never
  ignore_rules: true
  timeout_seconds: 900
  max_session_turns: 20
  artifact_dir: ".awk-live/codex-cli/my-run"
```

`ask_for_approval` is mapped to the current Codex CLI config override
`approval_policy="never"` rather than a legacy CLI flag.

Runner code can register the package adapters with:

```python
from agent_workflow_kernel import AdapterRegistry
from agent_workflow_kernel_codex_cli import codex_cli_runtime_registrations

registry = AdapterRegistry(codex_cli_runtime_registrations())
```

## Receipts And Proof

Each invocation captures:

- last assistant message;
- Codex JSONL events;
- stderr;
- command shape with sensitive path arguments redacted;
- token usage when present in events;
- session key, session id, reuse flag, and turn count for session mode.

If `runtime.codex_cli_session` cannot capture a reusable session id, the adapter
fails the stage instead of quietly pretending continuity exists.

## Smoke

Run a safe fake-CLI proof through unit tests:

```bash
python3 -m unittest tests/test_codex_cli_runtime_adapter.py
```

Run a tiny real Codex CLI proof only when token spend is intentional:

```bash
python3 scripts/codex_cli_runtime_smoke.py --run-real --mode session
```

This smoke does not mutate production OpenClaw, Northstar, Telegram, auth,
trading, or public publish surfaces.

Current proof: the live smoke on 2026-06-02 captured session id
`019e8689-c031-7f62-af8b-cf3ad86005a4`, resumed it, and the second turn
recalled the nonce from the first turn.
