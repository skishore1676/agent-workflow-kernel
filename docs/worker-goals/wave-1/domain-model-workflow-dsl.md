# Worker Goal: Domain Model And Workflow DSL

## Goal

Design the portable kernel domain model and workflow graph definition format.

## Scope

Own:

- `WorkflowDef`;
- `WorkflowInstance`;
- `StageDef`;
- `StageRun`;
- `Transition`;
- `ArtifactRef`;
- `Receipt`;
- workflow versioning;
- how graph transitions are represented without turning config into a
  programming language.

Do not own:

- concrete runner implementation;
- OpenClaw adapter details;
- prompt template authoring;
- surface-specific parsing;
- domain-specific OR/Radhe/Mala logic.

## Expected Artifact

Write or update:

- `docs/synthesis/domain-model.md`
- `docs/synthesis/workflow-dsl.md`

## Acceptance Criteria

- Can express Bumblebee review, Ivy/Jonah review loop, trading research gate,
  and Radhe review pipeline.
- Identifies what belongs in declarative workflow config versus adapter code.
- Includes at least one YAML-like example and one schema/table sketch.
- Names open questions and risks.

