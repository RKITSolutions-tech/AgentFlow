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
