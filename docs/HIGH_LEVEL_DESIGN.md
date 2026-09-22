# AgentFlow High Level Design

## 1. Purpose

This document defines the high level design for AgentFlow, a self hosted web application used to manage local coding agents, development projects, persistent agent sessions, autonomous Ralph style execution loops, automated testing, browser based UI verification, Git operations and development artifacts.

The system is intended to provide a single browser based control surface for software development agents running on a trusted Linux host.

The initial implementation will use Python and Flask and will be designed as a fresh implementation rather than a derivative of an existing agent interface.

The design is intended to support later decomposition into:

- component designs
- interfaces
- epics
- implementation tasks
- acceptance criteria
- automated tests

## 2. Product Goal

AgentFlow should allow a developer to:

1. register and manage local source code projects
2. start and resume coding agent sessions
3. interact with agents from a browser
4. inspect agent output and activity in real time
5. inspect and manage files and Git changes
6. execute project tests
7. define development tasks and acceptance criteria
8. execute tasks autonomously using Ralph style loops
9. perform browser based UI verification
10. capture screenshots, logs, diffs and test results
11. pause, steer, resume or terminate autonomous work
12. review the complete history of an autonomous run
13. access the system remotely from desktop or mobile browsers

The initial deployment target is a single trusted user running the system on a private Linux server accessed through LAN or Tailscale.

## 3. Design Principles

### 3.1 Local first

Projects, source code, Git repositories, agent sessions and artifacts should remain on infrastructure controlled by the user wherever practical.

### 3.2 Agent independent

The core application must not depend directly on Codex, Claude Code or any other single coding agent.

Agent specific behaviour must be isolated behind adapters.

### 3.3 Modular monolith

The initial application should be implemented as one deployable Flask application containing clearly separated modules.

Microservices should not be introduced unless operational requirements justify them.

### 3.4 Observable execution

Agent activity should never appear as an opaque background process.

The application should capture and expose:

- current state
- agent messages
- commands
- file changes
- test results
- errors
- screenshots
- Git changes
- iteration history

### 3.5 Recoverable execution

Long running tasks must be capable of surviving:

- browser disconnect
- application restart where practical
- agent crash
- failed test execution

### 3.6 Human control

Autonomous work must always support:

- pause
- stop
- resume
- steering
- inspection

### 3.7 Test driven automation

Autonomous completion should depend on objective verification wherever possible.

A coding agent claiming that a task is complete is not sufficient evidence of completion.

### 3.8 Secure by default

The application provides shell, filesystem and source control capabilities and must therefore be treated as a privileged development service.

## 4. Initial Scope

### Included

- project management
- multi repository projects
- repository discovery
- coding agent management
- persistent sessions
- live output streaming
- file browser
- Git status and diff
- terminal access
- task management
- sprint planning
- backlog capture
- Ralph execution engine
- automated command based testing
- Playwright based UI testing
- screenshots and artifacts
- run history
- basic MCP visibility and configuration
- optional Docker execution for project workloads
- mobile responsive browser interface
- host and Docker execution
- configurable Git strategies
- pipeline orchestration

### Initially excluded

- multi tenant operation
- public Internet hosting
- enterprise RBAC
- billing
- cloud development environments
- distributed workers
- Kubernetes
- collaborative simultaneous editing
- hosted SaaS operation
- arbitrary workflow automation platform

## 5. Target Environment

```text
Host OS:
Linux

Deployment:
Directly on the Linux host

Optional workload isolation:
Docker containers

Backend:
Python
Flask

Frontend:
Server rendered HTML
HTMX
small amounts of JavaScript where necessary
rich JS components where justified

Persistent application data:
SQLite initially

Project source:
Host filesystem / mounted volumes

Source control:
Git CLI

Agent execution:
Local CLI processes on the host or optional Docker containers

Browser testing:
Playwright

Remote access:
LAN / Tailscale

Primary clients:
Desktop browser
iPhone/iPad browser
```

PostgreSQL may replace SQLite later if concurrency or operational requirements require it.

## 6. High Level Architecture

```text
┌───────────────────────────────────────────────┐
│                    Browser                    │
│                                               │
│ Desktop                 Mobile                │
└───────────────────────┬───────────────────────┘
                        │
                 HTTPS / Tailscale
                        │
┌───────────────────────▼───────────────────────┐
│                  Flask Web App                │
│                                               │
│ HTML / HTMX / JS                              │
│ REST endpoints                                │
│ Event streaming                               │
└───────────────────────┬───────────────────────┘
                        │
┌───────────────────────▼───────────────────────┐
│              Application Services             │
│                                               │
│ Projects / Repositories                       │
│ Backlog / Sprint Planning                     │
│ Agents / Sessions                             │
│ Tasks / Runs / Ralph                          │
│ Pipelines / Testing / Browser testing         │
│ Git / Files / Terminal                        │
│ Artifacts / MCP                               │
└──────┬─────────┬──────────┬───────────┬────────┘
       │         │          │           │
       ▼         ▼          ▼           ▼
┌──────────┐ ┌────────┐ ┌─────────┐ ┌──────────┐
│ Agent    │ │ Git    │ │ Local   │ │ Browser  │
│ CLIs     │ │ CLI    │ │ Files   │ │ Runner   │
└──────────┘ └────────┘ └─────────┘ └──────────┘
```

## 7. Core Domain Model

```text
Workspace
   │
   └── Project
         ├── Repository
         ├── Skill
         ├── Pipeline
         ├── AcceptanceTemplate
         ├── Sprint
         │     └── Task
         │           └── Run
         │                 └── Iteration
         ├── AgentSession
         ├── TestDefinition
         └── Artifact
```

## 8. Major Components

| Component | Responsibility |
|---|---|
| Project Manager | Project and repository configuration |
| Backlog Manager | Fast capture, attachments, triage |
| Sprint Planning | Enrichment, task generation, dependency planning |
| Agent Manager | Start, resume, stop and observe agents |
| Agent Adapters | Codex, Claude, Gemini, OpenCode |
| Session Manager | Persistent session history and state |
| Task Manager | Tasks and acceptance criteria |
| Run Manager | Execution state and lifecycle |
| Ralph Engine | Autonomous iteration loop |
| Pipeline Engine | Deterministic orchestration |
| Test Manager | Unit, integration and UI tests |
| Browser Test Engine | Playwright/browser automation |
| Git Manager | Status, diff, branch, commit |
| File Manager | Project file browsing/editing |
| Terminal Manager | Persistent browser terminal |
| Artifact Manager | Screenshots, logs and reports |
| MCP Manager | MCP configuration and availability |
| Event/Streaming Layer | Live output to browser |
| Security | Execution and filesystem controls |

## 9. Agent Adapter Interface

The canonical interface is defined in `AGENT_ADAPTER.md` §3.

Initial implementations:

```text
CodexAdapter
ClaudeAdapter
GeminiAdapter
OpenCodeAdapter
FakeAgentAdapter
```

Codex should be implemented first.

## 10. Process Execution Layer

A shared execution abstraction should manage:

- process start
- process termination
- environment
- working directory
- stdout
- stderr
- exit codes
- timeout
- signals
- process groups
- PTY where required

Execution providers:

```text
HostExecutionProvider
DockerExecutionProvider
```

## 11. Sessions

Agent sessions may last much longer than a browser connection and therefore must not depend on active HTTP requests.

Suggested lifecycle:

```text
CREATED
STARTING
RUNNING
WAITING_FOR_INPUT
PAUSED
STOPPING
STOPPED
FAILED
LOST
```

## 12. Event Model

Structured events should include:

```text
ProjectCreated
SessionStarted
SessionOutputReceived
AgentMessageReceived
CommandStarted
CommandCompleted
FileChanged
GitStatusChanged
TaskStarted
IterationStarted
TestStarted
TestCompleted
ScreenshotCaptured
IterationCompleted
RunPaused
RunResumed
RunCompleted
RunFailed
```

These events should drive live UI updates and historical audit.

## 13. Browser Communication

Recommended approach:

```text
HTTP
for commands and normal page requests

HTMX
for partial page updates

Server Sent Events
for streaming events and logs

WebSocket
for terminal and other bidirectional low latency interaction
```

## 14. Ralph Execution Model

```text
LOAD TASK
    ↓
BUILD CONTEXT
    ↓
START / RESUME AGENT
    ↓
AGENT PERFORMS WORK
    ↓
COLLECT CHANGES
    ↓
RUN VERIFICATION
    ├── static checks
    ├── unit tests
    ├── integration tests
    └── UI tests
    ↓
EVALUATE RESULT
    ├── PASS → COMPLETE
    └── FAIL
          ↓
   GENERATE FEEDBACK
          ↓
     NEXT ITERATION
```

## 15. Ralph Policies

Support:

- maximum iterations
- maximum runtime
- no progress detection
- repeated failure detection
- agent crash policy
- test failure feedback
- pause/resume
- steering
- cancellation
- Git rollback where appropriate

## 16. Testing Model

Tests of AgentFlow:

- unit tests
- component tests
- API tests
- integration tests
- browser tests
- security tests

Tests AgentFlow executes against projects:

- pytest
- npm tests
- linting
- type checking
- Playwright
- screenshots
- console errors
- failed HTTP calls
- custom project commands

## 17. UI Verification

UI verification should support:

- deterministic browser tests
- browser diagnostics
- screenshots
- visual artifacts
- agent assisted visual review

Playwright is the initial browser automation technology.

## 18. Git Integration

Initial capabilities:

```text
repository detection
status
branch
log
diff
changed files
stage
unstage
commit
discard selected change
```

Git strategy is configurable at Project, Sprint and Run levels:

```text
CURRENT_BRANCH
TASK_BRANCH
WORKTREE
```

Auto commit is supported.

## 19. Persistent Terminal

Persistent terminal sessions should be backed by a server-side mechanism such as tmux.

```text
Browser
   ↓
WebSocket
   ↓
Terminal Manager
   ↓
tmux session
   ↓
shell/process
```

Browser disconnect should not terminate the session.

## 20. Security Model

Initial assumptions:

```text
single trusted user
LAN / Tailscale access
not Internet facing
```

Controls still required:

- CSRF protection
- loopback binding by default
- explicit Tailscale address binding and ACL/firewall requirements for remote access
- explicit unsafe configuration before binding to all interfaces
- filesystem boundary validation
- environment variable filtering
- command timeout
- process group termination
- secret redaction
- audit logging

Application-level login is initially out of scope. Network binding and access controls enforce the single-trusted-user deployment assumption.

## 21. Data Persistence

SQLite is suitable initially.

Artifact content is stored on the filesystem; SQLite stores artifact metadata and references.

Review PostgreSQL when:

- parallel Runs are introduced
- multiple workers are required
- multiple users are introduced
- write contention becomes material
- high event volume develops

## 22. Primary UI Navigation

Top level:

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

## 23. CloudCLI-style Operational UI

The standard AgentFlow operational experience should remain close to the useful interaction model of CloudCLI:

- project list
- session list
- chat/session view
- file browsing
- Git views
- terminal
- agent selection
- responsive mobile use

The implementation and styling must remain independently designed.

## 24. Pipeline Visualisation

Runs and pipelines should have an interactive graph view.

Every node should be inspectable.

The graph should show:

```text
Pending
Running
Passed
Failed
Warning
Paused
Retrying
Skipped
```

Selecting a step should open a right-hand inspector with:

```text
Inputs
Context
Activity
Outputs
Evidence
Artifacts
Events
Result
```

Agent steps should expose prompts, responses, file changes and relevant context.

Test steps should expose commands, stdout/stderr and structured results.

Browser steps should expose screenshots, traces, console errors and network failures.

Git steps should expose base state, diff and commit information.

## 25. Sprint Planning

Design and planning are first class Sprint phases.

The canonical Sprint lifecycle is defined in `SPRINT_PLANNING_AND_BACKLOG.md` §10. Discovery and design are planning activities rather than separate Sprint states.

Ralph should normally consume reviewed planning artifacts rather than raw ideas.

## 26. Backlog

Backlog capture must be deliberately low friction:

```text
simple text box
image/file upload
optional project/tags/priority
```

A BacklogItem may be incomplete and informal.

Planning is responsible for enrichment.

## 27. Delivery Decomposition

```text
Product
   ↓
Component
   ↓
Capability
   ↓
Epic
   ↓
Story / Task
   ↓
Acceptance Criteria
   ↓
Automated Tests
```

## 28. Initial Vertical Slices

### Slice 1

```text
Create Project
→ Add Repository
→ Start FakeAgent
→ Stream output
→ Modify fixture file
→ Show Git diff
→ Run configured test
→ Persist result
```

### Slice 2

Replace FakeAgent with Codex.

### Slice 3

Add structured Task and Run.

### Slice 4

Add Pipeline orchestration.

### Slice 5

Add Ralph retry loop.

### Slice 6

Add Playwright and UI evidence.

## 29. First Delivery Epics

```text
E01 Application foundation
E02 Project and repository management
E03 Backlog
E04 Sprint planning
E05 Process execution framework
E06 Agent abstraction
E07 Codex adapter
E08 Session management
E09 Event streaming
E10 Basic session UI
E11 Git integration
E12 Persistent terminal
E13 Test execution
E14 Task and acceptance management
E15 Pipeline engine
E16 Run management
E17 Ralph engine
E18 Browser testing
E19 Artifact management
E20 Runtime management
E21 Run controls
E22 Mobile UI
E23 Security hardening
E24 Additional agent adapters
```

## 30. Core Product Principle

AgentFlow should progressively move repeatable work out of agent prompts and into deterministic configuration.

```text
Agent reasoning:
what requires judgement

Pipeline:
what should happen repeatedly

Skill:
what the agent should know

Acceptance Criterion:
what must be true

Task:
what needs to change
```
