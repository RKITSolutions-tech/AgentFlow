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
  for automated tests. Driven by a scripted list of steps (`message`,
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
- `app/projects/` — Project and repository records; `app/security.py`
  validates repository paths against `ALLOWED_PROJECT_ROOTS`.

Testing: pytest, `tests/` mirrors `app/`'s layout. Shared fixtures
(`app`, `client`) are in `tests/conftest.py`. Guarded smoke tests that need a
real external CLI installed (e.g. Codex) use
`@pytest.mark.skipif(shutil.which(...) is None, ...)` rather than failing
when the tool is absent.

## Task Master AI Instructions
**Import Task Master's development workflow commands and guidelines, treat as if import is in the main CLAUDE.md file.**
@./.taskmaster/CLAUDE.md
