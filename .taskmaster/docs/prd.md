# AgentFlow Phase 1 PRD

## Goal

Deliver a useful single-user AgentFlow workspace that runs directly on a Linux host and supports Projects, Codex sessions, files, Git, a persistent terminal, and basic execution history.

The implementation must follow the canonical decisions and focused designs in `docs/`.

## Technical Constraints

- Python and Flask running directly on the Linux host
- server-rendered HTML with HTMX
- minimal JavaScript except where terminal interaction requires it
- SQLite for application records and artifact metadata
- filesystem storage for artifact content, transcripts, and large logs
- SSE for one-way live updates and WebSockets for terminal interaction
- tmux for persistent terminal sessions
- Git CLI for source control operations
- one modular application; no services, plugin framework, or speculative abstractions
- bind to loopback by default; remote access requires an explicit Tailscale address and network controls

## Required Delivery Tasks

### 1. Application and Project Foundation

Create the minimal Flask application shell, SQLite initialization, responsive navigation, and Project/repository management. A Project supports one or more validated repository paths and one default working repository.

Acceptance:

- application starts on the host and binds to loopback by default
- user can create, view, edit, and list Projects
- repository paths outside allowed roots are rejected
- data survives an application restart
- one small automated check covers Project creation and path validation

### 2. Host Execution and Event Streaming

Implement the canonical HostExecutionProvider needed by agents, commands, tests, and terminals. Add process ownership, working-directory validation, environment filtering, timeouts, process-group termination, stdout/stderr streaming, and reconnectable event delivery.

Acceptance:

- command runs in an allowed Project directory
- stdout, stderr, exit status, timeout, and cancellation are reported
- stopping execution terminates owned child processes
- reconnecting clients can continue from persisted event position
- one integration check covers success, failure, and cancellation

### 3. Agent Contract and FakeAgent Vertical Slice

Implement the canonical AgentAdapter contract and a deterministic FakeAgentAdapter before the real agent integration. Complete the first vertical slice: open Project, start FakeAgent, stream output, modify a fixture file, show the Git diff, run a configured test, and persist the result.

Acceptance:

- FakeAgent supports scripted messages, file changes, failure, retry, and success
- prompts, replies, events, and session metadata are persisted
- the vertical slice runs deterministically in CI without an external model
- one end-to-end check proves the complete slice

### 4. Codex Sessions

Implement CodexAdapter using HostExecutionProvider and the shared AgentAdapter contract. Add session creation, listing, discovery where supported, resume where supported, prompt input, live output, stop, and prompt/reply history.

Acceptance:

- Codex availability and version are detected
- a session can be started, observed, stopped, and reopened from history
- external and AgentFlow session identifiers are mapped persistently
- unsupported capabilities are reported rather than assumed
- FakeAgent remains the deterministic automated-test path

### 5. Operational Workspace

Add the Phase 1 Project workspace for file browsing/basic editing, search, Git status/diff/log/stage/unstage/commit, and a reconnectable tmux-backed terminal. Reuse the Project repository and HostExecutionProvider boundaries.

Acceptance:

- file and Git operations cannot escape allowed repository roots
- changed files link to their diff and file view
- large or binary files are handled without loading them into normal editors
- terminal sessions survive browser disconnect and reconnect
- desktop and mobile layouts expose the essential actions

### 6. Basic Runs, History, and Hardening

Add single-Task basic Runs for commands and tests, with step status, logs, prompt/reply inspection, artifact metadata, filesystem artifact content, pause/stop, restart reconciliation, and audit/security controls.

Acceptance:

- a command or test Run records status, timing, output references, and artifacts
- a missing process after restart marks its active Run blocked rather than running forever
- secrets are redacted before persisted output where practical
- unsafe all-interface binding requires explicit configuration
- Run history and basic step inspection work from desktop and mobile

## Dependencies

- Task 2 depends on Task 1.
- Task 3 depends on Tasks 1 and 2.
- Task 4 depends on Task 3.
- Task 5 depends on Tasks 1 and 2.
- Task 6 depends on Tasks 3 and 5.

## Out of Scope

- Ralph iteration and retry policy
- Sprint planning and backlog management
- pipeline authoring, compensation, and sub-pipeline execution
- auto-commit
- Docker deployment of AgentFlow
- additional real-agent adapters
- parallel Runs
- application-level login
