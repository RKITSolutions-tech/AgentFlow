# AgentFlow Domain and Orchestration Design

## 1. Purpose

This document defines the core domain model and execution/orchestration model for AgentFlow.

It formalises the concepts that drive:

- database design
- component boundaries
- APIs
- task execution
- Ralph loops
- acceptance testing
- Git isolation
- pipeline orchestration
- agent integrations
- UI structure
- delivery backlog

## 2. Core Design Principle

AgentFlow separates four concerns:

```text
Reasoning
Execution
Verification
Orchestration
```

These must not be collapsed into one agent prompt.

Preferred model:

```text
Task
  ↓
Pipeline orchestration
  ↓
Agent reasoning where required
  ↓
Deterministic execution
  ↓
Deterministic verification
  ↓
Acceptance evaluation
```

Whenever a repeatable operation can be deterministic, AgentFlow should perform it directly rather than ask an LLM to improvise it.

## 3. Primary Domain Structure

```text
Workspace
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

## 4. Project

A Project represents one logical software product or engineering context.

A Project may contain:

- one or more repositories
- runtime services
- external dependencies
- task sources
- agent configuration
- pipelines
- acceptance templates
- skills

Suggested fields:

```text
id
workspace_id
name
slug
description
status
default_agent
default_execution_provider
default_git_strategy
default_setup_pipeline_id
default_verification_pipeline_id
default_teardown_pipeline_id
project_root
created_at
updated_at
```

## 5. Repository

Repository is a first class entity.

Suggested fields:

```text
id
project_id
name
path
remote_url
default_branch
role
is_primary
created_at
updated_at
```

Roles may include:

```text
PRIMARY
FRONTEND
BACKEND
INFRASTRUCTURE
DOCUMENTATION
TESTS
OTHER
```

A Run must know which repositories it may read or modify.

## 6. Sprint

Sprint is a bounded delivery context, not necessarily formal Scrum.

It may override Project defaults.

Example configuration:

```yaml
sprint:
  agent: codex
  git_strategy: worktree
  max_iterations: 8

  pipelines:
    setup: web-stack
    verify: full-ui-validation
    teardown: standard-cleanup

  acceptance_templates:
    - standard-web-change
    - accessibility-basic
```

Resolution order:

```text
Run override
↓
Task
↓
Sprint
↓
Project
↓
System default
```

## 7. Task

Task is a discrete unit of engineering work.

Possible sources:

```text
INTERNAL
MARKDOWN
GIT_ISSUE
EXTERNAL
```

All sources normalise into the same internal Task model.

Suggested fields:

```text
id
project_id
sprint_id
source_type
source_reference
title
description
context
status
priority
created_at
updated_at
```

The canonical Task lifecycle is defined in `SPRINT_PLANNING_AND_BACKLOG.md` §27.

## 8. Task Source Adapter

```python
class TaskSourceAdapter:
    def list_tasks(self): ...
    def get_task(self, reference): ...
    def update_task(self, task): ...
    def sync(self): ...
```

Initial adapters:

```text
InternalTaskSource
MarkdownTaskSource
GitIssueTaskSource
```

## 9. Acceptance Criteria

AcceptanceCriteria are first class entities rather than free text.

Suggested fields:

```text
id
task_id
template_id
name
description
verification_type
verification_reference
required
review_status
created_at
updated_at
```

Verification types:

```text
TEST
COMMAND
UI_TEST
VISUAL
AGENT_REVIEW
MANUAL
PIPELINE_RESULT
```

Review states:

```text
DRAFT
REVIEWED
APPROVED
REJECTED
```

Criteria may be added during execution, but provenance and review state must be explicit.

## 10. Acceptance Templates

Examples:

```text
standard-web-change
standard-api-change
standard-form-change
basic-accessibility
database-migration
responsive-ui
```

Templates may apply at Project, Sprint or Task level.

## 11. Skills

Skill represents reusable knowledge or instructions for agents.

Skill is not executable orchestration.

Examples:

```text
UI design rules
repository conventions
testing conventions
Flask architecture rules
accessibility requirements
```

Distinction:

```text
Skill
"What the agent should know"

Pipeline
"What the platform should do"

Acceptance Criterion
"What must be true"

Task
"What needs to change"
```

## 12. Run

Run represents one attempt to execute a Task.

A Run captures resolved configuration at start so execution history remains reproducible.

Example:

```yaml
run:
  agent: codex
  execution_provider: docker
  git_strategy: worktree
  max_iterations: 10

  setup_pipeline: standard-web
  verification_pipeline: ui-full
  teardown_pipeline: clean-web

  repositories:
    frontend: write
    backend: read

  acceptance_policy:
    require_review: true

  auto_commit: true
```

The canonical Run and Iteration lifecycles are defined in `RUN_AND_RALPH.md` §§3–4.

## 13. Iteration

Iteration is one reasoning and verification cycle within a Run.

Suggested fields:

```text
id
run_id
number
status
started_at
completed_at
agent_session_id
input_context_reference
output_reference
result
failure_category
failure_summary
```

Possible results:

```text
PASS
FAIL
NO_PROGRESS
BLOCKED
AGENT_ERROR
INFRASTRUCTURE_ERROR
CANCELLED
```

## 14. Agent Session

AgentSession is a persistent conversation or CLI session.

Sessions may be:

- independent
- attached to a Task
- attached to a Run
- attached to an Iteration

A Ralph Run may reuse the same session between iterations where supported.

## 15. Execution Provider

All execution should occur through the canonical abstraction defined in `EXECUTION_PROVIDER.md` §2. Initial providers are HostExecutionProvider and DockerExecutionProvider; future providers may include SSH or remote workers.

## 16. Host Execution

Host execution is simple and fast but has weaker isolation.

## 17. Docker Execution

Docker execution may support:

```text
existing long lived container
ephemeral container
Docker Compose service
container per Run
```

Initial support should include:

```text
existing container
ephemeral Run container
```

## 18. Pipeline

Pipeline is a deterministic ordered sequence of executable steps.

Example:

```text
1. start database
2. start application
3. wait for healthcheck
4. seed fixtures
5. run pytest
6. run Playwright
7. capture browser trace
8. capture screenshot
9. stop application
```

Pipeline types:

```text
SETUP
BUILD
TEST
VERIFY
TEARDOWN
CUSTOM
```

## 19. Pipeline Step

Example:

```yaml
steps:

  - id: start-app
    type: command
    execution: host
    command: ./scripts/start-dev.sh

  - id: health
    type: healthcheck
    url: http://localhost:5000/health
    timeout: 30

  - id: test
    type: command
    command: pytest

  - id: browser
    type: playwright
    suite: regression
```

Initial step types:

```text
COMMAND
PROCESS_START
PROCESS_STOP
HEALTHCHECK
WAIT
TEST
PLAYWRIGHT
SCREENSHOT
HTTP_REQUEST
FILE_CHECK
GIT_CHECK
ARTIFACT_CAPTURE
AGENT
```

Later:

```text
CONDITION
PARALLEL
LOOP
MANUAL_APPROVAL
MANUAL_REVIEW
SCRIPT
```

## 20. Agent Pipeline Step

Agent steps invoke reasoning and must be visually distinguished from deterministic steps.

## 21. Deterministic First Principle

Do not ask Codex to run pytest if a TEST pipeline step can run it.

Do not ask an agent to check whether a service started if a healthcheck can do it.

Agents should reason about results, not rediscover repeatable procedures.

## 22. Resources

Resource represents something a pipeline may use.

Examples:

```text
local web server
Docker service
external API
database
browser target
SSH host
```

Example:

```yaml
resources:

  local-app:
    type: process
    start: ./run.sh
    stop: ./stop.sh
    healthcheck: http://localhost:5000/health

  test-postgres:
    type: docker
    service: postgres
```

## 23. Pipeline Failure Policy

Step behaviours:

```text
STOP
CONTINUE
RETRY
MARK_WARNING
```

Teardown must run in finally-style cleanup even when earlier steps fail.

## 24. Pipeline Execution Records

```text
PipelineExecution
   └── PipelineStepExecution
```

Step states:

```text
PENDING
RUNNING
PASSED
FAILED
SKIPPED
CANCELLED
TIMED_OUT
WARNING
```

## 25. Git Strategy

Supported strategies:

```text
CURRENT_BRANCH
TASK_BRANCH
WORKTREE
```

Strategy may be configured per Project, Sprint, Run and repository.

Multi-repository example:

```yaml
repositories:
  frontend:
    strategy: worktree
  backend:
    strategy: task_branch
  docs:
    strategy: current_branch
```

## 26. Auto Commit

Runs may commit automatically.

Recommended initial policy:

```text
checkpoint commit after successful verification
final commit when Task completes
```

Auto-commit creates one commit in each writable repository with changes. The Run checkpoint records the resulting commit set; commits across repositories are linked by that checkpoint but are not atomic.

## 27. Persistent Terminal

Persistent terminals should use tmux or similar.

```text
Browser
↓
terminal WebSocket
↓
Terminal Manager
↓
tmux session
↓
shell/process
```

## 28. Test Definitions

TestDefinition defines how a test runs.

Pipeline determines when it runs.

Categories:

```text
STATIC
UNIT
INTEGRATION
SYSTEM
UI
SECURITY
CUSTOM
```

## 29. Browser Verification

Initial implementation: Playwright.

Capabilities:

- navigation
- interaction
- DOM assertions
- screenshots
- console capture
- network failure capture
- responsive viewport testing
- browser traces

## 30. Acceptance Evaluation

Each criterion receives:

```text
PASS
FAIL
UNKNOWN
NOT_RUN
WAIVED
```

Each result should link to evidence such as:

- test results
- screenshots
- pipeline results
- Git diff
- browser trace
- agent assessment
- manual confirmation

## 31. Ralph Engine

Ralph coordinates services rather than implementing their internals.

```text
resolve configuration
↓
prepare Git environment
↓
execute setup pipeline
↓
iteration:
    build agent context
    invoke agent
    collect changes
    execute verification pipeline
    evaluate acceptance
    retry if necessary
↓
commit
↓
execute teardown
```

## 32. Agent Context

Possible context:

```text
Task
Acceptance criteria
Relevant skills
Sprint instructions
Repository paths
Previous iteration result
Failed tests
Browser failures
Git diff
Steering messages
Constraints
```

Do not blindly resend the complete historical log each time.

## 33. No Progress Detection

Signals may include:

```text
same failing test
same error signature
no Git changes
same Git diff
agent reports completion but acceptance still fails
```

Repeated no-progress should pause rather than burn tokens indefinitely.

## 34. Events

Example events:

```text
ProjectCreated
RepositoryAdded
SprintStarted
TaskImported
TaskApproved
RunCreated
RunStarted
IterationStarted
AgentStarted
AgentOutput
AgentCompleted
PipelineStarted
PipelineStepStarted
PipelineStepCompleted
TestCompleted
BrowserFailureDetected
AcceptanceEvaluated
CommitCreated
RunPaused
RunResumed
RunCompleted
```

## 35. Artifacts

Examples:

```text
screenshots
browser traces
test reports
agent transcripts
logs
Git patches
coverage reports
generated documents
```

Artifacts should be stored outside SQLite, with metadata in the database.

## 36. Configuration Resolution

```text
System
↓
Workspace
↓
Project
↓
Sprint
↓
Task
↓
Run
```

The resolved Run configuration is persisted at execution start.

## 37. Persistence

Likely tables:

```text
workspaces
projects
repositories
sprints
tasks
task_sources
acceptance_templates
acceptance_criteria
skills
pipelines
pipeline_steps
pipeline_executions
pipeline_step_executions
runs
iterations
agent_sessions
test_definitions
test_runs
resources
terminals
artifacts
events
steering_instructions
```

## 38. Linear Execution Constraint

Initial execution is linear.

Only one active Ralph Run should modify a Project execution context at once.

Initial lock scope:

```text
PROJECT
```

Future:

```text
REPOSITORY
RESOURCE
```

## 39. Component Boundaries

```text
app/
├── workspaces/
├── projects/
├── repositories/
├── sprints/
├── tasks/
├── acceptance/
├── skills/
├── resources/
├── agents/
├── sessions/
├── execution/
├── pipelines/
├── testing/
├── browser/
├── runtime/
├── terminals/
├── git/
├── runs/
├── ralph/
├── artifacts/
├── events/
├── config/
└── security/
```

## 40. Execution Philosophy

AgentFlow should progressively convert repeated discovery into stable automation or reusable knowledge.

```text
Repeated operational action
→ pipeline step

Repeated repository knowledge
→ Skill

Important architectural decision
→ documentation / ADR
```

This reduces repeated agent reasoning and improves Ralph reliability.
