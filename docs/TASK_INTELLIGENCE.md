# AgentFlow Task Intelligence Design

## 1. Purpose

This document defines the behaviour AgentFlow's own Planning Agent (see
`SPRINT_PLANNING_AND_BACKLOG.md` §31) should exhibit when turning a short,
informal idea into a reviewable requirement, and when decomposing approved
work into a task graph.

The reference model is Task Master, the CLI tool used to plan AgentFlow's
own delivery (`.taskmaster/`). This document names Task Master's specific
behaviours, states how AgentFlow's Planning Agent should replicate them,
and where AgentFlow must deliberately diverge, so this capability is not
lost or reinvented ad hoc once AgentFlow starts planning its own work.

This document does not redefine `BacklogItem`, `PlanningSession`,
`PlanningProposal` or `Task`; those are owned by
`SPRINT_PLANNING_AND_BACKLOG.md` and `DOMAIN_AND_ORCHESTRATION_DESIGN.md`.
It defines the enrichment, decomposition and dependency-inference
behaviour that operates on top of them.

## 2. Reference Behaviour: Task Master

Three Task Master behaviours motivate this document:

```text
add-task
  short prompt -> AI-generated title, description, implementation
  details, test strategy, plus AI-suggested dependencies

expand-task
  one task -> N subtasks, N chosen by complexity, each with its own
  dependency chain

next-task / dependency resolution
  the tool computes which tasks are unblocked and recommends one,
  rather than the human tracking the dependency graph by hand
```

One behaviour is worth capturing precisely, from a working session on
2026-09-23: running `add-task --dependencies=4,6` produced a task
depending on `1,2,3,4,5,6`. The tool expanded an explicit list of 2 to 6,
tagging the 4 it added itself as `(AI suggested)` versus the 2 given
explicitly. The distinction was visible in the CLI output, but nothing
forced a human to confirm the additions before the task was created with
them attached.

## 3. Mapping to AgentFlow Concepts

```text
Task Master "add-task" prompt
  -> BacklogItem free text (SPRINT_PLANNING_AND_BACKLOG §5)

generated title/description/details/test strategy
  -> PlanningProposal output (§15), Backlog Enrichment (§9)

AI-suggested dependencies
  -> Task dependency graph edges (§19), tagged by provenance

"expand-task"
  -> Task decomposition during a Planning Session (§14/§18)

complexity analysis
  -> Planning Profile selection (§20)

"next-task"
  -> Sprint Execution Queue eligibility (§28)
```

## 4. Requirement Elaboration

Input: a BacklogItem containing only free text — no title, no acceptance
criteria, no design (`SPRINT_PLANNING_AND_BACKLOG.md` §4).

When the Planning Agent is invoked against one or more BacklogItems, it
should produce a PlanningProposal containing:

```text
clarified title
description
design considerations
implementation approach
test strategy
proposed acceptance criteria
```

This mirrors `add-task`'s generated title/description/implementation
details/test strategy, but the output lands as a `PlanningProposal` in
`DRAFT` status (`SPRINT_PLANNING_AND_BACKLOG.md` §15), not as an
authoritative Task — Task Master has no equivalent review gate, and
AgentFlow must not skip it.

## 5. Task Decomposition

When a `PlanningProposal` or approved `Task` is too large for one Run, the
Planning Agent should propose a decomposition into smaller Tasks, each
with:

```text
title
description
dependencies on sibling tasks from this decomposition
dependencies on tasks outside this decomposition, if any
```

This mirrors `expand-task`, with two differences required by
`SPRINT_PLANNING_AND_BACKLOG.md` §16-18: capture boundaries (how many
BacklogItems went in) must not determine decomposition boundaries (how
many Tasks come out), and every generated Task keeps the same
`SUGGESTED` -> `REVIEWED` -> `APPROVED` provenance as any other planning
output.

Re-running decomposition against a Task that already has human-edited
subtasks should not silently discard those edits (see §8).

## 6. Dependency Inference and Provenance

This is the part Task Master gets partly right and AgentFlow must
tighten: an AI-suggested dependency must never become indistinguishable
from a human-specified one once a Task reaches `READY`.

Every dependency edge should carry:

```text
source: EXPLICIT | AI_SUGGESTED
status: SUGGESTED | REVIEWED | APPROVED | REJECTED
rationale: short text - why the planner believes this edge exists
```

An `AI_SUGGESTED` edge:

```text
may appear in the planning workspace immediately, for fast feedback
must not count toward Task Readiness (§26) or release eligibility
  until REVIEWED
must show its rationale, not just the edge
```

This applies the provenance principle already stated in
`SPRINT_PLANNING_AND_BACKLOG.md` §9 ("Suggested information must remain
distinguishable from reviewed or approved requirements") specifically to
dependency edges — the current dependency graph design (§19) does not yet
call this out, and the Task Master session above showed why it matters:
an AI added 4 dependencies to an explicit list of 2, and only the CLI's
`(AI suggested)` label — easy to miss — distinguished them.

## 7. Complexity-Driven Decomposition

Task Master's `analyze-complexity` assigns each task a complexity score
that determines how many subtasks `expand-task` generates. AgentFlow
should fold this into Planning Profile selection
(`SPRINT_PLANNING_AND_BACKLOG.md` §20) rather than introduce a second,
competing scoring mechanism: a `MAJOR_FEATURE` or `MIGRATION` profile
implies deeper decomposition than `QUICK_FIX`, and the Planning Agent
should use the resolved profile — not an independent complexity score — to
decide how far to decompose.

## 8. What AgentFlow Should Not Copy From Task Master

Task Master runs unattended in a CLI; its AI-suggested dependencies and
generated task content become authoritative the moment the command
returns, and re-expanding a task with `--force` fully regenerates and
discards prior edits. AgentFlow must not adopt any of that directly:

```text
generated task content is SUGGESTED until REVIEWED/APPROVED, per the
  existing Backlog Enrichment model (§9)

AI-suggested dependencies are visible as suggestions, never silently
  merged into the approved dependency graph (§6 above)

re-running decomposition on a Task with human-edited subtasks must not
  discard those edits by default
```

## 9. Open Questions

Phase 1 resolved none of these (no planning was built). Proposed answers
and the remaining open item are recorded in `PHASE2_PLANNING.md` §3.

```text
Does re-running decomposition on an edited Task merge or replace
  subtasks, and how are conflicts surfaced?
Is dependency rationale stored per-edge permanently, or only surfaced
  at generation time and then discarded once reviewed?
Does complexity-driven decomposition need anything beyond Planning
  Profile selection, or does §20 fully subsume it?
Should the Planning Agent's suggestions be generated by the same agent
  role/adapter used for implementation, or a dedicated PLANNING role
  session per SPRINT_PLANNING_AND_BACKLOG §31?
Should decomposition suggestions only surface within a Planning Session
  or Task review chat, or can an agent also raise one reactively
  mid-Run when it judges a Task too large — and if so, is that a
  decomposition suggestion or a clarifying question (AGENT_ADAPTER §12)?
```

## 10. Phase Placement

This is Phase 2 scope (P2.2 Sprint Planning, P2.9 Prompt Library in
`PHASED_DELIVERY_PLAN.md`). Task 7 ("Review Phase 1 design decisions and
consolidate Phase 2 planning") should fold any further discoveries into
this document before Phase 2 implementation planning begins.
