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

Bootstrap phase. The living project control document is
[`docs/control.md`](docs/control.md).

## Starting Principles

- Build for the full portable harness vision.
- Validate first through narrow, low-risk OpenClaw slices.
- Keep generic kernel code free of OpenClaw paths and lane names.
- Preserve working OpenClaw behavior until parity is proven.
- Treat publish, deploy, trade, auth, money, external sends, and destructive
  actions as explicit human-gated policy zones.

