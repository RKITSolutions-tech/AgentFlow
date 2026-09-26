# AgentFlow Pipeline Visualisation Design

## 1. Purpose

This document defines the interactive pipeline and Run monitoring experience in AgentFlow.

The visual experience must make it easy to answer:

- What is happening now?
- What happened previously?
- What went into a step?
- What happened inside it?
- What came out?
- Why did it pass or fail?
- What prompt was sent?
- What did the agent reply?
- Which tests ran?
- What files changed?
- What screenshots or other evidence were produced?

The graph is an operational interface, not decorative visualisation.

## 2. Relationship to CloudCLI

Phase 1 should reproduce the useful CloudCLI-style operational experience as closely as practical while remaining an independent implementation:

- Projects
- Sessions
- agent conversations
- Files
- Git
- Terminal
- responsive desktop/mobile access

AgentFlow then extends that model with Sprint, Run and Pipeline monitoring.

## 3. Desktop Layout

```text
┌───────────────────────────────────────────────────────────────────────────────┐
│ Project / Sprint / Task / Run                                    RUNNING      │
├───────────────┬───────────────────────────────────────────┬───────────────────┤
│ Navigation    │ Pipeline                                  │ Step Inspector    │
│               │                                           │                   │
│ Overview      │ [Prepare] → [Setup] → [Agent] → [Tests]   │ Selected Step     │
│ Sessions      │                                 ↓         │                   │
│ Tasks         │                           [Playwright]     │ Summary           │
│ Runs          │                                 ↓         │ Inputs            │
│ Files         │                           [Acceptance]     │ Outputs           │
│ Git           │                                 ↓         │ Prompts           │
│ Tests         │                             [Commit]       │ Tests             │
│ Terminal      │                                           │ Artifacts         │
│               │                                           │ Raw               │
└───────────────┴───────────────────────────────────────────┴───────────────────┘
```

## 4. Orientation

Desktop default:

```text
left → right
```

Mobile default:

```text
top
 ↓
bottom
```

Orientation is a presentation concern, not pipeline semantics.

## 5. Graph Nodes

Every executable or logical pipeline element is represented as a node.

Examples:

```text
Agent
Test
Playwright
Git
Acceptance
Manual Approval
SubPipeline
HTTP
SSH
Docker
Health Check
```

Node categories:

```text
Development
Verification
Rigging / Infrastructure
Human
Control
SubPipeline
```

Rigging steps such as SSH, HTTP, Docker Compose and external application control must be visually distinct from development steps.

## 6. Node States

Required runtime states:

```text
Pending
Running
Passed
Failed
Warning
Paused
Retrying
Skipped
Disabled
Waiting for Human
Cancelled
Timed Out
```

State must never rely on colour alone. Use text, icons and shape/state treatment.

A node may show name, status, duration, attempt and short result.

## 7. Step Inspector

Selecting a node opens the right-hand inspector.

Default presentation is concise. Raw execution details are available on demand.

Recommended tabs:

```text
Summary
Input
Output
Prompts
Tests
Files
Artifacts
Events
Raw
```

Only relevant tabs need to be displayed.

## 8. Summary

Shows:

```text
step name
type
status
duration
attempt
result summary
failure summary
compensation action
```

## 9. Input

May show:

```text
effective configuration
Task context
acceptance criteria
Skills
resource references
commands
environment summary
input files
previous step outputs
```

Secrets remain redacted.

## 10. Output

May show:

```text
stdout
stderr
structured outputs
agent response
generated values
result metadata
```

## 11. Prompts

Prompt visibility is mandatory.

The UI should make available:

```text
system prompt
role instructions
Project instructions
Sprint instructions
Task context
Skills
acceptance criteria
retry feedback
steering
effective final prompt
agent response
```

Where prompt composition is supported, distinguish source fragments, resolved prompt, and reply.

## 12. Tests

For test steps show:

```text
command
working directory
duration
exit code
test count
passed
failed
skipped
coverage
failure details
stdout
stderr
```

## 13. Files

May include:

```text
files read
files created
files modified
files deleted
Git diff
repository
branch/worktree
```

## 14. Artifacts

Artifacts behave like attachments to a step.

Examples:

```text
screenshots
browser traces
videos
logs
reports
coverage
patches
generated documents
```

Selecting an artifact should open an inline preview where practical.

## 15. Artifact Library

All artifacts are also available in a global Artifact Library.

Filters should include:

```text
Project
Sprint
Task
Run
Iteration
Step
type
date
```

Each artifact retains its originating StepExecution reference.

## 16. Raw Data

Raw execution data is available but not shown by default.

Possible content:

```text
raw JSON
raw CLI output
raw event records
raw provider response
resolved variables
process metadata
timestamps
```

## 17. Historical Replay

Historical replay is required.

The user should be able to move through a completed Run in execution order.

```text
|< Previous    Play/Pause    Next >|
```

Replay updates:

- selected node
- node states at that point
- inspector content
- timeline
- relevant artifacts

Replay never re-executes anything. It reconstructs recorded state from persisted events.

## 18. Timeline

Example:

```text
09:01 Run started
09:01 Prepare Git started
09:02 Agent started
09:06 Agent completed
09:07 Tests started
09:07 Tests failed
09:08 Retry started
09:11 Tests passed
09:12 Playwright started
09:13 Screenshot captured
09:14 Acceptance passed
09:14 Commit created
09:14 Run completed
```

Clicking an event selects the related graph node and execution state.

## 19. Ralph Iteration Grouping

Runs with multiple iterations should group nodes by iteration.

```text
Iteration 1
  Agent
  Tests FAILED

Iteration 2
  Agent
  Tests PASSED
  Playwright FAILED

Iteration 3
  Agent
  Tests PASSED
  Playwright PASSED
  Acceptance PASSED
```

Groups may be collapsed.

By default, expand the current Iteration and the most recent failed Iteration. Collapse older Iterations.

A later enhancement may compare iterations side by side, including prompts, replies, Git diffs, tests, screenshots and acceptance changes.

## 20. SubPipelines

SubPipelines initially appear as collapsed nodes. Users may expand inline or drill into them.

```text
[Standard Web Setup]
  ↓ expand
  Start DB
  Start App
  Health Check
```

## 21. Manual Steps

Manual nodes clearly show waiting, approved, rejected or cancelled states. The inspector contains the action controls.

For a design mockup review, the inspector displays the design specification and all proposed screens together, with approve, request-changes and cancel actions. Approval visibly unlocks the implementation path; requested changes return to mockup generation.

## 22. Compensation Flow

Compensation paths must be visible and distinguishable from normal flow.

```text
[Test FAILED]
      ↓ compensation
[Capture Diagnostics]
      ↓
[Stop]
```

## 23. Live Updates

SSE is preferred for:

- node state changes
- log updates
- timeline events
- test progress
- artifact availability

WebSocket remains appropriate for terminal interaction.

## 24. Graph Technology

The normal AgentFlow shell may use Flask/HTMX. The graph is a justified richer client-side component and should support:

- node selection
- pan
- zoom
- dynamic layout
- grouped nodes
- custom node rendering
- future editing
- desktop/mobile orientation

The architecture should remain graph-library agnostic.

## 25. Future Editing

Phase 1 is primarily view mode. Future editing should support:

```text
add step
remove step
enable/disable
skip
reorder
select prompt library item
select Ralph instruction
insert sub-pipeline
configure compensation
```

## 26. Phase 1 UI Priority

The first UI priority is the CloudCLI-style operational workspace:

```text
Projects
Sessions
Agent chat
Files
Git
Terminal
basic Runs
basic step inspection
```

A sophisticated pipeline graph must not delay daily usability.

## 27. Phase 2 UI Priority

Phase 2 adds:

```text
full interactive graph
iteration grouping
historical replay
artifact-rich inspection
compensation flow
manual nodes
design mockup preview and approval
sub-pipeline expansion
prompt library controls
future visual editing foundations
```

## 28. Mobile Behaviour

Mobile uses top-to-bottom graph orientation. The inspector becomes a bottom sheet, full-screen panel or dedicated tab.

Essential mobile actions:

```text
inspect current step
view prompt/reply
view failure
view screenshot
approve manual step
pause/stop Run
send steering
```

## 29. Core Rule

Every visible node should answer:

```text
What went in?
What happened?
What came out?
What evidence exists?
Why did it succeed or fail?
```

## 30. Implementation Decisions (Phase 2, autonomous assumptions)

Made without the owner in the loop while building task 24; recommendations to
confirm.

- **Routes.** A pipeline execution belongs to a Project, not to a Sprint, so the
  view lives at `/projects/<id>/pipelines/executions/<eid>` (with
  `graph.json`, `nodes/<name>.json`, `steps/<sid>/log`, `export.json|csv`,
  `cancel`, `steps/<sid>/decision`) rather than under `/sprints/<id>/run/<id>`.
  Ralph runs are at `/projects/<id>/ralph/<run>` and link each iteration to its
  verification execution. Project tabs gained **Pipelines** and **Ralph**.
- **Graph technology (§24).** No d3 or other dependency. The server assigns every
  node a `layer` (longest dependency path; teardown trails everything);
  CSS lays layers out as columns at >=861px and rows below it (§4), and a small
  script draws edges as an SVG overlay (dependency edges solid, compensation
  edges dashed and amber). Node state is always text + icon + border colour,
  never colour alone; rigging nodes use a dashed border and human nodes a pill
  shape; the running node pulses (a static ring under reduced motion).
- **States.** Pending, Running, Retrying (running attempt > 1), Passed, Failed,
  Warning (failed but `CONTINUE`), Waiting for human, Skipped, Disabled,
  Cancelled, Timed out. "Paused" is shown as the execution status.
- **Inspector** is loaded on demand per node: Summary (attempts, duration, exit
  code, on-failure policy, dependencies), Failure, Input (for AGENT steps this
  is the *effective prompt*, §11), Output / agent reply, Configuration (secrets
  redacted), Attempts, Events, and a download of the full output. The
  Tests/Files/Artifacts tabs are not separate yet: test steps show their command
  output, and artifacts arrive with task 25. Manual steps show approve /
  reject (or "request changes" for reviews) controls in the inspector and a
  name is required.
- **Live updates** poll `graph.json` every 2s while the execution is running
  and reload when any node state changes; it is not SSE (§23). The selected
  node is kept in the URL hash. Ralph pages use the existing `data-autorefresh`.
- **Not built:** collapsing sub-pipelines into one node (§20; children appear flat with their
  parent as a badge), grouping *graph nodes* by Ralph iteration (§19; the Ralph
  page lists iterations, each linking to its own graph), side-by-side iteration
  comparison, the design-mockup review layout of §21.
- CSV export prefixes cells beginning with `= + - @` with `'` so command output
  cannot become a spreadsheet formula.
- The mobile/desktop layout tests (`tests/test_backlog_sprint_viewports.py`) skip
  where Playwright or a browser is missing. The front-end JavaScript has since had a
  real-browser pass (Chromium, 375px and 1280px; PHASE2_PLANNING §9).

### Artifact library (task 25)

- One index table (`artifact_library`, plus `artifact_tags` and
  `artifact_comparisons`) with content on disk under the artifact root. Each row
  keeps its originating `step_execution_id`, `execution_id` and step name and,
  for Ralph, `ralph_run_id` + `iteration_number` (§15). Routes live under
  `/projects/<id>/artifacts` (list/filter, `search`, `export.csv`, detail,
  `preview`, `download`, `tags`, `compare`); a project tab was added.
- **Collection.** Every step's captured output is indexed as a `log` artifact.
  Any element may list `config.collect` globs (relative to the repository; absolute
  paths, `..` and symlinks resolving outside the repository are ignored, max 50
  files and 25 MiB each) and `ARTIFACT_CAPTURE` elements use `config.paths`.
  Files are **copied** under `pipelines/<execution>/step_<id>/`, so a later edit
  in the repository does not change stored evidence. Each Ralph iteration also
  stores its `git diff` (with untracked files listed in a header) as a `diff`
  artifact. A collection failure records an `ArtifactCollectionFailed` event and
  never fails the step. It hooks step completion inside the engine rather than
  listing files through the ExecutionProvider, because host execution shares the
  filesystem; a Docker provider will need a file-copy method.
- **Classification** is by extension: images -> `screenshot`, `.mp4/.webm/.mov`
  -> `video`, `.diff/.patch` -> `diff`, `*trace*.zip|.trace|.har` -> `trace`,
  `.log/.txt/.out/.err` -> `log`, `.html/.json/.xml/.csv/.md` -> `report`, else
  `file`. (The task text mapped `.html` to trace; a Playwright trace is a zip, so
  `.html` is a report here.) Text kinds are redacted before they are written and
  flagged; images record PNG/GIF dimensions from the header.
- **Comparison** is a unified text diff for two text artifacts (capped at 2000
  lines) and a byte-level comparison for two screenshots (identical / dimensions /
  differing bytes). True visual diffing needs an imaging library, which is not a
  dependency, so it is left out and the result says so. Results are stored.
- **Safety.** Inline preview is limited to raster images and text; SVG and HTML
  are never rendered inline (they can carry script) and only download. Previews
  send `nosniff` and a restrictive CSP. CSV export neutralises formula cells.
  Search escapes `LIKE` wildcards. Every route 404s when the artifact is not in
  the project in the URL.
- Phase 1 Run artifacts (`app/runs`) are **not** migrated into the library; they
  stay on the Run pages. Only pipeline and Ralph evidence is indexed. Filtering by
  Sprint or Task is not offered because neither has an execution link yet.

### Historical replay and timeline (task 29)

- **Nothing is re-executed.** `app/pipelines/replay.py` folds the execution's
  `pipeline_events` (in id order) over its stored `step_executions`. Position `N`
  means "after the first N events"; 0 is before anything ran. The same
  `visualization.build_graph` / `step_detail` produce the output, given the
  reconstructed step rows instead of the stored ones, so a replay at the last
  event is identical to the live view (tested).
- **Reconstruction rules.** Events say *when* a step changed state; the step row
  supplies *what* it produced. `StepStarted` -> RUNNING; `StepCompleted` -> PASSED;
  `StepFailed` -> the step's final FAILED / TIMED_OUT / CANCELLED; `StepSkipped` /
  `StepDisabled`; `ManualApprovalRequested` -> WAITING with the execution PAUSED and
  `waiting_step_id` set (they stay so through `ManualApprovalReceived` until
  `PipelineResumed`, as recorded); `LoopBack` events rebuild the loop counters. A step
  with no event yet is absent (PENDING). Output, exit code and completion time are
  blanked until the step is terminal, so scrubbing back never shows results from the
  future. Retries show as attempts appearing one by one. Artifacts "so far" are
  those of steps terminal (or waiting) at that event. The execution's reason, warning
  count and needs-attention flag are only shown at the final event, because they are
  not event-sourced.
- **Event taxonomy** (`EVENT_KINDS`): `milestone` (pipeline started / resumed /
  completed / failed / cancelled), `step`, `human` (approval requested / received,
  intervention), `compensation` (compensation, loop-back, sub-pipeline) and
  `housekeeping` (artifact collection failure, resource cleanup). Each event has an
  icon and its node; unknown types are `other`. Housekeeping events change no node,
  so `ReplayController` steps over them but they stay in the timeline and the export.
- **Playhead.** `ReplayController` is a small state machine (previous / next / first /
  last / seek, play at 0.5x-4x, pause, `tick`) over the visible events. The server
  keeps no playhead: `POST .../replay/seek` takes `current` + `action` and returns the
  state at the new position; the browser owns position and the play timer (one seek per
  tick at `1000ms / speed`), and discards a response a newer click has overtaken.
- **Routes** (under `/projects/<id>/pipelines/executions/<eid>/replay/`): `events`
  (paginated, `?kind=`), `state?event=N` (graph, artifacts, inspector for the
  event's node, or `&node=`), `seek`. The task text put these under
  `/pipelines/<run_id>/`; they follow the existing execution routes instead.
- **UI.** A "Replay" button on the execution page opens a panel under the graph:
  first / previous / play-pause / next / last, speed, kind filter, and a timeline strip
  that scrolls sideways *inside itself* (the page never does); every control is at least
  44px. The current event is `aria-current`, later ones are dimmed. The inspector
  follows the current event's node (so stepping shows what just happened rather than
  keeping a stale node); "Back to final state" restores the live graph. Live polling is
  suspended while replaying. `#replay` in the URL opens the panel.
- **Ralph iterations (§19).** The task text assumed `step_executions.iteration_number`;
  it does not exist. Each Ralph iteration's verification is its *own* execution
  (created with `variables.run` / `variables.iteration`, linked from
  `ralph_iterations.verification_execution_id`), so grouping is a strip of "Iteration N"
  links (with status, files changed, analysis) across the run's executions on every
  such execution's page, each opening straight into that iteration's replay. There is no
  merged multi-execution timeline, and no swimlanes or side-by-side comparison.
- **Performance.** State is O(events) per position and memoised per `Replayer`
  (built once per request; it reads events, steps and artifacts once). 1,200 events
  replay in well under 2s in a test; timelines are capped at 1,000 events per page.
  For much larger logs, checkpoint every K events instead of folding from zero.
- The mobile/desktop behaviour is covered by Playwright tests at 375px and 1280px in
  `tests/pipelines/test_replay.py`.
