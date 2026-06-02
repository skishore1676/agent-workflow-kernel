# Codex SDK Adapter

Preferred AWK runtime adapter backed by the official `openai-codex` Python SDK.

Adapter id:

- `runtime.codex_sdk_session`: bounded reusable Codex SDK thread using
  `Codex.thread_start(...)`, `Codex.thread_resume(...)`, and `Thread.run(...)`.

The SDK dependency is optional for the base AWK package:

```bash
python3 -m pip install '.[codex-sdk]'
```

When the SDK is not importable, the adapter fails the invocation with an
artifacted error instead of pretending a session ran. The older
`runtime.codex_cli_session` adapter remains available as the subprocess CLI
fallback.
