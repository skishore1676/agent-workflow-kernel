# Architecture Sketch

## Kernel Loop

```text
WorkflowDef
  -> WorkflowInstance
  -> StageRun
  -> PromptRef + ContextPacket
  -> RuntimeAdapter or SurfaceAdapter
  -> Artifact + Receipt
  -> Transition
  -> Next Stage / Human Gate / Done / Blocked
```

## Core Objects

- `WorkflowDef`: versioned graph of stages and transitions.
- `WorkflowInstance`: one running workflow with current state and history.
- `StageDef`: declared work node.
- `StageRun`: one attempt at executing a stage.
- `Transition`: structured movement between stages.
- `PromptRef`: versioned prompt template reference.
- `ContextPacket`: bounded rendered input to a worker or script.
- `Receipt`: immutable evidence of what happened.
- `ArtifactRef`: hashed pointer to generated or inspected material.
- `PolicyGate`: approval/risk decision.
- `AdapterInvocation`: runtime/surface call with status and metadata.

## Stage Types

- `agent_work`
- `agent_gate`
- `a2a_review_loop`
- `human_gate`
- `system_action`
- `wait_schedule`
- `recovery`
- `blocked`

## Adapter Families

- Runtime adapters: Codex, OpenAI/Anthropic, shell, browser, human.
- Surface adapters: Obsidian, Telegram, local Markdown, Sheets, Slack later.
- Host adapters: OpenClaw first.
- Lane adapters: OR Research, Bumblebee, Radhe, Kamandal, Mala, future lanes.

## Policy Zones

Always require explicit approval for:

- public publishing;
- live trades, broker actions, and money movement;
- deploys and production mutations;
- auth and credential changes;
- external sends;
- destructive cleanup;
- high-cost compute.

