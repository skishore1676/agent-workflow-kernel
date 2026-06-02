# Wave 16 Supervisor Autonomy Audit

Date: 2026-06-01

Scope: audit why recent AWK and Codex supervisor waves still required too much
Suman prompting, then add minimal repo-local operating guidance. This worker did
not mutate oldmac, OpenClaw runtime state, Obsidian/Northstar, Telegram,
credentials, auth, trading, deploy, or public-send surfaces.

## Verdict

The issue is not that the technical gates are absent. The repo has increasingly
good proof gates for runner recovery, oldmac/live-readonly evidence, prompt
provenance, and surface readback. The autonomy gap is that the supervisor
operating contract did not make those gates mandatory before handoff, so a wave
could stop at "worker appears done" or "proof exists somewhere" and leave Suman
to prompt the next poll, oldmac verification, or terminal readback.

Minimal fix applied: `docs/control.md` now has explicit supervisor autonomy
rules for worker terminal proofs, patient polling, runner completion evidence,
oldmac verification labels, and no-handoff-until-terminal closeout.

I did not edit `/Users/suman/.codex/skills/codex-supervisor-lane/SKILL.md`.
The repo-local rule is enough for this slice; a skill update should happen only
after the supervisor has used this rule successfully in at least one more wave.

## Findings

### F1. Worker completion was treated as a chat state instead of a terminal proof

Evidence:

- `docs/control.md` previously said workers should have bounded goals and leave
  blocked notes, but it did not define the exact proof bundle the supervisor
  must collect before handoff.
- The supervisor skill says to let workers run and read finals, commits,
  statuses, and artifacts, but it leaves the terminal checklist implicit.
- Recent reports did include verification summaries, but not a reusable rule
  that every worker must return final artifact/commit, verification/blocker, and
  post-commit status.

Impact:

Suman had to become the poller/scheduler when a worker was quiet or when a final
was incomplete but not clearly blocked.

Operating rule:

Do not close a wave while any worker lacks one of: final artifact/report and
commit hash, exact verification/blocker, post-commit status, or explicit
blocked note with the smallest human decision.

### F2. Patient polling existed as advice, not a stop condition

Evidence:

- The supervisor skill says slow workers should be polled/read before nudging
  or replacement.
- `docs/control.md` did not encode that quiet workers are not a handoff
  condition.

Impact:

Supervisor waves could return to Suman because a worker was slow, even when the
safe autonomous action was simply to poll/read again later.

Operating rule:

Quiet is not blocked. Poll patiently; nudge only for safety, wrong branch/tree,
scope drift, repeated blocker, or declared deadline. Preserve local branch work
before replacing a worker.

### F3. Runner completion needed explicit receipt/ledger/readback language

Evidence:

- `docs/synthesis/runner-recovery.md` says delegated output is not accepted just
  because a child thread says it is done; the parent must verify receipt,
  transcript pointer, artifacts, and policy scope.
- The owned runner and local review paths depend on recoverable ledger events,
  receipt exports, artifacts, and readback, not conversational completion.
- `scripts/openclaw_live_cutover.py` and its tests already encode blocked
  behavior when Obsidian/Blackboard/Telegram readback is untrusted.

Impact:

Without a supervisor rule, a chat final could be mistaken for runner completion
even when ledger stages, readback, or receipt trust had not been checked.

Operating rule:

Runner completion means every relevant stage is terminal, waiting on a named
human gate, or blocked with a named repair path, and that status is proven from
receipts, ledger state, artifacts, and readback.

### F4. Oldmac verification labels were still easy to blur

Evidence:

- Wave 10 and Wave 12 used oldmac live-readonly evidence but had to distinguish
  stdin-piped/local exporter fallback from deployed oldmac readiness.
- Wave 13 improved this by proving oldmac `post_pull_sync.sh`,
  `verify_host_setup.sh`, deployed OpenClaw fixture export, and oldmac AWK
  `./scripts/check.sh`.
- The existing safety boundary says not to mutate oldmac/runtime surfaces, but
  the supervisor closeout rule did not require host/root/commit/command/readback
  evidence before using "oldmac ready" language.

Impact:

Suman had to ask for the live-host proof that should have been part of the
handoff.

Operating rule:

If the proof ran from local fixtures, local scripts, or stdin-piped helpers,
label it local audit/fallback evidence. Deployed oldmac readiness requires the
live oldmac path, host/root/commit, command, output status, artifact paths, and
readback.

### F5. No-handoff-until-terminal was not a named supervisor invariant

Evidence:

- Wave reports list remaining gates, but the repo did not state that the
  supervisor should continue into the next safe autonomous step instead of
  asking Suman to schedule obvious follow-up.
- The global operating preference is end-to-end ownership, but the AWK control
  doc needed a project-local rule that binds worker polling, verification, and
  oldmac proof together.

Impact:

The user-facing handoff could happen between gates rather than after the wave
was actually terminal.

Operating rule:

Final handoff only after worker finals read, commits/status inspected, required
tests run or blocked with exact output, relevant live-readonly/readback captured,
and either the next autonomous wave is launched or a named Suman decision is the
true blocker.

## Recommended Skill Note Patch

Do not apply this automatically yet. If this repo-local rule proves useful in
the next supervisor wave, add a compact section to
`/Users/suman/.codex/skills/codex-supervisor-lane/SKILL.md`:

```text
No-Handoff-Until-Terminal:
- Treat quiet workers as active until polled/read and proven blocked, unsafe,
  wrong-branch, or deadline-expired.
- A worker is terminal only with final artifact/report, commit hash if changed,
  exact verification or blocker, and post-commit git status.
- Runner completion requires receipt/ledger/artifact/readback proof; a chat
  final is not enough.
- Oldmac readiness requires live-host path, commit, command, output status,
  artifact paths, and readback; otherwise label the result fixture/local audit.
- Close the supervisor wave only after every worker is terminal and every safe
  next step has been taken, or after a named Suman decision gate is reached.
```

## Verification Plan For This Slice

Required after writing this report:

- `python3 -m unittest discover -s tests`
- `./scripts/check.sh`
- commit exactly this doc slice with message:
  `Add supervisor autonomy hardening audit`
- `git status --short` after commit
