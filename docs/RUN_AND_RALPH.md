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

## 22. Implementation Decisions (Phase 2, autonomous assumptions)

Made without the owner in the loop while building task 23; recommendations to
confirm, not settled decisions.

- **Separate from Phase 1 Runs.** Command-step Runs (`app/runs/`) are unchanged.
  A Ralph run is its own record (`ralph_runs`, `ralph_iterations`,
  `ralph_steering` in `app/ralph/`), with its own smaller status set
  (`CREATED`, `RUNNING`, `VERIFYING`, `WAITING_FOR_HUMAN`, `PAUSED`, `BLOCKED`,
  `COMPLETED`, `FAILED`, `CANCELLED`, `TIMED_OUT`). The task text's
  `blocked_no_progress` is `BLOCKED` with `needs_attention` set and the reason
  naming the signal; the iteration is `NO_PROGRESS`.
- **No Task entity yet.** A Ralph run takes a task title/text/acceptance list
  and optionally links a `planned_work_items` row and a Sprint. Nothing yet
  turns approved planned tasks into Ralph runs; that belongs with Sprint
  execution (§19).
- **Loop per iteration:** build prompt → agent work → collect changes →
  verify → evaluate. The first iteration starts an agent session; later ones
  **resume the same session** with the feedback prompt, so the agent keeps its
  own context. The session id is stored on the run.
- **Verification is mandatory and is a pipeline.** A run cannot be created
  without a `verification_pipeline`; completion is "that pipeline's execution
  completed", never the agent's claim (§14). Each iteration links its
  `pipeline_executions` row, so evidence, prompts and outputs are reachable from
  it. If verification pauses for a person the run becomes `WAITING_FOR_HUMAN`.
  Acceptance-criteria evaluation beyond "the pipeline passed" is task 26.
- **Failure evidence** in the retry prompt is built from the failed step
  executions (type, name, exit code, last 1500 chars of output), the files
  changed so far, and a short history of previous attempts.
- **No-progress policy is Ralph's, across iterations (§10):**
  `identical_failure_limit` (default 2) compares a failure signature (failed
  step names + output with numbers, hashes and temp paths normalised);
  `no_change_limit` (default 2) compares a content-aware working-tree signature
  (`git status --porcelain` plus each changed file's bytes) across the last
  `limit + 1` iterations. Either blocks the run for manual review; `unblock`
  (optionally with a steering message) resets the block. Pipeline `LOOP`
  keeps its own within-step limits.
- **Limits:** `max_iterations` (default 8, then `FAILED`) and
  `max_runtime_seconds` (then `TIMED_OUT`; time is accumulated across pauses).
  Only run-level configuration exists; System/Project/Sprint/Task cascades
  (§8) are not built.
- **Steering** is stored per run with the iteration it was written during and
  injected into the next iteration's prompt exactly once
  (`consumed_iteration`).
- **Pause/cancel are database flags** checked at iteration boundaries (a safe
  boundary, §12) rather than signals, so they work from any request and a
  resumed run rebuilds purely from the database. A cancel during an iteration
  ends the run after that iteration's verification finishes. A run whose
  worker vanished (restart) is marked `BLOCKED` by `RalphManager.reconcile()`.
- **Auto commit** commits locally after verification passes, with the message
  `[AUTO] Phase 2 pipeline: <sprint> task <id> (iteration <n>)`, stores the
  SHA on the iteration and run, and blocks the run if the commit fails (git
  state must be valid, §14). **It does not push**: pushing is outward-facing,
  so it is left to a person or a later opt-in setting. Commit point is task
  completion only; per-iteration checkpoint commits (§16) are not done.
- Prompts and replies are redacted before storage and flagged
  (`redacted`); the *unredacted* prompt is still what the agent receives.

### Opt-in research on repeated failure (task 47)

A run-level, opt-in trigger sitting alongside the no-progress policy above, not
replacing it: `ralph_runs.research_on_failure` (default off) and
`ralph_runs.failure_threshold` (default 2, independent of
`identical_failure_limit`/`no_change_limit`) are set at run creation
(`app/ralph/views.py` `start_run`, `app/templates/ralph/list.html`).

- **What counts as "repeated failure" here:** a plain trailing streak of
  `FAILED`/`NO_PROGRESS` iterations (`RalphOrchestrator._consecutive_failures`),
  counted independently of `_no_progress`'s failure-*signature* comparison --
  research does not care whether the failures were identical, only that there
  have been `failure_threshold` of them in a row. The trigger fires exactly
  once per streak (`== failure_threshold`, not `>=`); a fresh streak after a
  `PASSED` iteration can trigger again.
- **Ordering with the no-progress block:** `RalphOrchestrator._maybe_research`
  runs *before* `_no_progress` in the same failure-analysis step, so on an
  iteration boundary where both a research trigger and a no-progress block
  could fire, research fires first. It never changes whether the run
  continues, blocks, or fails -- a person reviewing a `BLOCKED` run already
  has the report waiting for them, but research firing does not itself block
  or pause a run that would otherwise continue.
- **Driving the agent:** a `ResearchAgent` (`app/agents/research_agent.py`) is
  driven ad hoc from `RalphOrchestrator`, the same "not through the pipeline
  engine" pattern `PlanningAgent` uses for sprint planning -- a Ralph
  iteration is not a pipeline execution, so this bypasses
  `PipelineEngine._h_research` entirely. The question reuses
  `_analyse_failure`'s evidence text (failed step names/output) and the same
  short attempt history/changed-files fields already fed back into the retry
  prompt, rather than rebuilding either.
- **No lock nesting.** Neither `ResearchAgent.research()` nor this trigger
  ever calls `app/projects/lock.py` (§23 "Locking" of
  docs/AGENT_ADAPTER.md): the research session runs inside whatever lock
  `RalphManager.start()` already holds for the run's iteration, not a second
  acquisition.
- **Storage.** A completed report is written as a `research_report` Artifact
  (`app/artifacts/collector.py`), tagged `ralph_research`, `iteration_<n>`,
  `failure_analysis`, and linked back to the triggering iteration via
  `ralph_iterations.research_artifact_id` (plus `research_session_id` for the
  underlying `research_sessions` row). `research_report_presented` starts
  `false` and stays false until a person acts on it.
- **Advisory only, never auto-applied** (docs/AGENT_ADAPTER.md §23: "A person
  accepts findings before they change a plan or acceptance criteria"). The
  Ralph execution view (`app/templates/ralph/detail.html`) shows any
  unpresented report (summary + expandable findings) with two AJAX actions:
  "Use findings as steering" and "Dismiss". Both mark the report presented;
  only "Use findings as steering" also calls the *existing*
  `ralph_steering` mechanism (`app/ralph/models.add_steering`) -- the same
  one the manual "Add steering" form uses -- so the findings are injected into
  the next iteration's prompt exactly once, per the steering rule already
  documented above. There is no separate injection path.

### Project execution lock (task 28)

Implemented in `app/projects/lock.py`, table `project_locks` (plain `sqlite3`, like the
rest of the app; the task text's SQLAlchemy model is not used).

- **One ACTIVE lock per project**, enforced by a partial unique index, so two
  processes racing to acquire cannot both win. Columns: `owner_type`
  (`ralph_run` | `pipeline_run` | `manual_run`), `owner_id`, `acquired_at`,
  `heartbeat_at`, `released_at`, `status` (`ACTIVE` | `STALE` | `RELEASED`). Rows
  are kept as history rather than deleted.
- **Scope is the whole project**, as §19 says: a manual Run, a standalone pipeline
  execution and a Ralph run for the same project exclude each other. A Ralph run's
  verification pipeline executes *inside* the Ralph run's lock (the orchestrator calls
  the engine directly), so it does not conflict with its own run.
- **Lifecycle.** The manager's `start()` acquires synchronously, so the caller gets a
  `LockConflict` (a `ValueError`; HTTP 409 naming the holder) instead of a silent
  failure. The worker thread then owns a `Heartbeat` that refreshes `heartbeat_at`
  every **30 s** on its own connection and releases the lock in its `finally`, whatever
  the outcome (complete, cancel, error). Pausing or blocking a Ralph run ends its
  worker, so it releases the lock; the run resumes by re-acquiring.
- **Conflict strategy: fail by default; waiting is opt-in (task 36, below).**
  A never-started manual Run is marked `FAILED` and a `PENDING` pipeline execution
  `CANCELLED`, each with the holder in the reason; a Ralph run stays `CREATED` so it can
  be resumed once the project is free.
- **Same owner may re-acquire** (it just refreshes the heartbeat), so a request from the
  owner's own run, such as steering, never fails against its own lock.
- **Stale threshold: 360 s** (12 missed heartbeats). A stale lock is taken over by the
  next `acquire` (marked `STALE`), swept every **60 s** by `Sweeper` (not started when
  `TESTING`), and, because no worker survives a restart, *all* ACTIVE locks are marked
  `STALE` at app start. A displaced owner's late heartbeat is a no-op
  (`refresh_heartbeat` returns False).
- **UI.** The project overview shows the holder, acquisition time and last heartbeat
  (or "Not locked"); Runs, Pipelines and Ralph pages show a badge when locked, red
  "Lock stale" past the threshold. The sprint queue's `project_busy` treats a held lock
  as busy, and also a *paused or waiting* Ralph run, which has released the lock but
  still owns the working tree.

### Finer-grained locks (task 36)

Assumptions made without the owner in the loop.

- **Scope is a project setting, default unchanged.** `projects.lock_scope` is `project`
  (whole-project lock, as above) or `repository` (Edit project > "Execution lock").
  In `repository` mode a Run, pipeline execution or Ralph run locks only the repository it
  was started on (`project_locks.repository_id`), so independent repositories in one
  project execute together. It is opt-in because one Project's runs can share things the
  lock cannot see (a database, a port, a sprint's working assumptions). An execution with
  no repository still takes the whole project. Branch-level locks were considered and not
  built: two runs on branches of one repository share a working tree, so a repository is the
  smallest safe unit until worktrees are used per run.
  **(2026-09-27, task 41) Revisited, decision stands.** No new information changes the
  conclusion: branch scope still cannot be made safe without per-run worktrees, which is a
  separate, larger change (§19 execution model) than a locking one. This question is closed;
  a future worktrees-per-run change is what would reopen it, not another look at the lock.
- **Overlap rule.** Two locks conflict when either is whole-project or both name the same
  repository. `lock.acquire` reads the rivals and inserts inside `BEGIN IMMEDIATE`, since the
  unique index (now `project_id, IFNULL(repository_id, 0)` on ACTIVE rows) only covers
  equal scopes; it stays as the backstop. Stale takeover, the finished-owner shortcut,
  same-owner refresh, the sweeper and startup release all work per lock. The sprint
  queue's `project_busy` takes a repository so a busy repository A does not stop a task for B;
  the project overview and badges list every held lock with its scope.
- **Interactive chat sessions and terminals do not take the lock.** They are a person
  working, and blocking (or being blocked by) a run would make them unusable while Ralph
  is looping; they are also not owned by a single process the lock could heartbeat. Instead
  the chat and terminal pages show a "Locked ... is executing here; changes you make may
  collide" warning through the same badge (`lock_status(..., advisory=True)`).
  **Decision (task 41): warn-only is final, not revisited further.** Read-only enforcement
  would need new session-to-lock plumbing (a chat/terminal session is not a single
  heartbeating process the lock model can own or expire the way a run's worker thread is),
  for a benefit that is already covered by the advisory badge naming the holder. Both the
  chat page (`app/templates/sessions/chat.html`) and the terminal page
  (`app/templates/workspace/terminal.html`) render `lock_status(project.id, advisory=True)`;
  confirmed still true as of task 41.
- **Wait instead of fail is per start, opt-in** ("Wait if the project is busy" on the Run,
  Pipeline and Ralph forms; `wait_for_project=on`; `manager.start(id, wait=True)` returns
  True when queued). The default is still to fail with the holder named. A waiting run joins
  `project_lock_queue` synchronously (so the order is the order of requests), its worker
  thread polls every second (`Ticket.wait`) and takes the lock when no overlapping lock is
  held and no overlapping waiter is ahead. Overlapping-only means a waiter for repository B
  does not queue behind one for A, while a whole-project waiter waits for both. A fail-fast
  request is also refused while overlapping waiters exist, so nobody jumps the queue.
- **Waiting runs stay in their pre-start state** (Run `CREATED`, execution `PENDING`, Ralph
  `CREATED`) and the badge shows "Queued #n: <owner> is waiting". Stopping/cancelling one
  ends it (`CANCELLED`, "Stopped while waiting for the project") and removes it from the queue.
  After `WAIT_SECONDS` (1 hour) it gives up: a Run fails and an execution is cancelled with the
  reason, and a Ralph run stays `CREATED`, flagged needing attention, so it can be resumed.
- **Dead waiters.** A waiter that has not polled for 60 s (its thread died) is expired, and
  every WAITING entry is expired at app start with the orphan locks, so a crashed process
  cannot wedge the queue.
- **Sprint automatic mode queues instead of failing (task 41).** `queue.promote_next(...,
  queue_if_busy=True)` — used only by `queue.advance()` — skips its own early "project busy"
  refusal when the sprint has no task of its own already `IN_PROGRESS` (that check still
  applies unconditionally, so a sprint never has two tasks in flight at once); it still
  creates the Ralph run and marks the task `IN_PROGRESS`. The call sites that start an
  auto-promoted run (`RalphManager._advance_sprint`, `RalphManager.resume_automatic_sprints`,
  `sprints.views.auto_run`) pass `wait=True` to `RalphManager.start`, so a genuinely busy
  project joins `project_lock_queue` (above) instead of the task erroring out and automatic
  mode stalling until an unrelated event happens to call `advance()` again. A manual "Run
  next task" click is unaffected (`queue_if_busy` defaults to `False`, so it still fails fast
  with the holder named).
- Tests: `tests/projects/test_lock_scopes.py` (overlap, stale takeover per scope, races via
  the index, queue order and fairness, wait/timeout/cancel for Runs, pipelines and Ralph,
  advisory pages, settings form); `tests/sprints/test_queue.py` (automatic mode queuing
  behind a busy project, task 41).
