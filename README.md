# Agent Workflow Kernel

Portable workflow kernel for agent-driven work.

This repository is intentionally independent from OpenClaw. OpenClaw is the
first reference host and proving ground, but the kernel should stay portable
across agent runtimes, surfaces, and domain workflows.

## Product Thesis

The kernel coordinates auditable workflows that mix:

- LLM agents;
- deterministic scripts;
- human approval gates;
- reviewer/doer or agent-to-agent loops;
- versioned prompts and context packets;
- durable receipts;
- operator surfaces such as Obsidian, Telegram, local Markdown, or Sheets.

It is not a general agent brain, a no-code workflow builder, or a place to hide
domain logic inside YAML.

## Current Status

Wave 3 local harness is integrated. The living project control document is
[`docs/control.md`](docs/control.md).

Implemented so far:

- workflow contract dataclasses and enums;
- YAML workflow loader, validation, and canonical JSON compiler;
- SQLite ledger and adapter-neutral runner skeleton;
- prompt registry, context packets, and receipt provenance helpers;
- policy engine with hard human gates;
- generic optioned human gates with choice manifests, selected-option receipts,
  action fingerprints, and prompt-backed local proof support;
- runtime, surface, host, and lane adapter interfaces plus local fakes;
- five example workflow fixtures;
- operator CLI commands for validating, compiling, and locally running workflow
  fixtures;
- fixture-based parity reporting;
- read-only OpenClaw adapter boundary;
- repeatable `make setup` and `make check` paths.

Useful CLI entrypoints:

```bash
.venv/bin/python -m agent_workflow_kernel.cli validate workflows/bumblebee_quality_review.yaml
.venv/bin/python -m agent_workflow_kernel.cli compile workflows/bumblebee_quality_review.yaml
.venv/bin/python -m agent_workflow_kernel.cli run-local workflows/deterministic_system_action.yaml
```

## Development Setup

The repeatable setup path creates a local virtual environment and installs the
runtime plus development dependencies declared in `pyproject.toml`:

```bash
make setup
```

The repeatable check path always runs the bare-stdlib test suite first, then
runs venv-backed `pytest` when `.venv` exists:

```bash
make check
```

The project keeps both paths on purpose. Bare `python3 -m unittest discover -s
tests` protects worker threads and fresh machines where system `python3` may not
have `PyYAML` installed yet. The `.venv` path verifies the declared package
dependencies, including real `PyYAML` behavior and `pytest`, after `make setup`
has installed `.[dev]`. Generated development outputs such as `.venv`,
`__pycache__`, `.pytest_cache`, and egg-info directories are ignored.

## Starting Principles

- Build for the full portable harness vision.
- Validate first through narrow, low-risk OpenClaw slices.
- Keep generic kernel code free of OpenClaw paths and lane names.
- Preserve working OpenClaw behavior until parity is proven.
- Treat publish, deploy, trade, auth, money, external sends, and destructive
  actions as explicit human-gated policy zones.
