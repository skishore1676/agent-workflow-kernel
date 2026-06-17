# 00 — Baseline

## Environment
- **Repo:** `agent-workflow-kernel` (the prompt's "agent-kernel-harness" is a
  template placeholder; the real target is this repo).
- **Worktree:** `/Users/suman/code/agent-workflow-kernel-rearch`
- **Branch:** `rearch/agent-workflow-kernel-end-to-end` (off `main` @ `6c65514`)
- **Python:** 3.13.7 in `.venv` (editable install of all packages)

## Scope decision (confirmed with Suman)
Kernel-readiness re-architecture. This repo is a "recovery archive" under the
approved `docs/rearchitecture-plan.md`; only the **kernel** is vendored into the
new `lane-host` repo. So the work targets `packages/kernel/agent_workflow_kernel`
exclusively, making it clean, pure, bounded, and documented. Adapters/scripts/
runtime are out of scope (slated for rewrite in lane-host).

## Baseline commands & results
| Command | Result |
|---------|--------|
| `./scripts/check.sh` (unittest discover + pytest) | **263 passed** in ~5.5s |
| purity grep: kernel imports any provider/adapter pkg? | **CLEAN** (none) |
| `git status` | clean (worktree fresh) |

No pre-existing test failures. The 263-passing suite is the hard invariant for
every phase boundary.

## Baseline size smells (the targets)
| File | Lines | Shape |
|------|------:|-------|
| `kernel.py` | 3,779 | dataclasses + `WorkflowKernel` (~2,000L) + ~1,500L trailing stateless helpers |
| `local_adapters.py` | 4,207 | ~10 independent local/dev/sandbox/live adapter classes + render helpers |
| `storage.py` | 1,840 | `WorkflowLedger` (~1,630L) + export/DDL helpers |
| `ivy_lane.py` (adapter, out of scope) | 749 | — |

## Assumptions
- A1: scope = kernel package only (confirmed).
- A2: purity = no imports from `agent_workflow_kernel_{openclaw,codex_cli,codex_sdk,a2a,x_digest,ivy,artifact_validation}`.
- A3: public `agent_workflow_kernel.*` surface (135 `__all__` names) is frozen.
- A4: local worktree only; no live mutation; no protected gate in scope.
