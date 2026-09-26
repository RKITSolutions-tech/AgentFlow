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

## 25. Implementation Decisions (Phase 2, autonomous assumptions)

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
- **Not built:** historical replay (§17), timeline click-through (§18),
  collapsing sub-pipelines into one node (§20; children appear flat with their
  parent as a badge), grouping *graph nodes* by Ralph iteration (§19; the Ralph
  page lists iterations, each linking to its own graph), side-by-side iteration
  comparison, the design-mockup review layout of §21.
- CSV export prefixes cells beginning with `= + - @` with `'` so command output
  cannot become a spreadsheet formula.
- The mobile/desktop layout tests (`tests/test_backlog_sprint_viewports.py`) skip
  where Playwright or a browser is missing, which is the case in the
  environment these tasks were built in; the front-end JavaScript has only had
  a syntax check, so it needs a real browser pass.
