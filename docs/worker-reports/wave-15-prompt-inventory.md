# Wave 15 Prompt Inventory Report

## Scope

This report inventories the missing prompt-registry layer for the OpenClaw
migration lanes:

- `ivy_jonah_editorial`
- `jarvis_weekly_update_shadow`

No OpenClaw files, oldmac state, Obsidian vaults, Telegram routes, credentials,
or runtime state were mutated. This is a local source inventory and migration
map only.

## Current Prompt Coverage

The local registry at `prompts/registry.yaml` currently contains only the
generic quality-review prompt bundle:

| Prompt id | Kind | Version | File | Coverage |
| --- | --- | --- | --- | --- |
| `identity.portable_worker` | `identity` | `1.0.0` | `prompts/identities/portable-worker/v1.0.0.md` | Generic worker identity. Useful as a default renderer identity, but not enough to preserve Ivy, Jonah, Jarvis, or Blackboard behavior. |
| `policy.no_external_effects` | `policy` | `1.0.0` | `prompts/policies/no-external-effects/v1.0.0.yaml` | Generic local-draft no-external-effects policy. It does not yet encode the weekly shadow forbidden actions or the editorial public-publish boundary. |
| `lane.quality_review` | `lane` | `1.0.0` | `prompts/lanes/quality-review/v1.0.0.md` | Only covers the Bumblebee-style quality-review lane. |
| `stage.review` | `stage` | `1.0.0` | `prompts/stages/review/v1.0.0.md` | Only covers the generic review stage used by prompt/context tests. |

The target workflow YAML files declare no `prompt_refs` at all:

| Workflow | Stage count | Stages with `prompt_refs` | Result |
| --- | ---: | ---: | --- |
| `ivy_jonah_editorial` | 6 | 0 | Prompt provenance cannot identify Ivy/Jonah identity, P-gate rules, Jonah review contract, stale-review validation, or P5 public boundary. |
| `jarvis_weekly_update_shadow` | 4 | 0 | Prompt provenance cannot identify weekly readback rules, read/clear semantics, Suman gate wording, or shadow-only routing boundaries. |

Existing tests prove the generic prompt registry loader, exact hash resolution,
missing-prompt blocking, context-packet rendering, and receipt provenance for a
toy runtime stage. They do not yet require prompt refs on the two OpenClaw lane
fixtures, and the OpenClaw adapter receipts currently carry `context_packet_ref`
strings without prompt provenance.

## Source Materials To Import Or Reference

Use OpenClaw source as hashed imported prompt material, not raw workflow YAML
content and not unversioned runtime truth.

| Source material | Recommended registry use | Notes |
| --- | --- | --- |
| `/Users/suman/code/openclaw-core/workspace/agents/or_research/SOUL.md` | Import as `adapter_source.openclaw.or_research.soul`; distill into `identity.openclaw.ivy_or_research` and `lane.openclaw.ivy_jonah_editorial`. | Defines Ivy's research/editor/publisher-prep role, OR audience, source-quality rules, P1-P5 shape, and the no-external-publish boundary. |
| `/Users/suman/code/openclaw-core/workspace/agents/or_research/skills/research-project-lifecycle/SKILL.md` | Import as `adapter_source.openclaw.or_research.skill.research_project_lifecycle`; distill into Ivy/Jonah stage prompts. | Defines lifecycle truth, P3/P5 human gates, P4 package expectations, Jonah pre-P5 gate, publish bundle behavior, review-surface handoff, and validation. |
| `/Users/suman/code/openclaw-core/workspace/agents/jonah_editor/SOUL.md` | Import as `adapter_source.openclaw.jonah_editor.soul`; distill into `identity.openclaw.jonah_editor`. | Defines Jonah as the editorial gate, with emphasis on specificity, public source discipline, structure, visual usefulness, and Suman's voice. |
| `/Users/suman/code/openclaw-core/workspace/agents/jonah_editor/skills/editorial-review/SKILL.md` | Import as `adapter_source.openclaw.jonah_editor.skill.editorial_review`; distill into `stage.openclaw.ivy_jonah.editor_review`. | Defines artifact inputs, bounded questions, review passes, deterministic `p4_editor_review.md` write, allowed statuses, and output contract. |
| `/Users/suman/code/openclaw-core/workspace/agents/or_research/skills/weekly-report-style/SKILL.md` | Import as `adapter_source.openclaw.or_research.skill.weekly_report_style`; reference from weekly lane/stage prompts where summary voice or follow-up text is rendered. | Defines operator-note style: decision-useful, concrete, no hype, exact review artifact when Suman attention is needed. |
| `/Users/suman/code/openclaw-core/workspace-main/docs/blackboard_decision_loop.md` | Import as `adapter_source.openclaw.blackboard_decision_loop`; distill into `lane.openclaw.jarvis_weekly_update_shadow` and human-gate/routing stage prompts. | Defines read/clear vs approval, receipt-before-work, route model, comments-as-evidence, idempotence, fail-closed behavior, and final approval boundaries. |
| `/Users/suman/code/openclaw-core/workspace/agents/or_research/docs/or_research_v2.md` | Optional source reference for `stage.openclaw.ivy_jonah.editor_review` and `stage.openclaw.ivy_jonah.validate_editorial_state`. | The P4.5 Jonah section names allowed statuses and says P4 cannot advance to P5 unless Jonah clears it or Suman explicitly overrides. |

Recommended import manifest:

- `prompts/adapters/openclaw/imported-agents/v2026-06-01.yaml`
- `schema_version: adapter-prompt-import.v1`
- `adapter_id: host.openclaw`
- source URIs should be stable logical URIs such as
  `openclaw://workspace/agents/or_research/SOUL.md`, with source hashes.
- mapped prompts should use exact versions such as
  `2026.06.01+<short-source-hash>` for adapter-source records.

## Recommended Prompt Registry Additions

Add packaged prompts with semantic versions. The adapter-source imports can
record source hashes; the packaged lane/stage prompts should be the stable
contracts workflows reference.

### Shared Policy Prompts

| Prompt id | Version | File | Purpose |
| --- | --- | --- | --- |
| `policy.openclaw.read_only_shadow` | `1.0.0` | `prompts/policies/openclaw-read-only-shadow/v1.0.0.yaml` | Deny Obsidian writes, Telegram sends, OpenClaw runtime mutation, cron changes, credential access, auth changes, external sends, public publishing, trading, money movement, and destructive changes. |
| `policy.openclaw.editorial_public_boundary` | `1.0.0` | `prompts/policies/openclaw-editorial-public-boundary/v1.0.0.yaml` | Allow internal generation and local package preparation while requiring explicit Suman approval for public publish, browser staging beyond local plan, external send, push, deploy, or auth changes. |
| `policy.openclaw.review_only_human_gate` | `1.0.0` | `prompts/policies/openclaw-review-only-human-gate/v1.0.0.yaml` | Human review surfaces may collect explicit decisions and comments, but may not mutate runtime state by themselves. |

### Ivy/Jonah Editorial Lane

| Prompt id | Kind | Version | File | Source basis |
| --- | --- | --- | --- | --- |
| `identity.openclaw.ivy_or_research` | `identity` | `1.0.0` | `prompts/identities/openclaw-ivy-or-research/v1.0.0.md` | Ivy `SOUL.md` plus OR Research lifecycle boundaries. |
| `identity.openclaw.jonah_editor` | `identity` | `1.0.0` | `prompts/identities/openclaw-jonah-editor/v1.0.0.md` | Jonah `SOUL.md`. |
| `lane.openclaw.ivy_jonah_editorial` | `lane` | `1.0.0` | `prompts/lanes/openclaw-ivy-jonah-editorial/v1.0.0.md` | Ivy source quality, P1-P5 semantics, Jonah pre-P5 gate, P5 stop-for-Suman, no external publishing. |
| `stage.openclaw.ivy_jonah.accept_source_approval` | `stage` | `1.0.0` | `prompts/stages/openclaw-ivy-jonah/accept-source-approval/v1.0.0.md` | Bind `approved_source_packet` to the selected P3/P5 decision and block ambiguous or stale approvals. |
| `stage.openclaw.ivy_jonah.build_draft_package` | `stage` | `1.0.0` | `prompts/stages/openclaw-ivy-jonah/build-draft-package/v1.0.0.md` | Produce P4 draft package, source trail, headline/visual/source expectations, no publish. |
| `stage.openclaw.ivy_jonah.editor_review` | `stage` | `1.0.0` | `prompts/stages/openclaw-ivy-jonah/editor-review/v1.0.0.md` | Jonah artifact-aware review, bounded questions, allowed verdicts, `p4_editor_review.md` contract. |
| `stage.openclaw.ivy_jonah.revise_draft` | `stage` | `1.0.0` | `prompts/stages/openclaw-ivy-jonah/revise-draft/v1.0.0.md` | Apply Jonah or Suman feedback within one revision turn, preserving source trail and avoiding unreviewed scope expansion. |
| `stage.openclaw.ivy_jonah.validate_editorial_state` | `stage` | `1.0.0` | `prompts/stages/openclaw-ivy-jonah/validate-editorial-state/v1.0.0.md` | Treat draft/editor-verdict hash mismatch as a hard block and name the repair path. |
| `stage.openclaw.ivy_jonah.p5_final_approval` | `stage` | `1.0.0` | `prompts/stages/openclaw-ivy-jonah/p5-final-approval/v1.0.0.md` | Render explicit Suman choices: approve packet, revise, park, reject. Preserve `external_publish_allowed: false`. |

Recommended `prompt_refs` by Ivy/Jonah stage:

| Stage | Prompt refs |
| --- | --- |
| `accept_source_approval` | `policy.openclaw.review_only_human_gate`, `lane.openclaw.ivy_jonah_editorial`, `stage.openclaw.ivy_jonah.accept_source_approval` |
| `build_draft_package` | `identity.openclaw.ivy_or_research`, `policy.openclaw.editorial_public_boundary`, `lane.openclaw.ivy_jonah_editorial`, `stage.openclaw.ivy_jonah.build_draft_package` |
| `editor_review` | `identity.openclaw.jonah_editor`, `policy.openclaw.editorial_public_boundary`, `lane.openclaw.ivy_jonah_editorial`, `stage.openclaw.ivy_jonah.editor_review` |
| `revise_draft` | `identity.openclaw.ivy_or_research`, `policy.openclaw.editorial_public_boundary`, `lane.openclaw.ivy_jonah_editorial`, `stage.openclaw.ivy_jonah.revise_draft` |
| `validate_editorial_state` | `policy.openclaw.editorial_public_boundary`, `lane.openclaw.ivy_jonah_editorial`, `stage.openclaw.ivy_jonah.validate_editorial_state` |
| `p5_final_approval` | `policy.openclaw.review_only_human_gate`, `lane.openclaw.ivy_jonah_editorial`, `stage.openclaw.ivy_jonah.p5_final_approval` |

### Jarvis Weekly Update Shadow Lane

| Prompt id | Kind | Version | File | Source basis |
| --- | --- | --- | --- | --- |
| `lane.openclaw.jarvis_weekly_update_shadow` | `lane` | `1.0.0` | `prompts/lanes/openclaw-jarvis-weekly-update-shadow/v1.0.0.md` | Blackboard decision loop plus weekly report/operator-note style. |
| `stage.openclaw.weekly.discover_artifact` | `stage` | `1.0.0` | `prompts/stages/openclaw-weekly-update/discover-artifact/v1.0.0.md` | Discover supplied weekly fixture/artifact without reading or mutating live vault state. |
| `stage.openclaw.weekly.blackboard_readback` | `stage` | `1.0.0` | `prompts/stages/openclaw-weekly-update/blackboard-readback/v1.0.0.md` | Preserve item id, bucket, owner, evidence link, checked flag, and read state. |
| `stage.openclaw.weekly.suman_review_gate` | `stage` | `1.0.0` | `prompts/stages/openclaw-weekly-update/suman-review-gate/v1.0.0.md` | Render explicit choices: `read_clear`, `follow_up_requested`, `defer`, `blocked`. |
| `stage.openclaw.weekly.route_follow_up` | `stage` | `1.0.0` | `prompts/stages/openclaw-weekly-update/route-follow-up/v1.0.0.md` | Route only from receipt-backed decisions, shadow-only; no Obsidian/Telegram/runtime writes. |

Recommended `prompt_refs` by weekly stage:

| Stage | Prompt refs |
| --- | --- |
| `discover_weekly_artifact` | `policy.openclaw.read_only_shadow`, `lane.openclaw.jarvis_weekly_update_shadow`, `stage.openclaw.weekly.discover_artifact` |
| `readback_blackboard_card` | `policy.openclaw.read_only_shadow`, `lane.openclaw.jarvis_weekly_update_shadow`, `stage.openclaw.weekly.blackboard_readback` |
| `suman_review_gate` | `policy.openclaw.review_only_human_gate`, `lane.openclaw.jarvis_weekly_update_shadow`, `stage.openclaw.weekly.suman_review_gate` |
| `route_follow_up` | `policy.openclaw.read_only_shadow`, `lane.openclaw.jarvis_weekly_update_shadow`, `stage.openclaw.weekly.route_follow_up` |

## Tests That Should Fail If Prompts Are Missing

Add these tests before or alongside the prompt migration so missing refs fail
closed.

1. `tests/test_openclaw_prompt_inventory.py`
   - Load `PromptRegistry.load(ROOT / "prompts")`.
   - Resolve every recommended prompt ref above.
   - Assert each resolved prompt has an exact version, content hash, active
     status, and a path under `prompts/`.
   - This should fail today because none of the OpenClaw lane/stage prompts
     exist.

2. `tests/test_openclaw_prompt_inventory.py`
   - Load `workflows/ivy_jonah_editorial.yaml` and
     `workflows/jarvis_weekly_update_shadow.yaml` through
     `agent_workflow_kernel.dsl.load_workflow_file`.
   - Assert every target stage has non-empty `prompt_refs`.
   - Assert each stage includes exactly one `lane.*` prompt and exactly one
     stage prompt whose id matches the stage mapping in this report.
   - This should fail today because both workflow YAML files have zero
     `prompt_refs`.

3. `tests/test_example_workflows.py`
   - Replace the temporary `workflow_to_contract` bridge or add a companion test
     that uses the real DSL loader, so fixture tests cannot accidentally drop
     `prompt_refs`.
   - Assert `to_plain_data(workflow)["stages"][i]["prompt_refs"]` preserves the
     refs from YAML.

4. `tests/test_openclaw_ivy_lane_adoption.py`
   - Once the adoption mapper or runner receives a prompt registry, assert Ivy
     and weekly receipts for prompt-backed stages include
     `prompt_provenance.refs`, `prompt_bundle_digest`, and rendered context
     digests.
   - This should fail today because `make_adapter_receipt` records a
     `context_packet_ref` but no prompt provenance for the OpenClaw adapter
     fixture receipts.

5. `tests/test_openclaw_prompt_inventory.py`
   - Add a source-import manifest validation test: each OpenClaw source entry
     has a logical `openclaw://...` source URI, a source hash, a mapped prompt
     id/version, and no raw `/Users/sunny` or `/Users/suman` path in workflow
     YAML.
   - If source text changes without a new adapter-source version/hash, the test
     should fail.

6. `tests/test_openclaw_weekly_update_adoption.py`
   - Extend the existing explicit-human-gate test to assert the weekly human
     gate prompt ref exists and the policy prompt is
     `policy.openclaw.review_only_human_gate`.
   - Extend the shadow-only route test to assert `route_follow_up` uses
     `policy.openclaw.read_only_shadow`.

## Migration Risks

- **Prompt-free workflow fixtures still pass.** Current OpenClaw adoption tests
  validate stage graphs and fixture mapping, not prompt registry completeness.
  This can make a prompt migration look done while receipts still lack
  reproducible prompt provenance.
- **Adapter receipts bypass prompt provenance.** The OpenClaw mapper creates
  adapter receipts directly with `context_packet_ref`. Prompt-backed execution
  needs either the kernel runner path or an adapter helper that resolves
  PromptRefs and records prompt provenance before receipts are written.
- **Policy prompts are too generic today.** `policy.no_external_effects` is a
  useful sample, but the two lanes need explicit read-only shadow and editorial
  public-boundary policies matching their workflow-level forbidden actions.
- **OpenClaw source drift.** Ivy, Jonah, weekly, and Blackboard source files can
  change independently of AWK. Import versions should include source hashes, and
  source drift should require a new adapter-source snapshot or packaged prompt
  version bump.
- **Workflow budget conflict.** Jonah's editorial-review skill says to ask up
  to three bounded questions; `ivy_jonah_editorial.yaml` currently budgets
  `max_questions: 4`. Pick one contract before freezing
  `stage.openclaw.ivy_jonah.editor_review@1.0.0`.
- **Read/clear is not approval.** The weekly lane must preserve Blackboard's
  rule that ordinary checkboxes are read/clear unless a linked action block
  provides explicit approval or follow-up.
- **P5 approval is not public publishing.** Ivy/Jonah P5 can approve a local
  packet or request revisions, but external posting, browser publishing, push,
  deploy, or email must remain outside this workflow unless a separate explicit
  approval and workflow exist.
- **Internal paths and sensitive context.** Source files and fixture metadata
  contain host paths and internal artifact locations. Workflow YAML should use
  prompt ids and logical source URIs; rendered inputs and receipts should avoid
  leaking raw host paths unless they are explicitly redacted or fixture-local.
- **System-action prompts should stay contracts.** Discovery, readback, routing,
  and hash validation are deterministic stages. Their stage prompts should
  define inputs, outputs, policy, and receipts, not invite an LLM to reinterpret
  deterministic evidence.
- **No live readback in this report.** Because this wave is explicitly
  non-mutating and did not touch oldmac, it cannot prove current live OpenClaw
  prompt/runtime parity. Treat it as the migration map, not live adoption
  evidence.

## Recommended Migration Order

1. Add the OpenClaw adapter-source import manifest with hashes for the source
   materials listed above.
2. Add shared policy prompts for read-only shadow, review-only human gates, and
   editorial public boundaries.
3. Add packaged Ivy/Jonah identity, lane, and stage prompts.
4. Add packaged weekly lane and stage prompts.
5. Add `prompt_refs` to both workflow YAML files using exact `1.0.0` versions.
6. Add prompt inventory tests that fail closed when a workflow stage lacks its
   lane/stage PromptRef or when a PromptRef does not resolve.
7. Connect OpenClaw adoption receipts to resolved prompt/context provenance
   before treating prompt migration as complete.
