# AgentFlow UI Verification: Browser Capture Design

## 1. Purpose

`HIGH_LEVEL_DESIGN.md` §17 (UI Verification) lists "browser diagnostics,
screenshots, visual artifacts, agent assisted visual review" as required
capabilities, with Playwright as the initial automation technology. This
document designs how a Playwright-driven check gets captured so a human can
review what an agent's browser did, how far it got, and where it failed —
the AgentFlow equivalent of CloudCLI's agent-controllable browser tab
(`CLOUDCLI_GAP_ANALYSIS.md` G9), scoped to UI verification rather than a
general session workspace tab, per that gap's own resolution.

## 2. Prior Art: CloudCLI's Browser MCP

CloudCLI exposes browser control to agents as an MCP server
(`mcp__cloudcli-browser__*`) with a useful shape worth borrowing:

```text
browser_create_session(profileName?) -> sessionId
browser_navigate / browser_click / browser_type / browser_fill_form / ...
browser_snapshot(sessionId)        -> screenshot data URL + DOM text + metadata
browser_take_screenshot(sessionId) -> latest screenshot only
```

The "live" feel is not push-streamed video. State lives server-side per
session; a screenshot is captured as a side effect of each action, and the
viewer (human or agent) pulls the latest frame on demand. There are two
capture calls, not one, because the two are used differently: a full
snapshot (image + DOM text) is worth paying for right after an action that
changed the page; a plain screenshot pull is cheap enough to poll with. That
split — capture keyed to actions, not a clock, plus a cheap on-demand
image-only pull — is the model this document adopts, not literal CDP
screencasting.

## 3. What AgentFlow Already Has

Most of the storage and indexing side of this already exists and should be
reused rather than rebuilt:

- `app/artifacts/models.py` — `KINDS` already includes `screenshot`,
  `trace`, `video`, alongside `log`, `diff`, `report`, `file`.
  `Artifact` already links to `step_execution_id`, `ralph_run_id`,
  `iteration_number`.
- `app/artifacts/collector.py::classify()` already sorts collected files
  into `screenshot`/`video`/`trace`/`log`/`report`/`file` by extension (PNG,
  JPEG, WEBM, MP4, `*trace*.zip`, `.har`, ...), and `image_size()` reads
  PNG/GIF dimensions straight from the header for the metadata column — no
  imaging library needed.
- `app/pipelines/engine.py::_register_artifacts` / `app/runs/artifacts.py::
  collect_files` already copy files a step declares via glob patterns
  (`collect: [...]`) out of the working directory into the artifact store
  and index them, redacting text content on the way.
- `app/artifacts/views.py` already gives artifacts a search/tag/compare UI
  (`PIPELINE_VISUALISATION.md` §14-15) — a Playwright screenshot sequence
  becomes browsable for free once it is indexed this way.
- Run and pipeline execution detail pages already auto-refresh while active
  (`data-autorefresh="2000"` in `runs/detail.html`, matched in the pipeline
  execution view) — full-page polling reload, not a websocket. This is
  AgentFlow's existing answer to "how do I watch something in progress,"
  and the capture design should extend it rather than introduce a new
  transport.

## 4. The Gap: Capture Is Not Progressive

Both collection paths above run once, after a step's process has already
exited:

- Runs: `RunManager._run_step` calls `self.provider.wait(process.id)` (blocks
  to completion) before `artifacts.write_artifact(...)` and
  `artifacts.collect_files(...)` (`app/runs/executor.py:287-307`).
- Pipelines: `_register_artifacts` runs from the same post-step completion
  path (`app/pipelines/engine.py:304-330`).

So today, a Playwright step wired to `collect: ["screenshots/*.png"]` would
work, but every screenshot appears at once when the whole script finishes —
there is nothing to look at while it is running, and a hang or a slow
action is indistinguishable from a fast one until the process ends. This is
the one genuinely new mechanism this design needs; everything else in §3 is
reuse.

## 5. Proposed Design

### 5.1 Capture granularity: per action, not per interval

Confirmed in the prior conversation: capture a screenshot after each
Playwright action (`click`, `fill`, `goto`, `press`, assertion, ...) rather
than polling on a fixed timer. A fixed 5s interval is decoupled from what
the browser is doing — fast actions get skipped, slow ones leave a stale
frame on screen. Per-action capture keeps every frame meaningful and is
what CloudCLI's own model (§2) effectively does.

Playwright's built-in tracing (`context.tracing.start(screenshots=True,
snapshots=True)` → `trace.zip`) already does this for free, plus DOM
snapshots, console and network — classified as `kind="trace"` by
`collector.classify()`. A thin wrapper that also drops sequentially named
PNGs (`0001_click_#submit.png`, `0002_goto_...png`) alongside the trace is
what makes individual frames (not just the trace viewer) reviewable inline
in the Artifact Library without opening a separate tool.

### 5.2 Where it runs

A Playwright browser check runs as a subprocess through the existing
`ExecutionProvider` (`EXECUTION_PROVIDER.md` §2), same as any other step —
not a new execution path. It fits as:

- a **Pipeline step** (a `browser_check` element type, or a `CHECK` step
  that happens to run Playwright) when it is part of Ralph verification or
  a defined pipeline (`PIPELINE_ENGINE.md`), which gets it the richer
  Global Artifact Library indexing (§3) with proper `kind` classification
  and links back to `step_execution_id`/`iteration_number`; or
- a plain **Run step** (`RUN_AND_RALPH.md`) for an ad hoc/manual check,
  which gets the simpler per-run artifact table.

Either way the step declares its capture directory via the existing
`collect` glob mechanism; no new step schema field is needed beyond that.

### 5.3 Progressive indexing (the new piece)

To make a running step reviewable before it finishes, the execution loop
needs to index newly written files while the process is still alive, not
only after `wait()` returns. Two ways to get there, in increasing order of
change:

```text
A. Poll the declared collect glob(s) on the same cadence as the page's
   existing auto-refresh (~2s) while status == RUNNING, indexing any file
   not seen before. Reuses collect_files/register_existing as-is; only the
   call site moves from "after wait()" to "on a timer while waiting".

B. Have the capture wrapper emit a marker line to stdout after each
   screenshot ("SCREENSHOT 0007 0007_click_#submit.png"), which already
   flows through the live ProcessOutput event stream
   (app/execution/host.py). The step detail view parses marker events to
   know a new frame exists and fetches/indexes just that file.
```

(A) is simpler and fits the current polling-reload UI model exactly; (B)
gives an exact "how far did it get" event trail (useful for Ralph's
no-progress detection, `docs/RALPH_RESEARCH_DESIGN.md`) at the cost of a
required convention inside the capture wrapper. Recommendation: start with
(A) — it needs no changes to the capture wrapper or the event stream, only
a scheduled poll in the executor while a step with a declared `collect`
pattern is RUNNING — and revisit (B) only if the marker trail turns out to
be needed for Ralph progress detection specifically.

### 5.4 UI

No new component: the step/execution detail page already auto-refreshes
while active (§3). Newly indexed `screenshot` artifacts for the running
step just need to render as a filmstrip (most recent frame large, prior
frames as a strip beneath, per the existing artifact thumbnailing in
`app/artifacts/views.py`) rather than waiting for the completed-step
artifact list. On completion, the same filmstrip becomes the permanent
record, plus the full `trace.zip` for anyone who needs the DOM/network
detail the frames alone don't show.

### 5.5 Error surfacing

The last captured frame plus the action that produced it is the primary
"what went wrong" signal — no different in kind from any other frame, just
the last one. Playwright exceptions already include the failing selector
and action; redact and store that as the step's `reply` (Runs) /
step-execution error field (Pipelines), matching how every other step
already surfaces failures, so a failed browser check looks the same in the
UI as any other failed step, with the frame strip as the added context.

## 6. Non-Goals

- **Live CDP screencasting / mirroring a literal browser subwindow.**
  Discussed and explicitly set aside: it needs websocket streaming of
  screencast frames and a new always-on UI surface, for a payoff (smoother
  motion) that per-action capture on the existing 2s poll already covers
  for the stated goal (review progress + catch errors). Revisit only if
  per-action capture proves too coarse in practice.
- **A persistent, agent-controllable browser session/tab** (CloudCLI's
  actual G9 scope — an agent driving a live browser mid-conversation). That
  remains deferred to Phase 2 as a separate decision in
  `CLOUDCLI_GAP_ANALYSIS.md` §8 and is not this document's concern; this
  document is about capturing and reviewing a Playwright *check*, not
  giving an agent an interactive browser tool.

## 7. Open Questions

```text
Does the progressive-indexing poll (§5.3 option A) belong in RunManager's
  step loop, the Pipeline engine's step loop, or a shared helper both call?
  They currently duplicate the post-completion collection logic already
  (app/runs/artifacts.py vs app/artifacts/collector.py) — worth resolving
  together rather than adding a third duplicated poll loop.
Does a browser_check pipeline element type need its own schema/validator
  entry (schema.py, validator.py), or is "a step that happens to run
  Playwright with a collect pattern" sufficient without a dedicated type?
Trace files can be large (MAX_ARTIFACT_BYTES = 25MB in collector.py) --
  is that cap sufficient for a full trace with video, or does video need to
  stay opt-in per check?
Does Ralph's no-progress detection (RALPH_RESEARCH_DESIGN.md) want the
  marker-event trail (§5.3 option B) once it exists, making that worth
  building sooner than "only if needed"?
```
