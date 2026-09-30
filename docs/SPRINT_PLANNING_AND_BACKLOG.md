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
- `AGENTFLOW_PLANNING_AGENT` selects `codex` (default), `claude`, `local`, or
  `fake`. `fake` is the deterministic FakePlanningAgent of §43 (one task per
  backlog item) for CI; `claude` runs a `ClaudeAdapter` session the same way
  `codex` runs a `CodexAdapter` one. `local` drives a Settings model_catalog
  `local` entry (§17/§18 of docs/AGENT_ADAPTER.md): `AGENTFLOW_PLANNING_MODEL`
  names the entry's `model_id`, and since that catalog row carries only a
  base_url/api_key — not which CLI wire protocol it speaks —
  `AGENTFLOW_PLANNING_LOCAL_ADAPTER` (`codex`, default, or `claude`) says
  that separately. A missing/disabled/unnamed entry raises
  `ModelCatalogConfigError` (`app/settings/models.py`) rather than starting
  a session.
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
- **Promotion** is manual per task (`POST .../next-task`, "Run next task"), or automatic:
  the queue page's "Automatic mode" switch (`sprints.auto_run`, `POST .../auto-run`) makes
  each finished Ralph run start the next eligible task (`RalphManager._advance_sprint`, after
  the finished run's lock is released). It carries on after completed, failed, timed-out and
  blocked runs (independent tasks continue, §28) but not after a cancel, which is a person
  saying stop; it stops when nothing is eligible or the project is busy, and turning it on
  starts the next task immediately. It builds
  the Ralph run from the task (title + description, acceptance lines, `work_item_id`,
  `sprint_id`), using the pipeline chosen in the form, else the task's
  `verification_pipeline`, else the project's first enabled pipeline (an error if none),
  and the primary repository.
- **Restart.** `RalphManager.reconcile()` marks an in-flight run BLOCKED and syncs its task
  to `BLOCKED`; then `resume_automatic_sprints()` (app start, not under `TESTING`, after
  orphaned locks are released) runs `queue.advance` for every `EXECUTING` sprint with
  `auto_run = 1`, so independent tasks carry on. The blocked task waits for a person
  (assumption: it is not retried automatically, since its working tree may be half-edited).
  With nothing eligible the sprint stays idle and the queue page says "Automatic mode is
  waiting: <reason>" (`queue.auto_waiting`).
- **Task state follows the run.** `queue.sync_from_run` runs whenever a Ralph run's
  status changes: running/paused -> `IN_PROGRESS`; blocked or waiting for sign-off ->
  `BLOCKED`; completed -> `COMPLETE`; failed/timed out -> `FAILED`; cancelled ->
  `RELEASED` (so it can be picked up again). When every non-rejected task is `COMPLETE`
  the Sprint moves to `VERIFYING`; closing it to `COMPLETE` stays a human action.
- The queue page (`/projects/<id>/sprints/<id>/queue`) shows the §29 progress counts,
  each task's state, what it is waiting on, and the linked run.

## 50. Research Action

Decided in discussion. "Research this item" on a Backlog item or planned task starts a `RESEARCH` agent
session (AGENT_ADAPTER §23; repository first, then the shared knowledge base, then the web). The report attaches to the item as suggested context with the same provenance rules as
`SUGGESTED` planned tasks (§9): nothing is accepted automatically, a person accepts or dismisses each finding, and
accepted findings may seed acceptance criteria or task descriptions. Sources are always listed.

**Backlog items (implemented, task 44).** `app/backlog/research.py`; planned tasks are not wired up here.

- **Trigger.** `POST /projects/<id>/backlog/items/<item_id>/research` (`app/backlog/views.py.research_item`) builds the
  question from the item's title and text and drives `ResearchAgent` directly -- an ad-hoc call, the same way
  `app/sprints/planning_agent.PlanningAgent` is driven directly from sprint views, not a one-step pipeline execution.
  It reuses the project's configured agent (`PLANNING_AGENT`, via `app.pipelines.manager.default_agent_factory` -- the
  same setting Sprint planning and RESEARCH pipeline steps already use; there is no separate `RESEARCH_AGENT` knob) and
  the same repository resolution as `POST /projects/<id>/sprints/<id>/plan` (primary repository, else the project's
  first repository, else the first allowed root). Limits are `time_limit_seconds=300`, `cost_limit_usd=5` (tighter
  than a pipeline RESEARCH step's $10 default, since a backlog item's question is narrower in scope).
- **Blocking, by design.** Every adapter today resolves synchronously inside `start()` (AGENT_ADAPTER §23 "Limits"), so
  the request blocks for the research pass, exactly like Sprint planning's `POST /plan` already does. The trigger is a
  `data-ajax-reload` button (`app/static/app.js`): disabled while in flight, page reload on completion -- the same
  control Ralph's research-on-failure report already uses (`app/templates/ralph/detail.html`). The item's status is
  never touched: research can be requested from any Backlog status.
- **Storage.** A `backlog_research_links` row (`app/backlog/models.BacklogResearchLink`) links the item to the
  `research_sessions` row (task 45) and the Artifact Library entry indexing the report JSON
  (`kind='research_report'`, tagged `backlog-item-<id>`, metadata including `backlog_item_id` -- the same shape and
  helper pattern `PipelineEngine._register_research_artifact` uses for RESEARCH pipeline steps, task 43). A link table
  rather than a column on `backlog_items`: the item's own status/lifecycle is unaffected, and an item may accumulate
  more than one research run, so an append-only row per run (like `backlog_triage_history`) fits better than a single
  mutable field.
- **Accept / dismiss.** Each link starts `PENDING`; `POST .../research/<link_id>/accept` appends the report summary to
  the item's text and logs a `backlog_triage_history` note (status unchanged), then marks the link `ACCEPTED`.
  `POST .../research/<link_id>/dismiss` marks it `DISMISSED` without changing the item. Both mirror Ralph's
  `use_research`/`dismiss_research` (`app/ralph/views.py`) for the same report shape. Only the most recent `PENDING`
  link renders as an actionable report (summary, findings with an "unverified" badge on anything with no source,
  sources with file path/line range); reviewed links collapse into a compact history table linking to their Artifact.

## 51. Test Definition and QA

Decided in discussion (not yet implemented). Two distinct scopes, both built on the existing `Criterion` model
(§ "Acceptance criteria (task 26)") rather than a new test-case model, so evidence, verification and the Ralph
completion gate stay one workflow. Note: "Design" and "Tests" here are UI sections, unrelated to the planning-time
`PlanningQuestion`/design-Task concept in §32 or the meta test list for AgentFlow's own planning workflow in §43.

**Backlog item: Design and Tests sections.** A "Design this item" action mirrors §50's Research action -- same
ad-hoc, blocking, `PLANNING_AGENT`-driven pattern (`app/backlog/design.py`, alongside `app/backlog/research.py`) --
but its output seeds draft `Criterion` rows (`origin="AGENT"`, `work_item_id=<item>`, `status="DRAFT"`) rather than
appended text. These render in a new Tests section on the item page (`item.html`, alongside Attachments/Research/
History), reusing the existing approve/verify/evidence workflow and `acceptance.views` routes unchanged.

**Sprint QA screen.** Sprint-scoped regression and code-standards checks (login, nav-click smoke, lint, etc.) are
not owned by one work item, so `acceptance_criteria` gains a nullable `sprint_id` (a real FK -- unlike
`backlog_items.sprint_id`, the Sprints table already exists by the time this lands, so there is no ordering problem).
`app/acceptance/templates.py` gains a `default_for_sprint: true` flag on entries meant to pre-populate every sprint's
QA checklist. The Sprint QA screen lists these as checkboxes at planning/approval time; unchecking one does not
omit the row, it creates it `WAIVED` -- consistent with WAIVED's existing meaning ("deliberately not gating on
this") and keeping an audit trail of what was excluded and by whom, versus a checklist item that silently never
existed.

**Pytest node ID, not fuzzy matching.** `service.py`'s evidence suggestion (`_tokens`/`_STOPWORDS`) matches criteria
to test-result artifacts by keyword overlap -- adequate for agent-authored, freely-named tests, but not precise
enough for a fixed regression checklist. Sprint QA criteria instead carry an explicit `pytest_node_id` column
(e.g. `tests/test_workspace_integration.py::test_login`), set when the checklist entry is curated/templated, pointing
at tests already in `tests/` where they exist. A QA run step executes `pytest <node_id>` through the normal
`ExecutionProvider`/Run machinery (no agent involved); the resulting junit XML is picked up as the existing `.junit`
"report" artifact kind (`app/artifacts/collector.py`) and filed as `TEST_RESULT` evidence directly against the
matching criterion by node ID -- exact, not suggested.

**Where agents fit.** Two different roles, not one:

- *Ralph's own loop* (§ RUN_AND_RALPH) is unchanged -- the agent develops a feature and its own scoped tests as part
  of the same iteration; the verification pipeline runs those.
- *Sprint QA execution* uses no agent at all for checks expressible as pytest -- the framework runs them, as above.
  For QA checks that need judgment rather than a fixed assertion (e.g. "does this still look right"), each check
  gets its own fresh, narrowly-scoped agent session -- the same one-shot, bounded-cost/time pattern as
  `ResearchAgent`/`PlanningAgent` (§50), one session per check, no shared context across checks -- specifically to
  avoid one test's findings or scratch reasoning leaking into another's judgment ("context bleed").

**Open questions.** Not yet decided: what triggers a Sprint QA run (manual button on the QA screen vs. part of
sprint approval/release, §27); whether agent-authored pytest files from a backlog item's Design action are committed
to the repo as real test files or regenerated per run; and how a QA run's overall pass/fail rolls up into Sprint
readiness (§25) alongside per-item acceptance criteria.
