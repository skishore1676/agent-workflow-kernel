# Worker Goal: A2A Stage And Policy Gates

## Goal

Design the generic agent-to-agent review stage and policy gate model.

## Scope

Own:

- `a2a_review_loop` stage type;
- producer/reviewer roles;
- proof and transcript requirements;
- question budgets;
- revision budgets;
- verdict schema;
- policy gate decisions;
- hard approval boundaries.

Do not own:

- runner implementation;
- prompt registry storage;
- concrete OpenClaw session API.

## Expected Artifact

Write or update:

- `docs/synthesis/a2a-stage.md`
- `docs/synthesis/policy-gates.md`

## Acceptance Criteria

- Defines when A2A is useful and when it is harmful.
- Captures public publish, deploy, trade, auth, money, external send, and
  destructive changes as hard human gates.
- Includes enough structure to model Ivy/Jonah and Bumblebee.

