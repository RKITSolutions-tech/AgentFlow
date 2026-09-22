# AgentFlow Run and Ralph Design

## 1. Purpose

This document defines AgentFlow Run lifecycle, Ralph iteration, state management, retry behaviour, steering, failure handling and completion rules.

Ralph is an execution strategy for a prepared Task. It is not the primary Sprint planning mechanism.

```text
Backlog
→ Sprint Planning
→ Reviewed Task
→ Acceptance Criteria
→ Release
→ Run
→ Ralph Iterations
→ Verification
→ Commit
```

## 2. Run

A Run is one attempt to execute a Task. A Task may have multiple Runs.

A Run captures resolved execution configuration at start.

```yaml
agent: codex
git_strategy: worktree
execution_provider: host
max_iterations: 8
setup_pipeline: standard-web-setup
development_pipeline: standard-ralph
verification_pipeline: web-full
teardown_pipeline: standard-cleanup
auto_commit: true
```

## 3. Run States

```text
CREATED
PREPARING
READY
RUNNING
VERIFYING
RETRYING
WAITING_FOR_HUMAN
PAUSED
BLOCKED
COMPLETED
FAILED
CANCELLED
TIMED_OUT
```

Simplified state flow:

```text
CREATED
  ↓
PREPARING
  ↓
READY
  ↓
RUNNING
  ↓
VERIFYING
  ├── PASS → COMPLETED
  ├── FAIL → RETRYING → RUNNING
  ├── HUMAN → WAITING_FOR_HUMAN
  └── FATAL → FAILED
```

Pause and cancel may occur from active states.

## 4. Iteration

An Iteration is one agent-work plus verification cycle.

```text
Build Context
↓
Invoke Agent
↓
Collect Changes
↓
Verify
↓
Evaluate Acceptance
↓
Pass or Retry
```

Suggested Iteration states:

```text
CREATED
BUILDING_CONTEXT
RUNNING_AGENT
COLLECTING_CHANGES
VERIFYING
EVALUATING
PASSED
FAILED
NO_PROGRESS
BLOCKED
CANCELLED
```

## 5. Iteration Context

Context may include:

```text
Task
Sprint goal
acceptance criteria
Project documentation
Task documentation
Skills
repository state
previous iteration result
test failures
browser failures
screenshots
Git diff
steering messages
standard Ralph instructions
```

AgentFlow should avoid sending all available context blindly. Context should be resolved deliberately from Project, Sprint, Task, Run, evidence and step configuration.

## 6. Prompt Persistence

Every effective prompt sent to the agent must be stored, including:

- initial implementation prompt
- retry prompt
- test failure prompt
- UI failure prompt
- visual review prompt
- steering injection
- resume prompt

Every agent reply must also be persisted.

Secrets are redacted before persistence wherever practical, with an indication that redaction occurred.

## 7. Ralph Loop

```text
Prepare Git
↓
Setup Pipeline
↓
Iteration
  ↓
  Agent Work
  ↓
  Verification Pipeline
  ↓
  Acceptance Evaluation
  ├── Pass
  │    ↓
  │  Commit
  │
  └── Fail
       ↓
     Build Feedback
       ↓
     Next Iteration
↓
Teardown
```

## 8. Limits

Run policy supports:

```text
max_iterations
max_runtime
```

Values may be configured at System, Project, Sprint, Task or Run level.

## 9. Retry Feedback

Retry prompts should be evidence-based rather than generic.

Example:

```text
Iteration 3 failed.

Unit tests:
PASS

Playwright:
FAIL

Failure:
Expected Save button to remain visible at 390px.

Screenshot:
artifact://...

Console:
No errors.

Changed files:
templates/job.html
static/job.css
```

## 10. No Progress Detection

Signals may include:

```text
same failing test repeatedly
same error signature
same Git diff
no file changes
no acceptance progress
agent repeatedly claims success while verification fails
```

Policy may define limits such as:

```text
identical_failure_limit
no_change_limit
```

Possible responses:

```text
pause
manual intervention
alternate compensation pipeline
stop
```

Ralph owns no-progress and retry-limit policy across Iterations. Pipeline-level loops are limited to retries contained within a step or sub-pipeline and must not cross the Ralph Iteration boundary.

## 11. Human Steering

Users may add SteeringInstructions during a Run.

Example:

```text
Do not replace the current uploader.
Extend the existing component.
```

Steering is timestamped, persisted, associated with the relevant Run/Iteration, injected into subsequent context and visible during replay.

## 12. Pause, Resume and Cancel

Pause should stop progression at a safe boundary. The UI must distinguish whether the Run is paused while a subprocess continues or whether the process itself has stopped.

Resume reconstructs necessary state from persistence.

Cancel should stop active pipeline work, terminate owned processes where appropriate, attempt teardown, retain evidence and mark the Run cancelled.

## 13. Blocked State

BLOCKED means execution cannot continue without an external decision or dependency.

Examples:

```text
missing credential
unavailable test environment
unresolved planning question
manual approval rejected
repository conflict
```

## 14. Completion

A Run must not complete because the agent says it is done.

Completion normally requires:

```text
required pipeline steps passed
required acceptance criteria passed or waived
Git state valid
required artifacts captured
no blocking errors
```

## 15. Acceptance Evolution

Acceptance criteria may be added during execution. Each records:

```text
origin
iteration
author/agent
review state
required/advisory
```

Policy determines whether newly added unreviewed criteria block completion.

## 16. Auto Commit

Auto commit is supported.

Possible commit points:

```text
successful iteration
successful Task
both
```

Initial recommendation is checkpoint commits after meaningful successful verification where useful, with a final commit at Task completion.

## 17. Run Evidence

A completed Run retains:

```text
effective configuration
prompts
replies
events
pipeline executions
test results
screenshots
browser traces
Git diffs
commits
acceptance results
steering
manual actions
```

## 18. Historical Replay

Run events form the replay source. Replay should show the pipeline graph, current iteration, current step, inspector, timeline and artifacts without re-executing anything.

## 19. Sprint Integration

Sprint execution chooses the next eligible released Task.

Initial execution is linear.

Eligibility:

```text
Task RELEASED
dependencies complete
not blocked
Project lock available
```

Initial lock scope is `PROJECT`.

## 20. Phase 1 Run Scope

Phase 1 should remain deliberately modest:

```text
single Task Run
single agent session
basic command/test execution
pause/stop
Run history
```

This lets AgentFlow become useful quickly.

## 21. Phase 2 Ralph Scope

Phase 2 adds:

```text
multiple Ralph iterations
evidence-based retry
no-progress detection
steering
auto commit
rich visual pipeline
advanced compensation
historical replay
manual approval/input
sub-pipeline control
prompt library controls
acceptance evolution
external rigging
enhanced no-progress logic
```
