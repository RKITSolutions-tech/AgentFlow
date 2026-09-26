# AgentFlow Pipeline Engine Design

## 1. Purpose

The Pipeline Engine provides deterministic orchestration around agent-driven development. It is responsible for repeatable execution steps such as preparing repositories, starting and stopping services, invoking agents, running tests, executing browser verification, calling external APIs, interacting with infrastructure, collecting artifacts, and applying compensation behaviour when a step fails.

The core principle is:

```text
Reasoning belongs to agents.
Repeatable orchestration belongs to pipelines.
```

The same development task should be able to run repeatedly without requiring an agent to rediscover how to start services, run tests, prepare data, or collect evidence.

## 2. Pipeline Model

A Pipeline is an ordered composition of PipelineElements.

```text
Pipeline
 ├── Step
 ├── Step
 ├── SubPipeline
 ├── ManualStep
 ├── AgentStep
 └── Step
```

Initial execution is predominantly linear. The model must not prevent future branching, parallel execution, graphical editing, reusable components, or conditional composition.

Suggested Pipeline fields:

```text
id
project_id
name
description
type
version
status
enabled
created_at
updated_at
```

Pipeline types:

```text
SETUP
DEVELOPMENT
VERIFICATION
TEARDOWN
RIGGING
CUSTOM
```

## 3. Pipeline Definition Storage

Initial pipeline definitions are stored in SQLite. Structured definition data may use relational tables plus JSON configuration fields where appropriate. Future import/export to YAML or repository-hosted definitions should remain possible, but SQLite is the initial source of truth.

## 4. Pipeline Composition

The model supports references to reusable sub-pipelines from the beginning; executing them is a Phase 2 capability.

```text
Web Feature Pipeline
 ├── Standard Git Preparation
 ├── Standard Web Setup
 │    ├── Start Database
 │    ├── Start Application
 │    └── Health Check
 ├── Ralph Development
 ├── Full Web Verification
 │    ├── Unit Tests
 │    ├── Playwright
 │    └── Screenshot Capture
 └── Standard Cleanup
```

A SubPipeline references another pipeline definition rather than copying its contents.

## 5. Pipeline Element

All visual and executable nodes derive from a common PipelineElement model.

```text
id
pipeline_id
element_type
name
description
enabled
sequence
configuration
compensation_policy
created_at
updated_at
```

### Development steps

```text
AGENT
TEST
PLAYWRIGHT
GIT
ACCEPTANCE
```

### Rigging / infrastructure steps

```text
PROCESS_START
PROCESS_STOP
DOCKER
DOCKER_COMPOSE
SSH
HTTP
HEALTHCHECK
DATABASE
EXTERNAL_BROWSER
WAIT
FILE_OPERATION
```

Rigging steps must be visually distinct from development work.

### Human steps

```text
MANUAL_APPROVAL
MANUAL_INPUT
MANUAL_REVIEW
```

## 6. Initial Step Types

```text
COMMAND
AGENT
TEST
PLAYWRIGHT
SCREENSHOT
GIT
ACCEPTANCE
PROCESS_START
PROCESS_STOP
HEALTHCHECK
WAIT
HTTP_REQUEST
SSH_COMMAND
DOCKER_COMMAND
DOCKER_COMPOSE
FILE_CHECK
FILE_OPERATION
ARTIFACT_CAPTURE
MANUAL_APPROVAL
MANUAL_INPUT
MANUAL_REVIEW
SUB_PIPELINE
```

## 7. Agent Steps and Prompt Library

An AgentStep invokes an AgentAdapter. Configuration may include:

```text
agent
role
prompt_template
prompt_fragments
skills
standard_instructions
session_strategy
timeout
```

Reusable prompt components may include:

```text
standard-ralph-instructions
implement-task
analyse-test-failure
review-screenshot
verify-acceptance
```

A prompt may be composed from system instructions, role instructions, Project instructions, Sprint instructions, Task context, Skills, acceptance criteria, previous iteration evidence, steering, and the step-specific template.

The effective prompt must be persisted for every execution.

Standard Ralph instructions should also be reusable configuration rather than hard-coded strings. Future UI should allow individual instruction blocks to be enabled or disabled.

## 8. Step Enablement

Every PipelineElement supports:

```text
ENABLED
DISABLED
SKIPPED
```

`DISABLED` means the node remains in the definition but is not eligible for execution. `SKIPPED` means it was eligible but explicitly skipped for a particular execution.

The model must support future visual enable/disable/skip controls.

## 9. Pipeline Execution

A PipelineExecution represents one runtime execution of a Pipeline revision.

```text
id
pipeline_id
pipeline_version
run_id
iteration_id
status
started_at
completed_at
resolved_configuration
```

Each executed node produces a StepExecution:

```text
id
pipeline_execution_id
pipeline_element_id
status
started_at
completed_at
attempt
input_reference
output_reference
raw_data_reference
result_summary
error_summary
```

Every StepExecution exposes a common envelope:

```text
StepExecution
 ├── Inputs
 ├── Context
 ├── Activity
 ├── Outputs
 ├── Evidence
 ├── Artifacts
 ├── Events
 └── Result
```

This is the contract used by the visual Step Inspector.

## 10. Inputs, Outputs and Artifacts

Inputs may include command, environment, prompt, previous step outputs, files, Task context, acceptance criteria, resource references, and pipeline variables.

Outputs may include stdout, stderr, agent reply, generated files, test results, Git changes, HTTP responses, structured values, process references, and status values.

Artifacts may include screenshots, Playwright traces, videos, logs, diffs, reports, coverage files, and generated documents.

Artifacts are attached to the originating StepExecution and indexed in the global Artifact Library.

## 11. Prompt and Reply Persistence

All prompts and replies must be stored, including:

- planning prompts
- implementation prompts
- retry prompts
- verification prompts
- steering messages
- agent replies
- tool responses where available
- generated effective prompts

Prompts must be viewable from the Step Inspector and historical replay.

Prompts, replies and tool responses are transcript artifacts stored on the filesystem, with metadata and references in SQLite.

## 12. Redaction

Secrets should be redacted before persistent storage wherever practical. Redaction applies to prompts, replies, stdout, stderr, environment dumps, HTTP headers, URLs containing credentials, and suitable generated artifacts.

The execution record should indicate that redaction occurred.

## 13. Variables and Resources

Pipeline variables may come from:

```text
system
Project
Sprint
Task
Run
step outputs
resource properties
```

Example:

```text
${project.path}
${task.id}
${run.id}
${steps.start_app.process_id}
```

Pipeline steps should reference configured Resources rather than duplicate connection details. Examples include Docker services, external web apps, databases, APIs, SSH hosts, browser targets, and local services.

## 14. Compensation Model

Each step may define what happens when it fails.

Supported compensation actions:

```text
STOP
STOP_AND_MESSAGE
CONTINUE
RUN_STEP
LOOP
START_PIPELINE
```

Future additions may include:

```text
RETRY_WITH_BACKOFF
ROLLBACK
ESCALATE
```

### STOP

Terminate the current pipeline and mark it failed.

### STOP_AND_MESSAGE

Terminate execution and create a user-facing notification or intervention request.

### CONTINUE

Record the failure and continue with the next eligible step. The final execution may complete with warnings.

### RUN_STEP

Invoke a defined compensation step.

```text
Playwright fails
 ↓
Capture Browser Trace
 ↓
Capture Screenshot
 ↓
Stop
```

### LOOP

Return to a specified step or sub-pipeline. Safeguards must include maximum loops, timeout, and no-progress detection.

Pipeline-level loops are confined to a step or sub-pipeline, such as retrying a health check. Ralph exclusively owns retries and no-progress detection across Iterations.

### START_PIPELINE

Invoke another pipeline. Parent/child PipelineExecution relationships are recorded.

## 15. Retry and Cleanup

Retry may be represented as a standard compensation pattern:

```yaml
attempts: 3
delay_seconds: 5
backoff: fixed
```

Teardown must execute with finally-style semantics where configured. Resources started by a pipeline should be registered for cleanup so that failures do not leave services running unintentionally.

## 16. Manual Steps

A ManualApprovalStep pauses execution until approved, rejected or cancelled. The user must be able to inspect prior evidence before deciding.

A ManualInputStep requests structured or free-text input. The supplied value becomes an output available to later steps.

### Design mockup review pattern

Phase 2 pipelines may place a human review gate between design and implementation:

```text
Design Specification
→ Generate Screen Mockup(s)
→ Human Review
   ├── Approve → Full Development
   ├── Request Changes → Generate Screen Mockup(s)
   └── Cancel → Stop
```

An AgentStep generates one or more screen mockups from the design specification as image or static HTML artifacts. A ManualReviewStep presents the specification and mockups together. Full implementation must not begin until the review is approved; requested changes return only to mockup generation.

## 17. Historical Determinism and Versioning

PipelineExecution stores the pipeline revision, resolved configuration, effective step configuration, effective prompts, and execution results. Later edits must not alter historical Runs.

Editing a pipeline creates a new version/revision. Existing Runs remain linked to the revision they executed.

## 18. Events

Important events include:

```text
PipelineStarted
PipelineCompleted
PipelineFailed
StepStarted
StepCompleted
StepFailed
StepSkipped
StepDisabled
CompensationStarted
CompensationCompleted
ManualApprovalRequested
ManualApprovalReceived
SubPipelineStarted
SubPipelineCompleted
```

## 19. Future Visual Editing

Phase 1 is view-focused, but the data model must support future operations:

```text
add node
remove node
enable/disable
skip
reorder
insert sub-pipeline
select prompt library item
select Ralph instruction block
configure compensation
```

## 20. Phase 1 Scope

Phase 1 provides only the pipeline capabilities necessary to support the CloudCLI replacement and basic execution history:

- command execution
- agent execution
- tests
- basic setup/cleanup
- step status/history
- prompt/reply capture
- basic artifacts

Advanced pipeline authoring must not delay the usable AgentFlow workspace.

## 21. Phase 2 Scope

Phase 2 expands the Pipeline Engine into a core orchestration environment:

- richer graph execution
- compensation configuration
- reusable pipeline composition
- prompt library controls
- Ralph control nodes
- manual approval/input
- design mockup review gates
- rigging steps
- external systems
- historical replay
- richer artifacts
- future visual authoring foundations

## 22. Implementation Decisions (Phase 2, autonomous assumptions)

Made without the owner in the loop while building tasks 21-22; treat as
recommendations to confirm.

### Definition format and storage (task 21)

- Definitions are JSON-shaped dicts (`app/pipelines/schema.py` documents the
  shape), not YAML: PyYAML is not a dependency and §3 makes SQLite the source of
  truth. YAML import/export can be added later without a schema change.
- Storage follows §3/§5/§17: `pipelines` (identity; `project_id` NULL = shared
  built-in), `pipeline_versions` (append-only; an edit is a new version, history
  is never rewritten) and `pipeline_elements` (one row per element per version,
  with `configuration`, `compensation_policy` and `depends_on` as JSON).
- A project pipeline shadows a shared built-in of the same name. Built-ins live
  in `app/pipelines/definitions/*.json` and are installed idempotently at app
  start (`seed_builtins`): `standard-git-preparation`, `standard-cleanup`
  (reusable sub-pipelines) and the profiles `web-feature`, `backend-only`,
  `documentation-only`. Existing built-ins are never overwritten.
- Element `phase` (`SETUP`, `MAIN`, `TEARDOWN`) models rigging: SETUP runs
  first, TEARDOWN always runs last ("finally", §15). This replaces the
  `rigging.setup/teardown` blocks in the task text.
- `depends_on` is optional. When omitted, an element implicitly follows the
  previous element **of the same phase**, so a plain list reads as linear (§2).
  Explicit `depends_on` (a list of element names, possibly empty) is stored as
  given, and the model does not preclude future parallelism.
- Approval elements use the design's `MANUAL_APPROVAL/INPUT/REVIEW` types, and
  there is no `required_approvers` list: AgentFlow has no user model yet.
- Compensation actions are the §14 set (`STOP`, `STOP_AND_MESSAGE`, `CONTINUE`,
  `RUN_STEP`, `LOOP`, `START_PIPELINE`), not the task's retry/skip/abort. Retry
  is the orthogonal `attempts`/`delay_seconds`/`backoff` (`fixed|exponential`)
  policy of §15 and applies before the action. `LOOP` requires a target `step`
  and `max_loops` (1-20).
- A `DISABLED` element is skipped in the main sequence but may still be invoked
  as a `RUN_STEP` compensation, so evidence-capture steps can stay out of the
  happy path.
- `SUB_PIPELINE` elements reference another pipeline by name
  (`config.pipeline`); the reference is never copied into storage. `flatten`
  (`composer.py`) expands references at run time: child names become
  `<parent>.<child>`, children take the parent's phase, a child with no
  dependencies waits for the parent's dependencies, and dependants of the
  parent wait for all its children. Recursion and nesting deeper than 5 are
  rejected. Cross-pipeline (sub-pipeline) validation only checks existence.
