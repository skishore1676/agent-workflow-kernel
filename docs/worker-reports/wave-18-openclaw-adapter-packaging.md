# Wave 18: OpenClaw Adapter Packaging

Scope: local AWK repo/worktree only. No oldmac, OpenClaw runtime, Obsidian,
Telegram, credentials, auth, deployment, or trading state was mutated.

## Boundary Decisions

- Root editable installs should discover both `agent_workflow_kernel` and
  `agent_workflow_kernel_openclaw`.
- OpenClaw-specific adapter imports belong under
  `agent_workflow_kernel_openclaw`; the portable kernel should not re-export
  them.
- The live Obsidian Markdown adapter remains in the kernel for now because it is
  still a generic operator-surface implementation. The OpenClaw Telegram adapter
  moved because it shells through the OpenClaw CLI and carries OpenClaw delivery
  semantics.

## Changed

- Updated `pyproject.toml` package discovery to include both
  `packages/kernel` and `packages/adapters/openclaw`.
- Moved `OpenClawTelegramSurfaceAdapter` and
  `OPENCLAW_TELEGRAM_MESSAGE_SCHEMA` into
  `agent_workflow_kernel_openclaw.telegram`, exported through
  `agent_workflow_kernel_openclaw`.
- Removed the OpenClaw Telegram adapter class from
  `agent_workflow_kernel.local_adapters`.
- Updated OpenClaw cutover/completion scripts to try normal installed package
  imports first, falling back to source-checkout paths only when needed.
- Updated tests and docs to use the adapter package import path.
- Added packaging discovery coverage for the root package finder settings and
  the OpenClaw Telegram adapter import boundary.

## Deferred

- I did not relocate broader live-operator-surface safety helpers or
  `LiveObsidianMarkdownSurfaceAdapter`; that would be a larger boundary pass and
  risks disturbing existing generic local/surface tests.
- I did not add separate distribution metadata for a standalone
  `agent-workflow-kernel-openclaw` wheel. The low-risk change for this wave is a
  single root editable install that exposes both import packages.
- I did not remove test source-path inserts globally. Existing tests still run
  directly from a checkout; the new packaging test covers the root discovery
  settings, while scripts now prefer normal imports.

## Verification

- `python3 -m py_compile packages/adapters/openclaw/agent_workflow_kernel_openclaw/telegram.py packages/kernel/agent_workflow_kernel/local_adapters.py scripts/openclaw_live_cutover.py scripts/openclaw_owned_completion_bridge.py` passed.
- `python3 -m unittest tests.test_packaging_discovery tests.test_live_operator_surface_adapters tests.test_openclaw_live_cutover` passed: 14 tests.
- `python3 -m unittest discover -s tests` passed: 188 tests.
- `./scripts/check.sh` passed: 188 unittest tests; venv pytest was skipped because `.venv/bin/python` is missing, with the script message `Run ./scripts/dev_setup.sh first.`
