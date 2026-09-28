"""Built-in prompt content. It is *seeded* into the library at start-up; at
runtime the engine and Ralph read the library, so an edit there takes effect
and these constants are only the fallback for a library that was never seeded."""

DEFAULT_TEMPLATES = {
    "implement-task": (
        "Implement the task described below. Make the smallest change that "
        "satisfies the acceptance criteria, then stop.\n\n${vars.task}"
    ),
    "verify-acceptance": (
        "Verify each acceptance criterion below against the current code and "
        "report which pass.\n\n${vars.task}"
    ),
    # RESEARCH steps (docs/PIPELINE_ENGINE.md §23, task 43): a minimal default
    # a pipeline can reference by name (`config.prompt_template_id`) without
    # authoring its own. `ResearchAgent.build_prompt` wraps this further with
    # the repository context and the JSON reply format -- this template only
    # needs to phrase the question itself.
    "research-question": (
        "Research the following question about this project and report what "
        "you find; do not write, edit or commit anything.\n\n${vars.question}"
    ),
    # Ralph research on repeated failure (task 47): failure analysis prompt
    "ralph-research-failure": (
        "Analyze why this Ralph iteration failed to progress. You are read-only: "
        "report findings, do not edit or commit anything.\n\n${vars.question}"
    ),
}

# "when to use" text for each seeded template's `description` column
# (models.seed_defaults) -- title is the template's own `name`.
DEFAULT_TEMPLATE_DESCRIPTIONS = {
    "implement-task": "Built-in AGENT step prompt",
    "verify-acceptance": "Built-in AGENT step prompt",
    "research-question": (
        "Built-in RESEARCH step prompt -- reference by name from a RESEARCH element's "
        "config.prompt_template_id when a pipeline doesn't author its own question template."
    ),
    "ralph-research-failure": (
        "Built-in Ralph research-on-failure prompt (task 47) -- used when a Ralph run "
        "triggers optional research analysis after repeated failures to understand root causes."
    ),
}

DEFAULT_INSTRUCTIONS = (
    ("keep-minimal", "Work only on the task below and keep changes minimal."),
    ("extend-existing", "Extend existing code rather than replacing it."),
    ("no-commit", "Do not commit; AgentFlow commits after verification passes."),
    ("stop-when-done", "Stop when you believe the acceptance criteria are met; verification decides."),
)

# Generic skills (docs/AGENT_ADAPTER.md §23.2/23.3, task 54): each is created as
# an active `prompt_fragments` row by `models.seed_builtin_skills`. Content is
# grounded in what this codebase actually does today -- no invented tooling,
# no deployment/CI-CD advice (this app has no deployment pipeline of its own),
# and no web/knowledge-base research skill (docs/AGENT_ADAPTER.md §23 Phase
# B/C are proposed, not built; task 45 shipped only Phase A, repository-scoped
# research).
DEFAULT_SKILLS = [
    {
        "name": "research-ground-in-repository-context",
        "roles": ["research"],
        "adapter_types": [],
        "priority": 5,
        "author": "AgentFlow",
        "when_to_use": (
            "At the start of every RESEARCH session, before drafting findings, and again before "
            "writing the final summary."
        ),
        "constraints": [
            "No web or knowledge-base access exists in this session (docs/AGENT_ADAPTER.md §23 Phase "
            "B/C are proposed, not built) -- never claim to have looked anything up outside the "
            "repository context provided.",
            "Every finding needs a source_id pointing at a source you actually saw content from; a "
            "claim with no source is reported as unverified, not included as if it were checked.",
        ],
        "content": (
            "This session's context is repository-scoped only (app/agents/research_context."
            "RepositoryContextLoader): READMEs, CHANGELOGs, docs/**/*.md|*.rst|*.txt, and dependency "
            "manifests (requirements*.txt, pyproject.toml, package.json, etc.) are inlined in full; "
            "app/, src/, and tests/ source files are only listed as a path and size, not their "
            "content.\n\n"
            "Answer from the inlined documents first -- they are where this project records its own "
            "design decisions (e.g. docs/HIGH_LEVEL_DESIGN.md, docs/AGENT_ADAPTER.md), so a question "
            "about how or why something works is very often already answered there without needing "
            "to reason about code at all. Only reach for the listed-but-not-inlined source files when "
            "the docs don't cover it, and say plainly that you're inferring from file names/paths "
            "rather than file content in that case.\n\n"
            "Every finding you report must cite a source with a file_path and, where the excerpt "
            "supports it, a line_range -- an excerpt you paraphrase without having actually seen it "
            "is exactly the failure mode this format exists to prevent. When the provided context "
            "doesn't answer the question, say so directly in the summary rather than speculating; a "
            "clearly-flagged gap is far more useful downstream than a plausible-sounding guess. Keep "
            "the final reply to the single JSON object the caller expects: "
            '{"summary", "findings": [{"text", "source_ids", "category"}], '
            '"sources": [{"id", "file_path", "line_range", "excerpt"}]}.'
        ),
    },
    {
        "name": "web-ui-mobile-desktop-viewport-testing",
        "roles": ["implementation", "verification"],
        "adapter_types": [],
        "priority": 0,
        "author": "AgentFlow",
        "when_to_use": (
            "Before calling any new or amended user-facing page/template done, and when writing or "
            "reviewing a Playwright test for it."
        ),
        "constraints": [
            "Never wrap a Playwright assertion in `except Exception: pytest.skip(...)` -- only skip "
            "when the browser itself cannot launch (see `launch_chromium`/`launch_browser` in "
            "tests/conftest.py); swallowing a real failure as a skip hides regressions.",
            "Add every new page to tests/test_phase2_ui_verification.py's `_pages` map rather than "
            "writing a one-off standalone viewport test.",
        ],
        "content": (
            "Every new or amended user-facing page in this app is designed for mobile and desktop at "
            "the same time, not desktop-first with a mobile afterthought. Check both a narrow "
            "viewport (~375px, tests/conftest.py's MOBILE) and a desktop one (~1280px, DESKTOP) "
            "before calling a page done.\n\n"
            "Reuse app/static/app.css and existing semantic HTML first -- do not add Bootstrap or "
            "another UI dependency, and move new inline styles into CSS unless the value is truly "
            "one-off. Data tables use the shared `.table-compact` class for dense row spacing, with "
            "row-level actions in a `.row-actions` cell so they wrap on narrow screens and stay "
            "touch-sized on coarse pointers automatically. Every page renders inside the persistent "
            "shell in base.html (sidebar + main pane); colour, spacing and radii come from the design "
            "tokens at the top of app.css, never a hard-coded colour in a template. Project-scoped "
            "pages put `project_tabs(project, repo, active)` at the top of their content block; pages "
            "that own the full height (chat, terminal) set `content_class = content-fill` so they "
            "scroll internally instead of the whole page scrolling.\n\n"
            "Treat tables, modals, chat/input bars and navigation as mobile risk areas and give each "
            "an explicit responsive behavior -- below 860px the sidebar becomes a drawer and the tab "
            "strip scrolls sideways. Verify touch targets stay at least 44px "
            "(`assert_touch_target_size`, the `--tap` token) and that the page never scrolls "
            "sideways (`assert_no_horizontal_overflow` -- wide content scrolls inside its own box "
            "instead). Use `watch_console` to catch a stray console error or uncaught exception the "
            "assertions themselves wouldn't otherwise notice."
        ),
    },
    {
        "name": "pytest-conventions-and-fake-agent-scripting",
        "roles": ["implementation", "verification"],
        "adapter_types": [],
        "priority": 0,
        "author": "AgentFlow",
        "when_to_use": "Writing or reviewing tests for any app/ module, especially anything touching an AgentAdapter.",
        "constraints": [
            "A smoke test that needs a real external CLI must be skipped conditionally on the binary "
            "being absent (`@pytest.mark.skipif(shutil.which(...) is None, ...)`), never on a bare "
            "`except Exception`.",
        ],
        "content": (
            "tests/ mirrors app/'s layout module-for-module. The shared `app`/`client` fixtures "
            "(tests/conftest.py) build a fresh Flask app per test with its own tmp_path-backed SQLite "
            "database and its own `ALLOWED_PROJECT_ROOTS`, so tests never leak state into each other; "
            "a `db` fixture that does `with app.app_context(): yield get_db()` is the common pattern "
            "for exercising a models.py module directly, as in tests/prompts/test_skill_models.py and "
            "tests/prompts/test_skill_assembler.py.\n\n"
            "Prefer `FakeAgentAdapter` (app/agents/fake.py) over a real CLI for anything exercising "
            "the `AgentAdapter` interface: pass a scripted list of steps via "
            "`options={\"script\": [...]}`, each step one of `{\"action\": \"message\", \"text\": "
            "...}`, `{\"action\": \"ask\", ...}`, `{\"action\": \"write_file\", ...}`, `{\"action\": "
            "\"fail\", ...}` or `{\"action\": \"complete\"}` -- the same shape "
            "`app/agents/research_agent.scripted_report()` builds for a deterministic RESEARCH reply. "
            "This keeps a test's expected outcome fully deterministic and independent of any real "
            "model's behaviour.\n\n"
            "In-page AJAX actions (delete/stop/deprecate/etc.) are tested by posting with "
            "`headers={\"X-Requested-With\": \"XMLHttpRequest\"}` and asserting the JSON reply "
            "(`{\"status\": ...}` / `{\"error\": ...}`), not a redirect -- the same request a route "
            "handles differently for a non-JS fallback. A guarded smoke test that needs a real "
            "external CLI installed (e.g. Codex, Claude) skips via `shutil.which(...)` rather than "
            "failing when the tool is absent, so CI without those binaries still passes."
        ),
    },
    {
        "name": "code-review-correctness-checklist",
        "roles": ["verification", "general"],
        "adapter_types": [],
        "priority": 0,
        "author": "AgentFlow",
        "when_to_use": "Reviewing a diff for correctness before it's treated as done, independent of any security review.",
        "constraints": ["Advisory checklist -- nothing here is machine-enforced; use judgement on which items actually apply."],
        "content": (
            "- Does the change actually satisfy the task/acceptance criteria described, not merely "
            "look plausible next to it?\n"
            "- Edge cases: empty list/dict, `None`, zero, first/last item, and -- since this app uses "
            "plain sqlite3 rather than an ORM -- whether a write function actually calls `db.commit()` "
            "and doesn't leave a transaction open on an early return or exception.\n"
            "- Error paths raise the module's own domain exception (e.g. `LibraryError`, "
            "`PathNotAllowedError`) rather than leaking a bare `sqlite3.IntegrityError` or `KeyError` "
            "up to a route.\n"
            "- A new persisted list/dict field round-trips symmetrically through `json.dumps`/"
            "`json.loads` -- check both the write and every read path, not just one.\n"
            "- A new column or table has a matching entry in app/db.py's `_ADDED_COLUMNS` migration "
            "list (or the base `CREATE TABLE`), not just application code that assumes it exists.\n"
            "- This codebase has several hand-rolled dependency graphs (template inheritance, skill "
            "`skill_depends_on`, pipeline steps): a cycle must be rejected on write and must not hang "
            "or crash a read path that walks an already-hand-edited database -- check both directions "
            "were actually exercised, not just the happy path.\n"
            "- Do the new/changed tests actually exercise the changed branch, or would they still "
            "pass against the code as it was before this change?"
        ),
    },
    {
        "name": "code-review-security-checklist",
        "roles": ["verification", "general"],
        "adapter_types": [],
        "priority": 0,
        "author": "AgentFlow",
        "when_to_use": (
            "Reviewing any change that touches file paths, subprocess/command execution, persisted "
            "agent or command output, or AGENTFLOW_HOST/bind configuration."
        ),
        "constraints": [
            "Advisory checklist only, complementing (not replacing) this app's existing safeguards -- "
            "app/security.py's path validation, app/runs/security.py's redaction, and the loopback-"
            "only bind described in CLAUDE.md's Security Configuration section.",
            "This app has no user accounts or auth concept by design -- don't flag a missing login "
            "check as a bug; note it as a design constraint if genuinely relevant instead.",
        ],
        "content": (
            "This app has no authentication and binds to loopback by default, runs real commands, "
            "and reads/writes real files on the host on behalf of a project -- path and command "
            "handling are the highest-value area to review, not access control.\n\n"
            "- Path traversal: any new file/path input must be checked through "
            "`app/security.validate_repository_path` or the equivalent realpath-then-startswith "
            "check already used by app/runs/artifacts.py and "
            "app/prompts/assembler.resolve_context_files -- reject absolute paths and `..` segments, "
            "and resolve symlinks before trusting a path stays inside its configured root.\n"
            "- Secret handling: anything new that persists command output, agent replies, or "
            "collected files must pass through app/runs/security.py's redaction first (built-in "
            "rules plus `AGENTFLOW_REDACT_PATTERNS`); a new custom pattern should be exercised "
            "against a representative secret in a test, not just eyeballed.\n"
            "- Command construction: build argument lists, never a shell string built by "
            "concatenating untrusted input; a lightweight host check (e.g. `available()`/"
            "`version()`) uses `shutil.which`/`subprocess` directly and never `shell=True`.\n"
            "- Prompt injection surface: `${...}` variable substitution and `@path` mentions in the "
            "prompt library are deliberately resolved only against library-authored template/fragment "
            "text, not raw task/user input -- keep that separation in any new prompt-assembly code so "
            "a task description can't be used to smuggle an arbitrary file read.\n"
            "- Binding: `AGENTFLOW_HOST` must stay loopback unless `AGENTFLOW_ALLOW_UNSAFE_BIND=1` is "
            "explicitly set; don't add a code path that silently widens this."
        ),
    },
    {
        "name": "ralph-failure-analysis-pattern",
        "roles": ["implementation"],
        "adapter_types": [],
        "priority": 0,
        "author": "AgentFlow",
        "when_to_use": (
            "During a Ralph iteration retry, when the prompt includes failure evidence from a prior "
            "failed verification pipeline run."
        ),
        "constraints": [
            "Only run-level `max_iterations`/`max_runtime_seconds` exist today -- don't assume a "
            "System/Project/Sprint/Task-level override is being applied (docs/RUN_AND_RALPH.md §22).",
        ],
        "content": (
            "Ground diagnosis in exactly the evidence the retry prompt was actually built from "
            "(docs/RUN_AND_RALPH.md §22 \"Failure evidence\"): the failed step executions (their "
            "type, name, exit code, and the last 1500 characters of output -- not the full log), the "
            "files changed so far this iteration, and a short history of previous attempts. If that "
            "1500-character tail genuinely isn't enough to diagnose the failure, say what additional "
            "evidence would be needed rather than guessing beyond what was given.\n\n"
            "Use the history of previous attempts to tell whether a fix already tried is being "
            "repeated -- Ralph's own no-progress detection exists for exactly this: "
            "`identical_failure_limit` compares a normalised failure signature (failed step names + "
            "output with numbers/hashes/temp paths stripped) and `no_change_limit` compares a "
            "content-aware working-tree signature, both across the last few iterations, and blocks "
            "the run for manual review when neither is moving. A failure that repeats after a change "
            "believed to fix it almost always means the change didn't reach the failing code path -- "
            "not that the check itself is wrong.\n\n"
            "Prefer the smallest targeted fix that addresses the specific exit code/output shown over "
            "a broad rewrite -- this matches Ralph's own standing instructions (\"keep changes "
            "minimal\", \"extend existing code rather than replacing it\"). If steering guidance was "
            "left for this iteration, treat it as the most authoritative signal about what to change "
            "next, above your own read of the failure output."
        ),
    },
    {
        "name": "sqlite-persistence-conventions",
        "roles": ["implementation"],
        "adapter_types": [],
        "priority": 0,
        "author": "AgentFlow",
        "when_to_use": "Writing or extending a models.py persistence module anywhere in app/.",
        "constraints": [],
        "content": (
            "This app uses plain sqlite3 everywhere, not an ORM -- follow the conventions already "
            "established across app/prompts/models.py, app/backlog/persistence.py and "
            "app/sprints/persistence.py rather than introducing a new pattern:\n\n"
            "- A list/dict field is stored as a TEXT column via `json.dumps` and read back with "
            "`json.loads` in a small row-to-dataclass helper (e.g. `_fragment`) -- not a second child "
            "table for what's really just a bag of scalars.\n"
            "- A duplicate-name insert is caught as `sqlite3.IntegrityError` and re-raised as the "
            "module's own domain error (e.g. `LibraryError`) with a human-readable message; callers "
            "(routes, tests) should never need to catch a raw sqlite3 exception.\n"
            "- A content change bumps a `version` column and appends a row to a paired `_versions` "
            "history table (see `prompt_fragment_versions`); a metadata-only edit does neither.\n"
            "- A write function calls `db.commit()` itself at the end, so callers never need to "
            "remember to.\n"
            "- Any new dependency graph (foreign-key-shaped but application-enforced, like skill "
            "`skill_depends_on` or template inheritance) gets the same two-part guard: an explicit "
            "walk that raises before saving a direct or indirect cycle, and a separate ordering "
            "helper for read paths that is cycle-*safe* (skips an already-seen node) rather than "
            "raising, since a hand-edited database must never hang a read.\n"
            "- A new column on an existing table is added via app/db.py's `_ADDED_COLUMNS` list "
            "(`ALTER TABLE ... ADD COLUMN`), not by editing the original `CREATE TABLE IF NOT "
            "EXISTS` statement, which leaves an already-created table untouched."
        ),
    },
]
