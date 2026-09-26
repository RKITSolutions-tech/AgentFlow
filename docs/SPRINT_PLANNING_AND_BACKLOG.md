# AgentFlow Sprint Planning and Backlog Design

## 1. Purpose

This document defines AgentFlow backlog capture, sprint planning, task generation, enrichment, readiness and release.

Core principle:

```text
Capture quickly
Plan deliberately
Execute only when ready
```

## 2. Product Flow

```text
Idea
 ↓
Backlog
 ↓
Sprint Selection
 ↓
Planning
 ↓
Enrichment
 ↓
Task Generation
 ↓
Task Review
 ↓
Acceptance Review
 ↓
Readiness Gate
 ↓
Release to Pipeline
 ↓
Ralph Execution
 ↓
Verification
 ↓
Commit
 ↓
Sprint Progress
```

## 3. Top Level Product Areas

```text
Projects
Backlog
Sprints
Sessions
Runs
```

Project scoped:

```text
Overview
Documentation
Backlog
Sprints
Tasks
Sessions
Runs
Files
Git
Tests
Terminal
Settings
```

## 4. Backlog Purpose

Backlog is a deliberately low-friction inbox for:

- ideas
- problems
- screenshots
- observations
- potential improvements
- defects
- future work

A backlog item does not require:

- formal title
- acceptance criteria
- design
- technical detail
- repository assignment
- priority
- pipeline
- estimate

## 5. Fast Capture

Minimum capture UI:

```text
┌─────────────────────────────────────────┐
│ Add backlog item                        │
│                                         │
│ [ Describe idea/problem...            ] │
│                                         │
│ Drop images/files here                  │
│                                         │
│                           [ Add ]        │
└─────────────────────────────────────────┘
```

The only mandatory input is text or an attachment.

Optional:

```text
Project
Tags
Priority
Source
URL
Notes
```

## 6. Backlog Item

Suggested fields:

```text
id
project_id
text
title
status
priority
created_at
updated_at
created_by
source_type
source_reference
```

Status:

```text
INBOX
TRIAGED
SELECTED
PLANNING
PLANNED
READY
RELEASED
ARCHIVED
REJECTED
```

## 7. Attachments

Backlog items may contain:

```text
screenshots
photos
design mockups
logs
text files
PDFs
video
```

Attachments remain available throughout the full lifecycle.

## 8. Backlog Inbox

Optimise for triage.

Useful actions:

```text
Add to Sprint
Enrich
Merge
Split
Archive
Assign Project
Tag
```

Selecting an item should open a right-hand inspector rather than force navigation away.

## 9. Backlog Enrichment

Before Sprint assignment, an item may be enriched using:

- Project documentation
- repository structure
- screenshots
- existing tasks
- previous Sprints
- standards
- Skills
- architecture decisions

Suggested information must remain distinguishable from reviewed or approved requirements.

Possible provenance states:

```text
SUGGESTED
REVIEWED
APPROVED
```

## 10. Sprint

Sprint is a bounded delivery objective.

Suggested fields:

```text
id
project_id
name
goal
description
status
start_date
target_date
planning_profile
created_at
updated_at
```

Suggested states:

```text
DRAFT
PLANNING
REVIEW
READY
EXECUTING
VERIFYING
COMPLETE
CANCELLED
```

## 11. Sprint Goal

Every Sprint should have a clear delivery goal that becomes planning context.

## 12. Sprint Documentation

Sprint references Project documentation rather than duplicating it.

Hierarchy:

```text
Project
↓
Sprint
↓
Task
↓
Run
```

Example:

Project:

```text
architecture.md
coding-standards.md
```

Sprint:

```text
job-redesign.md
mobile-ui.md
```

Task:

```text
photo-upload.md
```

## 13. Sprint Planning Workspace

Recommended layout:

```text
┌─────────────────┬──────────────────────────────┬───────────────────────┐
│ Backlog         │ Sprint Plan                  │ Inspector             │
│                 │                              │                       │
│ □ BL-141        │ Goal                         │ Selected item         │
│ ☑ BL-142        │ Engineer workflow            │                       │
│ ☑ BL-143        │                              │ Original text         │
│ □ BL-144        │ Proposed work                │ Attachments           │
│                 │                              │ Related docs          │
│                 │ T01 Status controls          │ Suggestions           │
│                 │ T02 Blocker workflow         │                       │
│                 │ T03 Photo upload             │                       │
│                 │ T04 Mobile layout            │                       │
│                 │                              │                       │
│                 │ [Generate / Refine Plan]     │                       │
└─────────────────┴──────────────────────────────┴───────────────────────┘
```

## 14. Planning Session

PlanningSession represents one planning activity against a Sprint.

Input may include:

```text
Sprint goal
Selected backlog items
Project documentation
Sprint documentation
Relevant Skills
Repository structure
Existing code
Previous tasks
Previous Sprints
Existing Issues
Architecture decisions
```

Output may include:

```text
clarified scope
affected components
design considerations
task decomposition
task dependencies
acceptance criteria
recommended tests
pipeline requirements
documentation references
skills
risks
unknowns
```

## 15. Planning Proposal

A PlanningProposal is not directly executable.

Status:

```text
DRAFT
REVIEW
APPROVED
REJECTED
SUPERSEDED
```

Human review separates agent suggestions from approved Sprint planning.

## 16. Backlog to Task Mapping

Must support:

```text
1 backlog item → 1 task
1 backlog item → many tasks
many backlog items → 1 task
many backlog items → many tasks
```

Capture boundaries should not determine implementation boundaries.

## 17. Planned Work Item

A temporary PlannedWorkItem may sit between backlog and final Tasks.

This lets the planner propose structure before final Task records are created.

## 18. Task Generation

Each approved Task should normally include:

```text
title
description
purpose
source backlog items
affected repositories
affected components
dependencies
documentation references
Skills
acceptance criteria
test requirements
pipeline profile
Git strategy
execution settings
risks
unknowns
```

## 19. Dependency Graph

Planning should generate a dependency graph.

Example:

```text
          ┌── T02 ──┐
T01 ──────┤         ├── T05
          └── T03 ──┘
                │
                └── T04
```

Initial execution may remain linear, but the graph determines eligibility and prepares for future parallelism.

Dependency types:

```text
BLOCKS
REQUIRES
RELATED
```

## 20. Planning Profiles

Examples:

```text
QUICK_FIX
STANDARD_FEATURE
MAJOR_FEATURE
REFACTOR
MIGRATION
EXPERIMENT
```

A profile defines the amount of planning required.

Example:

```yaml
profile: STANDARD_FEATURE

requires:
  design_reference: true
  task_breakdown: true
  dependencies: true
  acceptance_review: true
  test_plan: true
  verification_pipeline: true
```

## 21. Acceptance Generation

Planning may propose AcceptanceCriteria.

They remain suggested until reviewed or approved.

Templates may provide common baselines such as:

```text
standard-web-feature
responsive-ui
form-change
api-change
bug-fix
database-change
```

## 22. Test Planning

Each Task should identify intended verification.

Example:

```text
Static:
ruff

Unit:
pytest

UI:
Playwright

Browser diagnostics:
console errors
failed network calls

Visual:
390px screenshot
desktop screenshot
```

## 23. Pipeline Selection

Tasks should normally reference a Pipeline Profile rather than define bespoke execution.

Examples:

```text
web-feature
api-feature
backend-only
documentation-only
database-change
```

If planning identifies a pipeline gap, it should propose a change rather than silently altering reusable pipeline configuration.

UI work may select a Phase 2 pipeline profile that generates screen mockups from the design specification and requires human approval before implementation begins.

## 24. Skills Resolution

Planning should attach relevant Skills based on:

- Project defaults
- Sprint Skills
- repository role
- Task type
- affected component

## 25. Sprint Readiness

Readiness is based on explicit conditions, not arbitrary scoring.

Example:

```text
Sprint Goal                  ✓
Backlog items selected       ✓
Design reviewed              ✓
Tasks generated              ✓
Dependencies resolved        ✓
Acceptance reviewed          !
Pipelines assigned           ✓
Documentation referenced     ✓
Execution configuration      ✓
```

## 26. Task Readiness

Potential conditions:

```text
Description complete
Dependencies known
Repositories assigned
Documentation attached
Acceptance reviewed
Tests defined
Pipeline assigned
Git strategy resolved
No unresolved blocker
```

A Task should enter execution only when required conditions are satisfied.

Manual override should be possible with a recorded reason.

## 27. READY Versus RELEASED

A Task may be fully prepared but intentionally held.

Canonical Task states:

```text
DRAFT
READY_FOR_REVIEW
READY
RELEASED
IN_PROGRESS
BLOCKED
FAILED
COMPLETE
CANCELLED
```

Starting a Run moves a released Task to `IN_PROGRESS`. Successful completion moves it to `COMPLETE`; a recoverable impediment moves it to `BLOCKED`; an unrecoverable or exhausted Run moves it to `FAILED`. An unblocked Task may return to `RELEASED`.

```text
READY
 ↓
Release
 ↓
RELEASED
 ↓
Execution queue
```

Recommended initial release mode:

```text
Manual Sprint release
then automatic linear execution of eligible Tasks
```

## 28. Sprint Execution Queue

Eligibility:

```text
status = RELEASED
dependencies complete
not blocked
Sprint active
Project lock available
```

A blocked or failed Task prevents its dependants from running, but the queue continues with independent eligible Tasks. If none remain, Sprint execution waits for human intervention.

## 29. Sprint Progress

Derived from actual Task state.

Example:

```text
Complete      7
Running       1
Ready         2
Blocked       1
Planning      1
```

## 30. Sprint Control View

Recommended areas:

```text
Progress
Current execution
Task dependency graph
Current Run
Readiness
```

Sprint tabs:

```text
Overview
Planning
Tasks
Dependencies
Documentation
Pipeline
Runs
Evidence
```

## 31. Planning Agent

Planning should use a distinct role from implementation.

Roles:

```text
PLANNING
IMPLEMENTATION
VERIFICATION
GENERAL
```

Planning agent may:

- analyse selected backlog items
- identify duplicates
- combine related items
- identify affected components
- read documentation
- propose design updates
- generate tasks
- create dependency graphs
- propose acceptance
- identify tests
- select pipeline profiles
- identify risks and unknowns

Planning should not automatically:

- modify source code
- commit changes
- release Tasks
- approve its own acceptance criteria
- silently change Project standards

## 32. Design During Planning

Planning may identify a design gap.

This should become an explicit PlanningQuestion or design Task rather than an agent guess.

PlanningQuestion states:

```text
OPEN
ANSWERED
WAIVED
```

Important answers may become PlanningDecisions.

## 33. Promote Knowledge

Useful planning knowledge may be promoted into:

```text
Project Standard
ADR
Skill
Documentation
```

This prevents repeated rediscovery.

## 34. Backlog Sources

Backlog items may originate from:

```text
manual capture
Git issue
agent suggestion
failed Run
test failure
Sprint retrospective
external integration
```

All normalise to BacklogItem.

## 35. Git Issues

Git issues may be imported or synchronised.

```text
Git Issue
↓
Backlog Item
↓
Sprint Planning
↓
Tasks
```

## 36. Run Generated Backlog

If an implementation Run discovers unrelated future work, it should create or propose a new BacklogItem rather than silently expand the current Task.

## 37. Mobile Capture

Mobile flow should be almost frictionless:

```text
Backlog
↓
+
↓
type thought
↓
attach screenshot/photo
↓
Add
```

## 38. Search and Duplicate Detection

Search should index:

```text
text
title
attachment metadata
tags
planning notes
task titles
documentation references
```

The planner may suggest duplicates, but the user decides whether to merge.

## 39. Merge and Split

Merge must retain original identities for traceability.

Split must retain a link to the original backlog item.

## 40. Traceability

Forward:

```text
Backlog Item
↓
Planning Session
↓
Planning Proposal
↓
Sprint
↓
Task
↓
Acceptance Criterion
↓
Run
↓
Iteration
↓
Pipeline Step
↓
Evidence
↓
Commit
```

Reverse navigation should work as well.

## 41. Database Entities

Likely additions:

```text
backlog_items
backlog_attachments
sprints
sprint_document_refs
planning_sessions
planning_proposals
planning_questions
planning_decisions
planned_work_items
planned_work_backlog_links
tasks
task_dependencies
task_document_refs
task_skill_refs
acceptance_criteria
acceptance_template_refs
sprint_releases
```

## 42. Service Boundaries

```text
app/
├── backlog/
├── planning/
├── sprints/
├── tasks/
├── acceptance/
├── documentation/
├── skills/
└── releases/
```

## 43. Planning Tests

Examples:

```text
Backlog item can be created from text only.
Backlog item can be created from attachment only.
One backlog item can generate multiple Tasks.
Multiple backlog items can map to one Task.
Blocked dependencies prevent release.
Required unreviewed acceptance prevents readiness.
Planning proposal cannot execute directly.
Sprint release makes eligible Tasks available to pipeline.
```

A deterministic FakePlanningAgent should support CI testing.

## 44. MVP Backlog Scope

```text
text capture
image/file upload
project assignment
list
search
archive
select into Sprint
```

## 45. MVP Sprint Planning Scope

```text
Sprint creation
Sprint goal
select backlog items
attach documentation
invoke planning agent
generate proposed Tasks
edit proposal
accept Tasks
define dependencies
acceptance criteria
readiness review
Sprint release
```

## 46. Core UX Principle

Backlog and Sprint Planning deliberately optimise for opposite goals.

Backlog:

```text
fast
low friction
incomplete
informal
```

Sprint Planning:

```text
deliberate
structured
reviewable
traceable
executable
```

## 47. Core Planning Principle

Sprint planning transforms:

```text
"What I noticed"
```

into:

```text
"What we are going to change"
```

and then:

```text
"How we know it is correctly completed"
```

before Ralph implementation begins.

## 48. End-to-End Sprint Model

```text
QUICK CAPTURE
    ↓
BACKLOG
    ↓
SPRINT SELECTION
    ↓
CONTEXT + DOCUMENTATION
    ↓
SPRINT PLANNING AGENT
    ↓
DESIGN / QUESTIONS / DECISIONS
    ↓
TASK GRAPH
    ↓
ACCEPTANCE + TEST PLAN
    ↓
HUMAN REVIEW
    ↓
READY
    ↓
SPRINT RELEASE
    ↓
TASK EXECUTION PIPELINE
    ↓
RALPH ITERATION
    ↓
VERIFICATION
    ↓
AUTO COMMIT
    ↓
NEXT ELIGIBLE TASK
    ↓
SPRINT COMPLETE
```

## 49. Implementation Decisions (Phase 2, autonomous assumptions)

These were made without the owner in the loop while building Phase 2 (tasks
18-26) and are recommendations to confirm, not settled decisions.

### Backlog UI (task 18)

- Pages: `/projects/<id>/backlog/inbox` (capture + filter), `/triage` (cards,
  inline priority, bulk move), `/sprint` (items selected for a sprint) and
  `/items/<id>` (full edit + history). A "Backlog" tab is added to the project
  tab strip. The "select an item opens a right-hand inspector" idea of §8 is
  deferred; items open a full page instead (better on mobile).
- No authentication exists, so "non-project members cannot access" is
  implemented as *scoping*: every item route 404s when the item does not belong
  to the project in the URL.
- Archive is a status change (`ARCHIVED`), exposed as `DELETE /items/<id>` and
  `POST /items/<id>/archive`. Items are never hard-deleted from the UI, so
  history and attachments stay traceable. Archiving an archived item is a no-op.
- "Add to sprint" before Sprints exist (task 19) moves an item to `SELECTED`;
  `sprint_id` is attached when a Sprint is chosen. Drag-to-reorder is dropped:
  it needs a persisted position column and the planner, not the inbox, defines
  order. Revisit with the planning workspace (task 20).
- Bulk moves are best effort per item: each id is checked against
  `TRANSITIONS`; the response lists `changed` and `failed` ids.
- Image attachments are shown inline; SVG is always served as a download
  (scriptable), as are non-image files. Max 10 files per capture, 25 MiB each.

### Sprints and planning workflow (tasks 18-19)

- Sprint status uses the §10 lifecycle (`DRAFT` → `PLANNING` → `REVIEW` →
  `READY` → `EXECUTING` → `VERIFYING` → `COMPLETE`, plus `CANCELLED`), not the
  shorter list in the task description. Transitions are enforced by
  `SPRINT_TRANSITIONS` (`app/sprints/models.py`).
- Storage is plain `sqlite3` dataclasses like the rest of the app (not
  SQLAlchemy). Tables: `sprints`, `sprint_document_refs`, `planned_work_items`,
  `planned_work_dependencies`, `planned_work_backlog_links` (many-to-many, §16),
  `sprint_readiness_checks`, `sprint_approvals` (approval history).
- Backlog membership uses the existing `backlog_items.sprint_id` (one sprint per
  item) instead of a separate link table; an item already in another sprint
  cannot be added. `sprint_id` still has no foreign key (SQLite cannot add one
  to an existing column without a table rebuild); nothing deletes Sprints yet.
- Status sync: selecting an item → `SELECTED`; planning starts → `PLANNING`;
  proposal ingested → `PLANNED`; approval → `READY`; revoked approval →
  back to `PLANNED`; failed planning → back to `SELECTED`.
- Cross-sprint dependencies (`SprintDependency`) are deferred: dependencies are
  modelled between planned tasks inside one Sprint (§19), which is what
  readiness and the pipeline need first.
- The planning agent (`app/sprints/planning_agent.py`) is an ordinary
  `AgentAdapter` session with role `PLANNING`. It is prompted to reply with one
  JSON object (`tasks[]` with `ref`, `title`, `description`, `acceptance`,
  `depends_on`, `backlog_items`, `estimate`); backlog ids it invents are dropped,
  bad replies fail the planning run and return the Sprint to `DRAFT`. Agent
  output enters as `SUGGESTED` (§9 provenance); any human edit makes it
  `REVIEWED`, approval makes it `APPROVED`. Resource estimates are a free-text
  size (`S|M|L`), not hours.
- Planning is polled, not blocking: `POST /plan` starts the session and stores
  its id on the sprint; `GET /plan/status` ingests the reply once the session
  completes and the sprint page polls it. Adapters that run synchronously (the
  fake agent) finish within the start call.
- `AGENTFLOW_PLANNING_AGENT` selects `codex` (default) or `fake`. `fake` is the
  deterministic FakePlanningAgent of §43 (one task per backlog item) for CI.
- Re-planning ("Refine plan") deletes only un-reviewed `SUGGESTED` tasks and
  keeps anything a person touched (PHASE2_PLANNING §3: merge, don't replace).
- Readiness (`app/sprints/readiness.py`) is a list of explicit checks:
  `goal_defined`, `items_selected`, `tasks_proposed`, `backlog_covered`,
  `acceptance_defined`, `acceptance_reviewed`, `dependencies_valid` block
  approval; `documentation_referenced` only warns.
- There is no user/role model, so "only project leads may approve" cannot be
  enforced. Approval instead requires a typed name (recorded as the signature)
  and every approve/revoke is kept in `sprint_approvals`. Real authorisation
  waits for a user model.
- The task graph groups tasks into dependency layers: columns left-to-right at
  ≥861px, stacked top-to-bottom below it. Edges are shown as "after <task>"
  labels rather than drawn lines (drawn edges belong with task 24's visualiser).

### Acceptance criteria (task 26)

- Criteria are first-class rows (`acceptance_criteria`, `acceptance_evidence` in
  `app/acceptance/`) attached to a **planned task** (`work_item_id`) and/or a
  **Ralph run** (`ralph_run_id`); the task text's `task_id` does not exist as an
  entity yet. A run's effective criteria are its own plus its planned task's,
  which is how criteria added *during* execution (origin `AGENT`, with the
  `iteration`) sit beside the reviewed ones (RUN_AND_RALPH §15).
- Status: `DRAFT` -> `APPROVED` -> `VERIFIED` | `FAILED`, plus `WAIVED` (with a
  reason and name). Failed/verified/waived criteria can be re-opened. Editing an
  approved criterion's title withdraws the approval.
- **Sprint approval seeds criteria.** When a Sprint is approved, each planned
  task's acceptance lines become criteria (origin `PLANNING`) already
  `APPROVED`, attributed to the Sprint approver, because readiness already
  forced a person to review them. Re-running the sync is a no-op.
- **Independence without roles.** There is no user/role model, so "approved by a
  non-implementer" is a case-insensitive comparison between the approver and the
  criterion's author (a soft control that catches honest mistakes, not
  impersonation). Verification is evidence-based instead: it needs an approved
  criterion and at least one *linked* evidence item (an artifact, a passed step
  result, or a manual note), but the same person may verify what they approved.
- **Templates** (`templates.py`): the six named in the task, each with required
  `{field}` placeholders (a missing field is an error, never a literal
  placeholder) and *hints* (step types / artifact kinds that usually prove it).
- **Evidence suggestion.** After a Ralph run's verification passes (and on
  demand) passed step results and artifacts from that run's verification
  executions are matched to open criteria by keyword overlap with the title or
  by the template's hints. Matches are stored `SUGGESTED`; a person links or
  dismisses each one, and dismissed items never resurface. Nothing is auto-linked.
- **Completion gate.** A Ralph run whose verification passes now completes only
  when every *required* criterion is `VERIFIED` or `WAIVED`; otherwise it waits
  in `WAITING_FOR_HUMAN` (`awaiting_acceptance`) with evidence suggested. Draft
  (unreviewed) criteria block, which is the conservative reading of "policy
  decides"; advisory (`required = false`) criteria never block. "Complete run"
  finalises (commit included) once criteria are satisfied; "Continue" with
  guidance instead sends the agent round again. Runs with no criteria behave
  exactly as before.
- Routes are project-scoped (`/projects/<id>/acceptance/...`) rather than
  `/tasks/<id>/criteria`; the list takes `?work_item=` and `?run=` filters.

### Sprint execution queue (task 27)

- **The Task is the planned work item.** There is no separate `tasks` table:
  `planned_work_items` gained `task_state` (the §27 canonical states, default
  `DRAFT`), `released_at` and `verification_pipeline`. `ralph_runs.work_item_id`
  *is* the task FK the design calls `task_id`; it was not renamed. A "SprintBacklogLink"
  is not extended either, because state belongs to the Task, not the backlog link.
- `app/sprints/models.py` holds `TASK_STATES` and `TASK_TRANSITIONS`; `queue.set_state`
  is the only writer and rejects other moves (`InvalidTaskTransitionError`).

```text
DRAFT/READY_FOR_REVIEW -> READY -> RELEASED -> IN_PROGRESS -> COMPLETE
                                      ^            |-> BLOCKED -> RELEASED | IN_PROGRESS
                                      |            |-> FAILED  -> RELEASED
                                      +------------+ (run cancelled)
```

- **Approval makes tasks READY; revoke returns them to DRAFT.** Release is manual:
  "Release all ready tasks" (`POST .../release`) or one task at a time
  (`.../tasks/<id>/release`); releasing moves the Sprint `READY -> EXECUTING`, after
  which approval can no longer be revoked.
- **Eligibility** (`queue.eligible_task`): Sprint `EXECUTING`, task `RELEASED`, every
  dependency `COMPLETE`, first in plan order. A blocked or failed task only holds back
  its own dependants. "Project lock available" is `queue.project_busy` (any Ralph run
  in the project that is created/running/verifying/paused/waiting); task 28 replaces it
  with the real lock.
- **Promotion is manual per task** (`POST .../next-task`, "Run next task"): the initial
  release mode is manual release, and automatic linear execution is deferred. It builds
  the Ralph run from the task (title + description, acceptance lines, `work_item_id`,
  `sprint_id`), using the pipeline chosen in the form, else the task's
  `verification_pipeline`, else the project's first enabled pipeline (an error if none),
  and the primary repository.
- **Task state follows the run.** `queue.sync_from_run` runs whenever a Ralph run's
  status changes: running/paused -> `IN_PROGRESS`; blocked or waiting for sign-off ->
  `BLOCKED`; completed -> `COMPLETE`; failed/timed out -> `FAILED`; cancelled ->
  `RELEASED` (so it can be picked up again). When every non-rejected task is `COMPLETE`
  the Sprint moves to `VERIFYING`; closing it to `COMPLETE` stays a human action.
- The queue page (`/projects/<id>/sprints/<id>/queue`) shows the §29 progress counts,
  each task's state, what it is waiting on, and the linked run.
