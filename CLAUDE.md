# Claude Code Instructions

## Architecture

AgentFlow is a Python/Flask app backed by SQLite. Design docs live in `docs/`
(read `docs/HIGH_LEVEL_DESIGN.md` for the overview; the two most relevant to
day-to-day implementation are `docs/AGENT_ADAPTER.md` and
`docs/EXECUTION_PROVIDER.md`). Delivery is sequenced in
`docs/PHASED_DELIVERY_PLAN.md` and tracked via Task Master (`.taskmaster/`).

Key modules:

- `app/agents/base.py` — the `AgentAdapter` ABC. Every coding-agent
  integration (Codex, Claude, Gemini, OpenCode, FakeAgent) implements this
  same interface; nothing else in the app depends on agent-specific CLI
  behaviour.
- `app/agents/models.py` — persistence for `AgentSession`/`AgentEvent`/
  `AgentResult` (SQLite). Adapters store all session state here (metadata,
  cursors, external session IDs) rather than in adapter instance memory, so
  `resume()` can reconstruct a session purely from the database.
- `app/agents/fake.py` — `FakeAgentAdapter`, the deterministic adapter used
  for automated tests. Driven by a scripted list of steps (`message`, `ask`,
  `write_file`, `fail`, `complete`) passed via `options["script"]`.
- `app/agents/codex.py` — `CodexAdapter`, the first real adapter. Being
  built incrementally across Task Master task 4's subtasks; methods not yet
  implemented raise `NotImplementedError` naming the subtask that will add
  them.
- `app/execution/base.py` — the `ExecutionProvider` ABC (`HostExecutionProvider`
  today, `DockerExecutionProvider` later). Adapters request process
  execution through this abstraction (per `docs/AGENT_ADAPTER.md` §13)
  rather than calling `subprocess` directly — except lightweight,
  project-independent host checks like binary/version detection
  (`CodexAdapter.available()`/`version()`), which aren't tied to a
  Project's execution context and use `shutil.which`/`subprocess` directly.
- `app/execution/host.py` + `app/execution/models.py` — process/context
  lifecycle and event persistence for host-executed commands.
- `app/runs/` — Phase 1 Runs (docs/RUN_AND_RALPH.md): a Run is an ordered
  list of command Steps executed through the `ExecutionProvider` by
  `RunManager` (`executor.py`, one instance at
  `app.extensions["run_manager"]`, so pause/stop from later requests reach the
  worker thread). Status, timing, events and artifact metadata live in SQLite
  (`models.py`); artifact content and full logs live on disk under
  `AGENTFLOW_ARTIFACT_DIR` (default `artifacts/` beside the database,
  `artifacts.py`, path-traversal checked). `RunManager.reconcile()` runs at app
  start: a RUNNING/PAUSED Run whose process is gone becomes BLOCKED.
  `security.py` redacts secrets before anything is persisted (built-in rules
  plus `AGENTFLOW_REDACT_PATTERNS`, a JSON list of regexes); the unredacted
  command is held in memory only, so a redacted step cannot be restarted.
- `app/backlog/` — Phase 2 Backlog (docs/SPRINT_PLANNING_AND_BACKLOG.md §6-7):
  `models.py` (dataclasses, status/priority constants, `TRANSITIONS`),
  `persistence.py` (sqlite3 CRUD; `transition()` enforces the map and writes
  `backlog_triage_history`), `attachments.py` (uploads stored under
  `<artifact root>/backlog/<project>/<item>/`, filenames sanitised and paths
  re-checked with the same helpers as Run artifacts). `sprint_id` has no FK
  until the Sprints table exists.
  `views.py` is the Backlog UI (inbox, triage, sprint items, item page; AJAX
  via the shared `data-ajax-*` handlers in `app.js`).
- `app/sprints/` — Phase 2 Sprints (docs/SPRINT_PLANNING_AND_BACKLOG.md §49):
  `persistence.py` (sprints, planned tasks + dependencies, readiness rows,
  approval history), `workflow.py` (keeps Sprint and Backlog statuses in step;
  select → plan → review → approve), `planning_agent.py` (a PLANNING-role
  `AgentAdapter` session whose JSON reply becomes SUGGESTED tasks;
  `AGENTFLOW_PLANNING_AGENT=fake` for deterministic runs), `readiness.py`
  (explicit pass/fail/warn checks).
- `app/pipelines/` — Phase 2 Pipeline Engine (docs/PIPELINE_ENGINE.md §22):
  JSON definitions (`schema.py`, `validator.py`, `composer.py`), versioned
  storage (`persistence.py`, built-ins in `definitions/*.json` seeded at start),
  the re-entrant `PipelineEngine` (`engine.py`; persisted cursor, retries,
  compensation, manual pause/resume, teardown) and `PipelineManager` threads
  (`app.extensions["pipeline_manager"]`), `visualization.py` graph JSON and the
  execution view (`views.py`, `static/pipeline.js`).
- `app/ralph/` — Ralph iteration loop (docs/RUN_AND_RALPH.md §22):
  `orchestrator.py` runs agent work → collect changes → verification pipeline →
  failure analysis, with steering, no-progress blocking and local auto commit;
  pause/cancel/steer are DB flags; `manager.py` is `ralph_manager`.
- `app/artifacts/` — Artifact Library (docs/PIPELINE_VISUALISATION.md §30):
  step logs and `config.collect` files are indexed with their originating step;
  search, tags, text/byte comparison.
- `app/acceptance/` — acceptance criteria, templates, evidence suggestion and
  the Ralph completion gate (docs/SPRINT_PLANNING_AND_BACKLOG.md §49).
- `app/sprints/queue.py` — Sprint execution queue (docs/SPRINT_PLANNING_AND_BACKLOG.md §49):
  `planned_work_items.task_state` follows the canonical Task states; release, eligibility,
  `promote_next` (creates the Ralph run) and `sync_from_run` (called by the orchestrator).
- `app/projects/lock.py` — per-project execution lock (`project_locks`, docs/RUN_AND_RALPH.md
  §22): managers acquire in `start()` and a `Heartbeat` thread holds it; stale locks are taken
  over or swept; startup releases all orphans.
- `app/pipelines/replay.py` — historical replay: `Replayer` folds `pipeline_events` over the
  stored steps (no re-execution); `ReplayController` is the playhead; routes under
  `.../executions/<id>/replay/`.
- `app/prompts/` — prompt library (fragments, templates with inheritance, Ralph instruction
  blocks, recorded `execution_prompts`); the pipeline engine and Ralph read it at runtime and
  `defaults.py` only seeds it.
- `app/projects/` — Project and repository records; `app/security.py`
  validates repository paths against `ALLOWED_PROJECT_ROOTS`.

Testing: pytest, `tests/` mirrors `app/`'s layout. Shared fixtures
(`app`, `client`) are in `tests/conftest.py`. Guarded smoke tests that need a
real external CLI installed (e.g. Codex) use
`@pytest.mark.skipif(shutil.which(...) is None, ...)` rather than failing
when the tool is absent.

## Security Configuration

- AgentFlow binds to loopback only. `AGENTFLOW_HOST` set to any other address
  is refused at startup unless `AGENTFLOW_ALLOW_UNSAFE_BIND=1` is also set,
  and a warning is logged when it is (the app runs commands on the host and
  has no authentication).
- `AGENTFLOW_REDACT_PATTERNS` (JSON list of regexes) adds to the built-in
  secret redaction applied to Run commands, logs, replies and collected files.
- `AGENTFLOW_ARTIFACT_DIR` relocates Run artifact storage.

## Temporary UI and Mobile Rules

Until the UI direction settles, every new or amended user-facing page should:

- Design for mobile and desktop at the same time; check narrow screens around
  375px and desktop around 1280px before calling the page done.
- Reuse `app/static/app.css` and existing semantic HTML first. Do not add
  Bootstrap or another UI dependency unless repeated components make the
  smaller local CSS path worse.
- Keep touch targets easy to tap, forms usable without horizontal scrolling,
  and long paths/output wrapped or scrollable.
- Treat tables, modals, chat/input bars and navigation as mobile risk areas;
  give each an explicit responsive behavior when touched.
- Move new inline styles into CSS unless the value is truly one-off.
- Data tables use the shared `.table-compact` class (`app/static/app.css`)
  for dense row spacing instead of ad hoc per-page padding/font-size rules;
  put row-level actions in a `.row-actions` cell so they wrap on narrow
  screens and stay touch-sized on coarse pointers automatically.
- Every page renders inside the persistent shell in `base.html` (sidebar +
  main pane; `app/shell.py` supplies the sidebar data). Colour, spacing and
  radii come from the design tokens at the top of `app/static/app.css`
  (dark by default, `[data-theme="light"]` override) — never hard-code a
  colour in a template or rule.
- Project-scoped pages put `project_tabs(project, repo, active)` (from
  `_project_tabs.html`; repository pages reach it through `workspace_nav`) at
  the top of their content block. Pages that own the full height (chat,
  terminal) also set `{% block content_class %} content-fill{% endblock %}`
  so they scroll internally.
- Below 860px the sidebar becomes a drawer and the tab strip scrolls
  sideways; keep touch targets at least 44px (`--tap`).
- Playwright viewport tests must only skip when the browser cannot launch
  (see `_new_page` in `tests/test_workspace_integration.py`); never wrap
  assertions in `except Exception: pytest.skip(...)`, which hides failures.
- Browser checks live in `tests/test_phase2_ui_verification.py`; add new pages to its
  `_pages` map and use the helpers in `tests/conftest.py` (`assert_touch_target_size`,
  `assert_no_horizontal_overflow`, `watch_console`).
- In-page actions on a list/table (delete, stop, etc.) should be AJAX, not a
  full-page form post + redirect: mark the trigger with
  `data-ajax-action="<url>"` (plus optional `data-confirm="..."` and
  `data-on-removed="<event-name>"`) per the delegated handler in
  `app/static/app.js`, and have the row carry `data-row` so it's removed
  from the DOM on success. Routes backing these detect the AJAX call via the
  `X-Requested-With: XMLHttpRequest` header and return JSON
  (`{"status": ..., "message": ...}` / `{"error": ...}`) instead of
  redirecting; keep the redirect/flash behavior for any non-JS fallback.
  Reserve full-page redirects for actions that genuinely navigate away
  (e.g. deleting the thing the current page is about).

## Task Master AI Instructions
**Import Task Master's development workflow commands and guidelines, treat as if import is in the main CLAUDE.md file.**
@./.taskmaster/CLAUDE.md
