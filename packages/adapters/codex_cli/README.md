# AWK Codex CLI Runtime Adapter

This adapter package lets AWK run agent-work stages through the native Codex
CLI without depending on OpenClaw as the worker runtime.

Supported adapter ids:

- `runtime.codex_cli_exec`: one-shot `codex exec` invocation.
- `runtime.codex_cli_session`: bounded reusable `codex exec` session using a
  concrete captured session id and `codex exec resume`.

The session adapter is intentionally first-class. AWK should prefer explicit
session ids stored in receipts/ledger outputs over `--last`, because `--last`
can resume the wrong conversation when multiple workers are active.
