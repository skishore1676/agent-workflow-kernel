# A2A Review Stage

## Purpose

`a2a_review_loop` is a generic stage type for bounded producer/reviewer work.
It exists when the outcome improves because one agent can challenge another
agent's evidence, assumptions, artifact quality, or revision choices before the
workflow advances.

The kernel owns the contract, budgets, receipts, proof requirements, state
transitions, and policy checks. Runtime adapters own the concrete agent sessions
and any host-specific message transport. Surface adapters may show the result to
humans, but the surface is not the durable source of truth.

## When To Use A2A

Use A2A when all of these are true:

- the producer has a concrete artifact, draft, proposal, plan, or evidence
  packet to defend;
- the reviewer has a distinct lens, rubric, or role that can improve the work;
- the useful interaction can be bounded by question and revision budgets;
- the expected output is a structured verdict or revised artifact, not an open
  brainstorming chat;
- policy gates can stop risky side effects before they happen;
- the transcript and proof can be recorded as receipts.

Good examples:

- a generic quality reviewer probes a Codex proposal before it becomes a work
  handoff;
- Jonah reviews Ivy's public article package and asks bounded questions before
  P5;
- a research agent and risk reviewer debate a thesis before a human trading
  review gate;
- a media workflow reviews message quality before expensive audio or video
  generation.

## When A2A Is Harmful

Do not use A2A when the loop adds ceremony without a separate reviewer value.

Harmful patterns include:

- using a reviewer to rubber-stamp deterministic checks that a script can run;
- asking two agents to debate an unbounded product judgment with no stop rule;
- routing ordinary single-agent work through A2A because it feels safer;
- letting a reviewer become the implementer, publisher, deployer, or final
  approver;
- spending reviewer turns after evidence is already sufficient;
- treating transcript prose as proof of a side effect;
- using A2A to bypass a hard human gate.

If the desired behavior is a simple lint, schema check, file existence check,
or known deterministic transformation, use `system_action` plus receipts
instead.

## Stage Definition

Minimum stage shape:

```yaml
type: a2a_review_loop
id: review_stage_id
producer:
  role: producer
  agent_ref: codex
reviewer:
  role: reviewer
  agent_ref: quality_reviewer
owner:
  role: orchestrator
  agent_ref: main
review_goal: "Probe whether the artifact satisfies the handoff."
source_artifacts: []
acceptance_criteria: []
required_artifacts: []
forbidden_actions: []
risk_policy_ref: default
question_budget:
  max_questions: 3
  max_turns: 7
  timeout_seconds_per_turn: 600
revision_budget:
  max_revisions: 1
  allow_reviewer_to_request_revision: true
proof_requirements:
  require_transcript: true
  require_runtime_proof: true
  require_artifact_hashes: true
stop_conditions:
  - pass
  - block
  - needs_human
  - revision_budget_exhausted
```

The workflow DSL may add convenience fields, but the normalized kernel object
should retain these concepts.

## Roles

| Role | Owns | Must Not Own |
| --- | --- | --- |
| Producer | Producing or revising the artifact under review, answering questions, citing evidence, declaring requested side effects. | Final approval, reviewer verdict, hidden scope expansion. |
| Reviewer | Reading the contract, asking bounded questions, checking artifacts and evidence, returning a verdict. | Implementation, domain truth it does not own, public publish, deploy, trade, auth, money, external send, destructive action. |
| Orchestrator | Creating the stage run, enforcing budgets and policy, persisting receipts, routing next state. | Inventing reviewer conclusions or treating UI surfaces as source of truth. |
| Human approver | Explicit approval for hard gates and subjective calls that exceed the contract. | Routine scheduler work that the workflow can continue safely without. |

The same agent may not be both producer and reviewer for the same stage run
unless the workflow explicitly marks the review as self-checking. Self-checking
does not satisfy gates that require independent review.

## Review Contract

Every run starts from a durable contract.

```text
a2a_review_contract.v1
- workflow_id
- instance_id
- stage_id
- stage_run_id
- producer_ref
- reviewer_ref
- owner_ref
- review_goal
- source_artifacts
- acceptance_criteria
- required_artifacts
- forbidden_actions
- risk_policy_ref
- question_budget
- revision_budget
- transcript_policy
- proof_requirements
- stop_conditions
- expected_verdict_schema
```

The contract must be rendered into the producer and reviewer context packets.
The context packet hash belongs in the stage receipt so a later audit can prove
what each side was asked to do.

## Question And Answer Packets

Reviewer questions are sequential. The reviewer asks one question, receives an
answer or revision, then decides whether another question is still worth
spending.

```text
review_question.v1
- question_id
- stage_run_id
- target_ref
- question
- reason_for_question
- evidence_needed
- expected_response_shape
```

```text
review_answer.v1
- question_id
- stage_run_id
- answer
- revised_artifacts
- evidence_refs
- requested_actions
- side_effects
- approval_required
```

Malformed, evasive, missing, or unevidenced answers still count against the
budget. They should push the stage toward `block` or `needs_human`, not toward a
thin pass.

## Verdict

The generic verdict schema is:

```text
review_verdict.v1
- stage_run_id
- verdict: pass | refine | block | needs_human
- questions_asked
- producer_answers
- acceptance_result
- artifact_result
- risk_result
- required_rework
- residual_risk
- next_broker: script | agent | human | none
- next_owner
- next_action
- proof_refs
```

Verdict meanings:

- `pass`: acceptance criteria are met, required artifacts exist, proof is valid,
  and no hard gate is being crossed by the next transition.
- `refine`: the producer can repair the work within the declared scope and
  remaining revision budget.
- `block`: the stage cannot safely continue because evidence, artifacts,
  proof, schema validity, or bounded repair is missing.
- `needs_human`: the next decision is subjective, high-impact, risky, or
  covered by a hard human gate.

Lane adapters may expose domain-specific verdict labels, but they must map back
to the generic verdict before the kernel transitions.

## Proof

Proof is adapter evidence that the exchange happened as declared. It is not the
same as transcript prose.

Minimum proof references:

- stage contract hash;
- producer and reviewer context packet hashes;
- artifact refs and hashes before and after revision;
- runtime invocation ids or adapter event ids;
- question and answer packet ids;
- verdict receipt id;
- policy decision receipts;
- transcript ref, if retained.

When a runtime supports native A2A tool events, accepted proof should come from
trusted adapter events or response wrappers, not from reviewer-written JSON or
prompt text claiming that a message was sent.

Host-specific proof such as `native_session_proof.v1` is allowed as an adapter
receipt. The kernel should store it behind a generic proof reference.

## Transcript

The transcript is useful for audit and recovery, but it is not the authority for
side effects or approval.

Transcript policy should declare:

- whether the transcript is retained;
- where it is stored;
- retention and redaction rules;
- whether human-visible summaries are generated;
- which fields are safe to expose on surfaces.

Secrets, credentials, private account data, and irrelevant chain-of-thought must
not be stored in the transcript. Store decision evidence and concise reasoning,
not hidden deliberation.

## Budgets

Question budget:

- `max_questions`: default 3;
- `max_turns`: default 7, including producer replies;
- `timeout_seconds_per_turn`: default 600;
- missing or malformed answers count as turns.

Revision budget:

- `max_revisions`: default 1 for rich creative/research loops, 0 for read-only
  review;
- revised artifacts must be new artifact refs with hashes;
- once exhausted, the next verdict is `block` or `needs_human`.

Budgets are ceilings, not goals. The reviewer should stop early once the
verdict is clear.

## Stop Conditions

The stage must stop when any of these occurs:

- reviewer returns `pass`, `block`, or `needs_human`;
- reviewer returns `refine` and no revision budget remains;
- producer requests or implies a hard-gated side effect;
- producer answer is missing, malformed, evasive, or unevidenced after budget;
- required artifact or proof is absent;
- contract scope becomes ambiguous;
- runtime timeout or lease expiry prevents reliable continuation;
- policy evaluation returns `require_human` or `deny`.

The orchestrator records the stop condition in the stage receipt and routes to
the next stage, human gate, retry, recovery, or blocked state.

## Transition Rules

```text
pass
  -> next declared stage, unless policy requires a human gate

refine
  -> producer revision attempt, then same reviewer contract with decremented
     revision budget

block
  -> blocked or recovery stage with evidence and smallest unblock request

needs_human
  -> human_gate with exact decision ask and evidence refs
```

No A2A verdict can approve public publishing, deploys, live trades, auth or
credential mutation, money movement, external sends, or destructive changes.
Those transitions always require a separate policy gate.

## Modeling Bumblebee

Bumblebee maps to `a2a_review_loop` as a generic, read-only quality review:

- producer: Codex or the lane agent that created the artifact;
- reviewer: `quality_reviewer`;
- owner: the orchestrator or Work Ledger equivalent;
- review goal: challenge evidence, criteria coverage, scope, and residual risk;
- question budget: usually 2 or 3 questions;
- revision budget: 0 for pure review, 1 when the producer may repair within
  scope;
- verdict mapping: `pass`, `refine`, `block`, `needs_human`;
- forbidden actions: implementation, publish, deploy, push, merge, auth,
  external send, trading/broker actions, destructive mutation;
- proof: contract, artifact hashes, structured questions and answers, verdict
  receipt, runtime proof when native A2A is used.

This keeps Bumblebee as a protocol plus reusable review skills, not a new owner
of every domain's truth.

## Modeling Ivy And Jonah

Ivy/Jonah maps to the same stage with a specialized reviewer lens:

- producer: Ivy / OR Research, owner of research, P1-P4 drafting, visuals, and
  revision;
- reviewer: Jonah, owner of public editorial review for Ivy P4 packages;
- owner: Jarvis or the workflow orchestrator;
- source artifacts: approved P3, P4 draft package, headline decision, visual
  decision, source list, publish bundle when present;
- question budget: up to 3 bounded editorial questions;
- revision budget: usually 1 bounded P4 revision before escalation;
- domain verdicts: `publishable`, `publishable_with_minor_edits`, `revise`,
  `ask_suman`, `kill`;
- generic mapping: `publishable` and `publishable_with_minor_edits` map to
  `pass`; `revise` maps to `refine`; `ask_suman` maps to `needs_human`; `kill`
  maps to `block` unless the workflow asks for a human rescue decision;
- next policy gate: P5 remains a human publish decision.

The stage may clear a draft for P5 review. It must not publish, send
externally, mutate a browser, or treat Jonah clearance as Suman approval.

## Minimum Acceptance Tests

An implementation slice for `a2a_review_loop` should prove:

- contracts render producer and reviewer context packets with stable hashes;
- question and revision budgets are enforced;
- malformed producer answers cannot produce `pass`;
- verdicts map to transitions deterministically;
- transcript and proof refs are recorded;
- hard policy zones force `needs_human` or a `human_gate`;
- Bumblebee-style generic review and Ivy/Jonah editorial review are expressible
  without custom kernel code.
