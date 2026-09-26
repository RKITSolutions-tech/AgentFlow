# AgentFlow Phased Delivery Plan

## 1. Purpose

AgentFlow should become useful as early as possible. The first priority is therefore to replace the day-to-day CloudCLI capabilities that are already useful. Advanced Sprint planning, visual pipelines, Ralph orchestration, compensation and automation can then be layered onto a working tool.

```text
Phase 1
CloudCLI replacement / usable agent workspace

Phase 2
AgentFlow orchestration / Sprint / pipeline / Ralph controls

Phase 3
Advanced automation and optimisation
```

## 2. Phase 1 Goal

Provide a self-hosted AgentFlow instance that can replace CloudCLI for normal use.

Primary workflow:

```text
Open AgentFlow
↓
Select Project
↓
Select/start Agent Session
↓
Interact with Codex
↓
Browse Files
↓
Inspect Git
↓
Use persistent Terminal
↓
Resume session later
```

Phase 1 should be useful before the advanced pipeline environment is complete.

## 3. Phase 1 Feature Priorities

### P1.1 Application Shell

- responsive Flask UI
- desktop and mobile
- CloudCLI-like operational information architecture
- Project/session navigation
- left navigation
- dark/light mode if straightforward

### P1.2 Projects

- create/edit Project
- one or more repositories
- Project list
- recent Projects
- repository validation
- default working repository
- Project settings

### P1.3 Codex Sessions

- Codex first
- list sessions
- start session
- resume where supported
- discover existing sessions where supported
- stop session
- prompt input
- live streaming output
- prompt/reply history

### P1.4 Files

- directory tree
- file view
- basic editing
- changed-file indicators
- search
- sensible large/binary file handling

### P1.5 Git

- status
- current branch
- diff
- log
- stage/unstage
- commit
- changed-file navigation

### P1.6 Persistent Terminal

- tmux-backed persistence
- reconnect
- resize
- Project working directory
- host execution

### P1.7 Basic Runs

- simple command/test execution
- Run history
- logs
- basic step list
- prompt/reply inspection
- artifacts where generated

### P1.8 Event Streaming

- session output
- command output
- status updates
- reconnect support

### P1.9 Persistence

SQLite for:

```text
Projects
repositories
sessions
events
basic Runs
artifact metadata
```

Artifact content is stored on the filesystem.

### P1.10 Security

- LAN/Tailscale deployment assumption
- loopback binding by default
- explicit Tailscale address and ACL/firewall configuration for remote access
- path validation
- CSRF
- secret redaction
- execution ownership

## 4. Phase 1 Deferred Items

Do not block initial use on:

```text
full visual pipeline editor
advanced compensation
full Sprint planning
Git issue sync
multiple real agents
complex Docker execution
historical pipeline replay
advanced acceptance templates
parallel Runs
```

## 5. Phase 1 Milestones

### Milestone A: Shell

```text
Flask app
Project list
Project view
responsive navigation
SQLite
```

### Milestone B: Codex Session

```text
ExecutionProvider
FakeAgentAdapter
CodexAdapter
session creation
streaming
session history
```

At this point AgentFlow becomes an early usable agent interface.

### Milestone C: CloudCLI Replacement Core

```text
Files
Git
persistent terminal
session discovery/resume
mobile usability
```

At this point AgentFlow should cover the main CloudCLI-style workflow.

### Milestone D: Basic Execution History

```text
basic Run model
simple pipeline execution
tests
artifacts
step inspection
```

This creates the bridge into Phase 2.

## 6. Phase 1 Definition of Useful

Phase 1 is practically useful when:

```text
A Project can be opened from desktop or phone.
Codex can be started or resumed.
Prompts and replies are visible and persisted.
Files can be inspected.
Git changes can be inspected.
A persistent terminal can be opened and reconnected.
Basic tests/commands can be run.
The system works over LAN/Tailscale.
```

## 7. Phase 2 Goal

Turn AgentFlow from a CloudCLI replacement into a development orchestration platform.

```text
Capture backlog
↓
Plan Sprint
↓
Generate Tasks
↓
Review acceptance
↓
Release
↓
Pipeline
↓
Ralph
↓
Verification
↓
Auto commit
↓
Visual monitoring
```

## 8. Phase 2 Features

### P2.1 Backlog

- quick text capture
- image/file upload
- Inbox
- triage
- select for Sprint
- enrich

### P2.2 Sprint Planning

- Sprint goal
- documentation references
- planning agent
- proposed task graph
- dependencies
- review
- readiness

### P2.3 Acceptance

- first-class criteria
- templates
- reviewed/approved states
- evidence links

### P2.4 Pipeline Engine

- reusable pipeline definitions
- SubPipelines
- deterministic steps
- rigging steps
- manual steps
- design-spec-driven screen mockups with human approval before implementation
- compensation
- setup/verify/teardown

### P2.5 Ralph

- multiple iterations
- failure feedback
- no-progress detection
- steering
- pause/resume
- auto commit

### P2.6 Visual Monitoring

- left-to-right desktop graph
- top-to-bottom mobile graph
- right-hand inspector
- prompts/replies
- tests
- files
- artifacts
- raw data
- iteration grouping
- compensation paths

### P2.7 Historical Replay

- Run replay
- event timeline
- step-by-step inspection
- artifact replay

### P2.8 Artifact Library

- screenshots/traces/logs/diffs
- filtering
- originating Step linkage

### P2.9 Prompt Library

- reusable prompt fragments
- standard Ralph instructions
- selectable instruction blocks

### P2.10 External Rigging

- HTTP
- SSH
- Docker
- Docker Compose
- external browser applications
- health checks
- service lifecycle

Carried in from the Phase 1 review (see `PHASE2_PLANNING.md`): context-file
resolution alongside P2.9, document review event types alongside P2.4, and
the deferred Run/terminal items listed there.

### Phase 2 build status (Task Master tasks 17-26)

| Item | Status | Notes |
| --- | --- | --- |
| P2.1 Backlog | built | inbox, triage, sprint pages, attachments (SPRINT_PLANNING §49) |
| P2.2 Sprint Planning | built | Sprint, PLANNING-role agent, readiness, approval history |
| P2.3 Acceptance | built | criteria, templates, evidence suggestion, completion gate |
| P2.4 Pipeline Engine | built | definitions, versions, composition, engine (PIPELINE_ENGINE §22) |
| P2.5 Ralph | built | iterations, steering, no-progress, auto commit (RUN_AND_RALPH §22) |
| P2.6 Visual Monitoring | partly | execution graph + inspector; no replay, no collapsing sub-pipelines |
| P2.7 Historical Replay | not started | events are recorded; no replay UI |
| P2.8 Artifact Library | built | index, search, compare; images compared byte-wise only |
| P2.9 Prompt Library | not started | AGENT steps use a small built-in template table |
| P2.10 External Rigging | partly | process/HTTP/health/wait steps; SSH/Docker run as plain commands |

Not yet joined up: nothing turns an approved Sprint's tasks into Ralph runs
(Sprint execution queue, SPRINT_PLANNING §28) and there is no Project lock, so
the "Definition of Useful" below is not met end to end. Front-end JavaScript
and the viewport tests have not been exercised in a real browser.

## 9. Phase 2 Definition of Useful

Phase 2 is complete when a reviewed Sprint can autonomously execute released Tasks through a visible, inspectable pipeline with evidence and controlled Ralph retries.

## 10. Phase 3 Direction

Potential Phase 3 capabilities:

```text
visual pipeline authoring
parallel Runs
worktree-based concurrency
additional agent adapters
Git issue synchronisation
semantic backlog search
duplicate detection
automatic pipeline suggestions
promote repeated actions to pipeline
promote discoveries to Skills/ADRs
richer visual comparison
remote workers
PostgreSQL if required
```

## 11. Priority Rule

When choosing between a CloudCLI-equivalent feature needed for daily use and an advanced orchestration feature, Phase 1 prioritises the CloudCLI-equivalent feature unless the orchestration feature is required infrastructure for the replacement capability.

This prevents AgentFlow becoming a long-running platform build before it can replace the existing tool.
