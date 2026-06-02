# Wave 15 Prompt Layer Adversarial Audit

Date: 2026-06-01

Scope: disprove the claim that AWK can safely migrate the Ivy/Jonah editorial lane and the Jarvis weekly update lane before completing the prompt registry layer. This audit stayed read-only against OpenClaw files and did not touch oldmac, Obsidian, Telegram, credentials, or live runtime state.

Conclusion: I would block full cutover. The current AWK lane proofs are useful fixture and shadow-readiness evidence, but they do not yet prove prompt-safe migration. The target workflows can run with no registered prompt refs, the local registry has no OpenClaw/Ivy/Jarvis lane prompts, and the OpenClaw adoption receipts can say shadow-ready while carrying no real prompt provenance.

## C1. Target workflows have zero prompt refs, so the prompt layer is bypassed

Severity: Critical

Evidence:
- The prompt-registry contract says each executable `StageRun` resolves `StageDef.prompt_refs`, renders a `ContextPacket`, invokes the runtime, and records prompt provenance in the receipt (`docs/synthesis/prompt-registry.md:18-29`).
- Receipt requirements include prompt ids, versions, content hashes, context packet digest, rendered input digest, runtime/model identity, tool permissions, policy envelope id/version/hash, and redaction mode when relevant (`docs/synthesis/prompt-registry.md:355-366`).
- `StageDef.prompt_refs` defaults to an empty tuple (`packages/kernel/agent_workflow_kernel/contracts.py:160-174`), and the DSL only populates it when a stage explicitly declares `prompt_refs` (`packages/kernel/agent_workflow_kernel/dsl.py:85-101`).
- The kernel renders prompt context only under `if stage.prompt_refs`; otherwise adapter invocations get `context_packet_ref=None` (`packages/kernel/agent_workflow_kernel/kernel.py:946-1001`).
- Runtime input only includes `context_packet`, `rendered_input`, and `rendered_input_digest` when a rendered context exists (`packages/kernel/agent_workflow_kernel/kernel.py:2580-2595`).
- Neither target workflow declares prompt refs across any stage (`workflows/ivy_jonah_editorial.yaml:37-160`, `workflows/jarvis_weekly_update_shadow.yaml:44-140`).

How to reproduce:

```bash
python3 -c 'import sys; from pathlib import Path; root=Path.cwd(); sys.path.insert(0, str(root/"packages"/"kernel")); from agent_workflow_kernel.dsl import load_workflow_file; paths=[root/"workflows"/"ivy_jonah_editorial.yaml", root/"workflows"/"jarvis_weekly_update_shadow.yaml"]; print({p.name: {s.id: len(s.prompt_refs) for s in load_workflow_file(p).stages} for p in paths})'
```

Observed output:

```text
{'ivy_jonah_editorial.yaml': {'accept_source_approval': 0, 'build_draft_package': 0, 'editor_review': 0, 'revise_draft': 0, 'validate_editorial_state': 0, 'p5_final_approval': 0}, 'jarvis_weekly_update_shadow.yaml': {'discover_weekly_artifact': 0, 'readback_blackboard_card': 0, 'suman_review_gate': 0, 'route_follow_up': 0}}
```

Why this blocks cutover:

The two migration workflows can satisfy graph, policy, fixture, and read-only checks while proving nothing about the actual prompt bundle Ivy, Jonah, Jarvis, or a system-action adapter would receive. That means a post-cutover regression could be caused by missing identity rules, missing lane rules, changed host instructions, or adapter-side prompt text, and AWK receipts would not explain it.

Required fixes/gates before full cutover:
- Add prompt refs or an explicit audited `prompt_context_exempt` reason to every executable stage in both workflows. Agent, A2A, human-gate, and system-action stages should not default silently to no prompt bundle.
- Add workflow validation that fails these two cutover workflows if an executable stage lacks prompt refs and lacks an approved exemption.
- Add a test that loads both workflows and asserts all non-exempt stages resolve exact prompt refs against a configured registry.
- Add an end-to-end kernel test proving at least one Ivy/Jonah runtime stage and one Jarvis weekly system stage record non-empty `context_packet_ref`, `prompt_provenance.refs`, `context.packet_digest`, and `context.rendered_input_digest`.

## C2. OpenClaw/Ivy/Jarvis source prompts are not imported, pinned, or mapped

Severity: Critical

Evidence:
- The local registry contains only four generic prompts: `identity.portable_worker`, `policy.no_external_effects`, `lane.quality_review`, and `stage.review` (`prompts/registry.yaml:3-23`).
- Those prompt bodies are generic and do not encode Ivy, Jonah, OR Research, weekly check-in, Blackboard decision-loop, or Jarvis routing behavior (`prompts/identities/portable-worker/v1.0.0.md:1-4`, `prompts/lanes/quality-review/v1.0.0.md:1-4`, `prompts/stages/review/v1.0.0.md:1-4`).
- The design says OpenClaw `AGENTS.md`, Codex skills, tool/plugin allowlists, OpenClaw lane instructions, task packets, and Blackboard/Northstar/Telegram receipts must map through the host adapter as prompt refs, policy envelopes, context packets, and receipts (`docs/synthesis/prompt-registry.md:372-415`).
- Ivy's live source prompt is lane-specific: it defines Ivy's identity, scope, source-quality rules, P1-P5 workflow, human-gate semantics, no-publication boundary, weekly synthesis behavior, memory behavior, and lint/ingest checks (`/Users/suman/code/openclaw-core/workspace/agents/or_research/SOUL.md:1-12`, `/Users/suman/code/openclaw-core/workspace/agents/or_research/SOUL.md:31-45`, `/Users/suman/code/openclaw-core/workspace/agents/or_research/SOUL.md:65-68`, `/Users/suman/code/openclaw-core/workspace/agents/or_research/SOUL.md:99-133`).
- The Blackboard decision loop requires receipt-backed events, explicit route data, allowed scopes, final-approval policy, fail-closed behavior, fixture/no-write tests, and oldmac live validation (`/Users/suman/code/openclaw-core/workspace-main/docs/blackboard_decision_loop.md:13-41`, `/Users/suman/code/openclaw-core/workspace-main/docs/blackboard_decision_loop.md:101-149`, `/Users/suman/code/openclaw-core/workspace-main/docs/blackboard_decision_loop.md:308-340`).

How to reproduce:

```bash
nl -ba prompts/registry.yaml
rg -n "or_research|Ivy|Jonah|Jarvis|weekly|blackboard|host.openclaw|adapter_source" prompts workflows
```

Expected blocker:

The registry has no `host.openclaw` or lane-specific imported prompt records for the required OpenClaw sources, and the workflows do not reference any such records.

Why this blocks cutover:

The migration is supposed to preserve behavior that currently lives in OpenClaw prompt files and operator docs. Without an import manifest and pinned prompt refs, AWK cannot prove whether a run used the current Ivy/Jonah/Jarvis behavior, an obsolete prompt, a generic review prompt, or adapter-private text outside the kernel receipt model.

Required fixes/gates before full cutover:
- Implement an OpenClaw prompt import manifest for the Ivy/Jonah and Jarvis weekly sources. It should record source kind, source URI, source hash, mapped prompt id/kind/version/content hash, and import time.
- Add registry records for Ivy identity/lane, Jonah reviewer role, Jarvis weekly lane behavior, Blackboard decision-loop route policy, and any stage-specific contracts used by the two workflows.
- Add tests that fail when the OpenClaw source file hash changes but the imported registry snapshot is not refreshed.
- Add tests that prove workflows use generic prompt refs while the OpenClaw adapter can satisfy those refs from host-imported sources without raw OpenClaw paths in the workflow YAML.

## H1. Shadow adoption can report ready while receipts have empty prompt provenance

Severity: High

Evidence:
- Ivy/Jonah adoption receipts are synthesized from fixture observations via `make_adapter_receipt`, with a fixture-shaped `context_packet_ref` but no rendered context or prompt provenance (`packages/adapters/openclaw/agent_workflow_kernel_openclaw/ivy_lane.py:257-310`).
- Weekly update adoption receipts follow the same pattern and set `context_packet_ref=f"context:{weekly.fixture_id}:{stage_id}"` without a rendered prompt bundle (`packages/adapters/openclaw/agent_workflow_kernel_openclaw/weekly_update.py:431-520`).
- `make_adapter_receipt` copies `invocation.context_packet_ref` into the receipt but does not add `prompt_provenance` (`packages/kernel/agent_workflow_kernel/adapters.py:120-163`).
- Ivy/Jonah tests assert receipt count, read-only policy, public-publish blocking, and deterministic JSON, but not prompt provenance (`tests/test_openclaw_ivy_lane_adoption.py:57-74`, `tests/test_openclaw_ivy_lane_adoption.py:98-108`).
- Weekly tests assert deterministic, shadow-only receipts, but not prompt provenance (`tests/test_openclaw_weekly_update_adoption.py:101-113`).

How to reproduce:

```bash
python3 -c 'import sys; from pathlib import Path; root=Path.cwd(); sys.path.insert(0, str(root/"packages"/"kernel")); sys.path.insert(0, str(root/"packages"/"adapters"/"openclaw")); from agent_workflow_kernel_openclaw import load_ivy_jonah_fixture, adopt_ivy_jonah_fixture, load_weekly_update_fixture, adoption_report_from_fixture; ivy=adopt_ivy_jonah_fixture(load_ivy_jonah_fixture(root/"fixtures"/"openclaw"/"ivy_jonah"/"p3_approval_to_p5_shadow.json")); weekly=adoption_report_from_fixture(load_weekly_update_fixture(root/"fixtures"/"openclaw"/"weekly_update"/"weekly_check_in_ready.json")); print({"ivy_prompt_provenance": [bool(r.prompt_provenance) for r in ivy.receipts], "weekly_prompt_provenance": [bool(r.prompt_provenance) for r in weekly.receipts]})'
```

Observed output:

```text
{'ivy_prompt_provenance': [False, False, False, False, False], 'weekly_prompt_provenance': [False, False, False]}
```

Why this blocks cutover:

The readiness layer currently proves "fixture mapped to expected shadow receipts", not "the target AWK runtime saw a pinned prompt/context bundle." A cutover gate that accepts these receipts would allow migration even though the core prompt-safety evidence is absent.

Required fixes/gates before full cutover:
- Require non-empty prompt provenance for all target lane adoption receipts that claim `ready_for_shadow`, `waiting_on_human`, or live-cutover readiness.
- Replace placeholder `context_packet_ref` strings with real rendered context packet ids, hashes, and canonical rendered input digests.
- Extend the Ivy/Jonah and weekly adoption tests to assert prompt refs, prompt hashes, context packet digests, rendered input digests, runtime identity, and permission digest fields.
- Keep fixture-only readiness status distinct from prompt-safe cutover readiness until those fields exist.

## H2. System-action stages can remain untracked escape hatches around prompt/context auditing

Severity: High

Evidence:
- The prompt-registry design explicitly says scripts also receive context packets and that `system_action` stages need context and policy provenance so scripts do not become an untracked escape hatch around approval gates or prompt/context auditing (`docs/synthesis/prompt-registry.md:425-434`).
- The Ivy/Jonah workflow has a `validate_editorial_state` `system_action` stage with no prompt refs (`workflows/ivy_jonah_editorial.yaml:123-136`).
- The Jarvis weekly workflow has three `system_action` stages with no prompt refs: discovery, Blackboard readback, and follow-up routing (`workflows/jarvis_weekly_update_shadow.yaml:45-90`, `workflows/jarvis_weekly_update_shadow.yaml:118-140`).
- The kernel only renders and records context packets when `stage.prompt_refs` is non-empty (`packages/kernel/agent_workflow_kernel/kernel.py:946-985`).

How to reproduce:

Use the C1 prompt-ref count command and inspect the `system_action` stages listed above. They all report zero prompt refs.

Why this blocks cutover:

These stages are exactly where stale artifact validation, Blackboard semantics, and Jarvis follow-up routing are supposed to be constrained. If they execute without registered stage contracts and context provenance, a live cutover can drift into adapter-private behavior while the kernel receipts still look structurally valid.

Required fixes/gates before full cutover:
- Add policy/stage-contract prompt refs for every system-action stage, even if the prose prompt is minimal and the stage is deterministic.
- Add a test proving system-action receipts include context packet refs and policy provenance.
- Add a cutover rule that any system-action stage without context provenance is fixture-only and cannot be counted as live migration readiness.

## H3. Prompt policy envelopes are receipt metadata, not an enforceable policy source

Severity: High

Evidence:
- The design says a policy envelope is not prose-only; it contains risk and permission data the runner can enforce before invoking a runtime adapter (`docs/synthesis/prompt-registry.md:166-203`).
- Current policy enforcement compiles workflow defaults, workflow policies, stage policy, and adapter metadata (`packages/kernel/agent_workflow_kernel/kernel.py:1835-1888`).
- The prompt policy file is only resolved as part of a prompt bundle. Receipt helpers can copy the first policy prompt id/version/hash into `policy_snapshot`, but they do not parse the policy envelope into the enforcement layer (`packages/kernel/agent_workflow_kernel/receipts.py:54-80`, `packages/kernel/agent_workflow_kernel/receipts.py:141-145`).
- The local prompt policy declares approval-required hard boundaries for public publish, external send, auth changes, live trades, money movement, and destructive changes (`prompts/policies/no-external-effects/v1.0.0.yaml:1-22`), but those prompt-file fields are not the source used by `_effective_policy_for_stage`.

How to reproduce:

Inspect the code paths above. `PromptRegistry.resolve` reads prompt content and hashes it (`packages/kernel/agent_workflow_kernel/prompts.py:200-248`), while `_effective_policy_for_stage` never receives resolved prompt content.

Why this blocks cutover:

Even if prompt refs are added, the prompt registry layer would still not fully satisfy the design's policy-envelope claim. The kernel can record "policy.no_external_effects" in a receipt while enforcement comes from a separate stage/workflow/adapter policy path. That split is easy to misconfigure during migration: the receipt can name one policy while the runnable gate used another.

Required fixes/gates before full cutover:
- Define whether policy prompts are authoritative enforcement inputs or receipt-only provenance. If authoritative, parse them into the effective policy compiler and fail closed on mismatch with workflow/stage policy.
- Add a test where a prompt policy envelope and stage policy conflict; the stricter policy must win and the receipt must show both sources.
- Add a test that the rendered prompt policy id/hash matches the effective policy snapshot used by `policy_preflight`.

## M1. Prompt lifecycle status exists but is not enforced

Severity: Medium

Evidence:
- Registry records include `status`, and resolved prompts preserve that status (`packages/kernel/agent_workflow_kernel/prompts.py:36-60`).
- `PromptRegistry.resolve` resolves records and returns a bundle without rejecting inactive, deprecated, or blocked statuses (`packages/kernel/agent_workflow_kernel/prompts.py:200-216`, `packages/kernel/agent_workflow_kernel/prompts.py:224-248`).
- The checked-in registry marks every current prompt `active`, but there is no failing path that would prevent a cutover workflow from using a stale registered prompt if one is later marked non-active (`prompts/registry.yaml:3-23`).

How to reproduce:

Create a temporary registry record with `status: deprecated` or `status: blocked`; `PromptRegistry.resolve` will still return it as long as id/kind/version/path match and hashes pass.

Why this matters:

Prompt cutover needs an operator-safe way to retire imported host prompts. Otherwise a stale Ivy/Jonah or Jarvis prompt can remain exactly versioned and hash-valid while still being semantically disallowed for live cutover.

Required fixes/gates before full cutover:
- Define allowed lifecycle statuses for execution, shadow, and historical replay.
- Fail execution for non-active required prompts unless the runner is explicitly in historical replay mode.
- Add tests for `deprecated`, `blocked`, and `active` prompt records.

## Cutover Gates I Would Require

1. Both target workflows load with exact prompt refs or explicit audited exemptions for every stage.
2. An OpenClaw prompt import manifest exists for Ivy, Jonah, Jarvis weekly, Blackboard decision-loop routing, and relevant policy/allowlist sources.
3. A target-lane shadow run produces receipts with prompt refs, prompt content hashes, context packet digest, rendered input digest, runtime identity, and permission digest.
4. System-action stages cannot count toward live readiness without context packet and policy provenance.
5. Prompt policy envelope behavior is clarified and tested against the effective policy compiler.
6. Prompt lifecycle status is enforced.
7. At least one oldmac live read-only validation run compares OpenClaw source hashes and AWK prompt import hashes without mutating oldmac, Obsidian, Telegram, credentials, or runtime state.

## Tests That Should Exist Before Full Cutover

- `test_target_openclaw_workflows_require_prompt_refs_or_exemptions`
- `test_openclaw_prompt_import_manifest_pins_source_hashes`
- `test_ivy_jonah_shadow_receipts_include_prompt_context_provenance`
- `test_weekly_update_shadow_receipts_include_prompt_context_provenance`
- `test_system_action_stages_record_context_packet_provenance`
- `test_policy_prompt_envelope_matches_effective_policy_snapshot`
- `test_non_active_prompt_status_blocks_execution`
- `test_openclaw_source_hash_drift_blocks_cutover_readiness`

## Verification

Completed before commit:
- `python3 -m unittest discover -s tests` passed: 172 tests in 2.855s.
- `./scripts/check.sh` passed its unittest path: 172 tests in 3.344s. It also reported `Skipping venv pytest: .venv/bin/python is missing. Run ./scripts/dev_setup.sh first.`
- `git status --short` after commit is recorded in the worker closeout.
