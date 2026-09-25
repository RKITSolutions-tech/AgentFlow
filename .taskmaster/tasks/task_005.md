# Task ID: 5

**Title:** Build the operational project workspace

**Status:** in-progress

**Dependencies:** 1 ✓, 2 ✓

**Priority:** medium

**Description:** Add file, search, Git, and persistent terminal tools to the Phase 1 Project workspace.

**Details:**

Provide repository-scoped file browsing and basic editing, search, Git status/diff/log/stage/unstage/commit, and a reconnectable tmux-backed terminal. Reuse Project repository validation and HostExecutionProvider boundaries. Link changed files to their diff and file view, and avoid loading large or binary files into the normal editor. Keep essential actions usable on desktop and mobile.

**Test Strategy:**

Verify file and Git operations cannot escape allowed repository roots, changed-file links open the correct diff and file, large and binary files are handled safely, and a tmux terminal survives browser disconnect and reconnect. Exercise essential actions at desktop and mobile viewport sizes.

## Subtasks

### 5.1. Implement repository-scoped file browsing and editing

**Status:** done  
**Dependencies:** None  

Build file browser UI component that displays repository directory structure with safe access controls. Implement basic file viewer and editor with safeguards against large and binary file loading.

**Details:**

Create file browser component that: (1) respects Project repository validation boundaries using existing security.py checks, (2) displays directory tree with breadcrumb navigation, (3) implements file viewer with syntax highlighting for text files, (4) detects and safely handles large files (>5MB) and binary files by preventing full load into editor, (5) supports basic editing for text files with save functionality, (6) ensures mobile viewport responsiveness, (7) integrates with HostExecutionProvider to validate all file access paths.
<info added on 2026-09-23T19:29:01.858Z>
Completed implementation of file browsing, viewing, and editing for repository-scoped workspace access. Implementation includes: (1) app/workspace/files.py with resolve_path() leveraging validate_repository_path() scoped to repository root, list_directory(), read_file() with binary detection via null-byte check and 5MB MAX_FILE_SIZE threshold, and write_file(); (2) app/projects/models.py extended with get_repository(db, project_id, repo_id); (3) app/workspace/routes.py Blueprint 'workspace' at /projects/<project_id>/repos/<repo_id>/files with /view and /edit GET+POST routes, 404s on unknown project/repo or path traversal attempts; (4) templates in app/templates/workspace/{browse,view,edit}.html with mobile-safe CSS in app.css; (5) integration linked from projects/detail.html repo table and Blueprint registered in app/__init__.py; (6) comprehensive test coverage in tests/test_workspace.py including directory listing, subdirectory navigation, view/edit round-trip, binary file rejection, oversized file rejection, path traversal rejection, and unknown repo 404s (full suite 45 tests passing); (7) manual smoke testing via curl on dev server confirmed browse/view/edit-save round trip. Next subtasks: search (5.2), Git ops (5.3), tmux terminal (5.4), final integration (5.5).
</info added on 2026-09-23T19:29:01.858Z>

### 5.2. Implement project-wide file search functionality

**Status:** done  
**Dependencies:** 5.1  

Add file search interface allowing full-text search across repository with result linking and filtering options.

**Details:**

Implement search feature that: (1) accepts search queries via UI input field, (2) executes searches via HostExecutionProvider using standard tools (grep/ripgrep), (3) displays results as clickable file:line references, (4) supports filtering by file type and path patterns, (5) caches search results briefly for performance, (6) validates all file paths remain within allowed repository roots, (7) respects .gitignore patterns when searching, (8) works efficiently on large repositories, (9) provides mobile-friendly result list display.

### 5.3. Implement Git operations (status, diff, log, staging)

**Status:** done  
**Dependencies:** 5.1  

Build Git interface supporting status view, diff visualization, commit history, and staging/unstaging workflow.

**Details:**

Create Git operations component that: (1) displays git status showing modified/untracked files, (2) implements diff viewer showing file changes with side-by-side or unified view, (3) displays commit log with author, date, message, and ability to view commit details, (4) supports staging/unstaging individual files or hunks, (5) integrates changed-file links to open diff and file view, (6) validates all file paths via existing Project repository validation, (7) executes git commands via HostExecutionProvider with proper working directory boundaries, (8) provides desktop and mobile-optimized UI for diff inspection, (9) handles merge conflicts and detached HEAD state.
<info added on 2026-09-24T13:58:15.155Z>
Git operations module implementation complete. All core functionality delivered:

Core module (app/workspace/git.py) provides status(), diff(), log(), stage(), and unstage() operations. API routes in app/workspace/routes.py expose these via GET/POST endpoints supporting AJAX. HTML templates (git_status.html, git_diff.html, git_log.html) provide desktop and mobile-optimized views with navigation between Git operations. CSS styling in app/static/app.css includes .git-nav, .diff-*, and .commit-* classes for consistent UI. JavaScript in app/static/app.js handles stage/unstage actions and smooth row removal. Test suite (tests/test_workspace_git.py) validates status checking, diff viewing, commit retrieval, and stage/unstage workflows. All file paths validated via existing security.validate_repository_path() to prevent escape attempts. Git commands executed via HostExecutionProvider with proper working directory boundaries. Implementation handles branch info display, unified diff format, commit history with author/date/message, and file staging workflow. UI responsive across desktop and mobile viewports per CLAUDE.md guidelines.
</info added on 2026-09-24T13:58:15.155Z>

### 5.4. Implement persistent tmux-backed terminal with reconnection

**Status:** done  
**Dependencies:** 5.2, 5.3  

Build reconnectable terminal UI backed by tmux sessions that survives browser disconnection and reconnection.

**Details:**

Implement persistent terminal that: (1) creates tmux sessions managed by HostExecutionProvider, (2) streams terminal output via server-sent events or WebSocket, (3) accepts user input and sends to tmux session, (4) supports multiple terminal tabs/panes within a session, (5) persists terminal session ID in browser storage and resumes on reconnect, (6) validates working directory against allowed repository roots, (7) maintains scrollback buffer accessible after reconnect, (8) supports killing/restarting tmux sessions, (9) provides mobile-friendly terminal interface with touch keyboard support, (10) respects environment filtering from HostExecutionProvider configuration.
<info added on 2026-09-24T14:28:30.912Z>
Per tmux session, persist all terminal output and events in the ProcessEvent table (session_id as key), reusing the execution/models.py event-resume pattern to allow terminal history replay after browser reconnect. Terminal user input is sent as live shell input directly to the tmux session. Support interactive control: clear screen, Ctrl+C interrupt, and session exit/kill commands. Maintain full terminal history across reconnections for seamless session resume.
</info added on 2026-09-24T14:28:30.912Z>
<info added on 2026-09-24T14:59:03.220Z>
Implemented per the approved plan (plan file: toasty-dreaming-torvalds.md). Summary of changes:

Schema: added terminal_sessions and terminal_events tables to app/db.py using idempotent CREATE TABLE IF NOT EXISTS statements (no migration framework in this repo).

app/workspace/terminal_models.py: DB CRUD operations mirroring app/execution/models.py conventions with create/get/list/set_status/touch for sessions and add/list with after_id resume for events.

app/workspace/terminal.py: drives tmux entirely through one-shot CLI commands via ExecutionProvider.execute() (new-session, send-keys -l for literal text, send-keys with allowlisted control keys, pipe-pane -o to mirror pane output to file, has-session for liveness, kill-session). No PTY support added to HostExecutionProvider itself; tmux supplies the PTY. Module-level _TerminalManager (lock-guarded dict of daemon tailer threads) tails each session's pipe-pane output file and writes ANSI-stripped chunks into terminal_events. ensure_running() lazily restarts a tailer or marks a session LOST if the tmux server is gone after an AgentFlow restart; the tmux session itself survives because it's a separate OS process.

Routes added to app/workspace/routes.py: GET/POST /terminal, GET /terminal/<id>/stream (after_id polling, text/event-stream matching app/sessions/routes.py's /stream shape), POST /terminal/<id>/input (JSON {text} or {key}), POST /terminal/<id>/kill. Every route re-validates term.repo_id == repo_id (404 on mismatch).

app/templates/workspace/terminal.html and app.css additions: plain-text scrollback (no xterm.js per explicit user decision, dependency-free per CLAUDE.md), tab bar with multiple independent tmux sessions per repo, touch-sized Ctrl+C/Tab/Esc/arrow key buttons, JS polling pattern copied from chat.html (fetch + after_id, not a real EventSource), localStorage persists only last-active tab id per repo for UX; full reconnection relies on DB-backed terminal_events history via after_id=0.

Terminal link added to the existing repeated .git-nav block in git_status.html/git_diff.html/git_log.html. browse.html and search.html integration deferred to subtask 5.5.

tests/test_workspace_terminal.py: whole file skipif(shutil.which('tmux') is None) per CodexAdapter test convention. All 8 new tests skip cleanly when tmux is absent. Full suite: 98 passed / 16 skipped / 5 failed; the 5 failures are pre-existing Playwright e2e tests unrelated to this change (net::ERR_SOCKET_NOT_CONNECTED, confirmed identical on main before this change).

Verified with Flask test client (tmux absent): GET /terminal renders correctly in empty state; POST /terminal fails gracefully with 400 JSON error when tmux is unavailable, not 500.

Known limitation: no idle-timeout auto-kill of abandoned tmux sessions yet (not required by subtask test strategy, flagged rather than silently skipped).

Pending: full manual end-to-end smoke test (create session, run command, reload page, confirm reconnection, kill/restart) and mobile/desktop visual check—blocked on tmux installation and Playwright localhost limitations. User should install tmux and perform manual pass, or request re-verification once available.
</info added on 2026-09-24T14:59:03.220Z>
<info added on 2026-09-24T15:21:10.459Z>
Fixed a critical bug discovered during manual end-to-end testing: after simulating an AgentFlow process restart (kill + restart the wsgi process while the tmux session survives), the lazily-recovered tailer thread re-read the pipe-pane output file from byte 0 instead of resuming where it left off, causing duplicate terminal_events rows on reconnect. Root cause: pipe file offset was not tracked. Solution: added pipe_offset column to terminal_sessions, atomically updated with each output chunk via new terminal_models.record_output_chunk() function (single commit ensures consistency across crash scenarios). The _tail_pipe() function now seeks to the session's last known pipe_offset on start, enabling clean tailer restarts. Added regression test test_tailer_restart_resumes_without_duplicating_output to prevent reoccurrence.

Full verification completed with tmux installed:
- tests/test_workspace_terminal.py: all 9 tests pass (8 original + 1 regression test), run 3x back-to-back with no flakiness
- Full suite: 107 passed, 8 skipped, 5 failed (same 5 pre-existing Playwright e2e failures unrelated to this change)
- No tmux session or pipe-file leaks after test runs
- Manual end-to-end smoke test against a live dev server: created session, sent command, verified output, killed and restarted AgentFlow process, confirmed tmux session survived, tailer resumed with zero duplicate events, new commands land correctly, killed session emits status event, unknown repo returns 404, terminal page tab list shows killed tab with terminal-tab-dead class

All 10 subtask requirements implemented and verified: (1) tmux sessions via HostExecutionProvider, (2) output streaming, (3) input handling, (4) multiple terminal tabs, (5) browser storage + resume-on-reconnect, (6) working-directory validation, (7) scrollback after reconnect, (8) kill/restart support, (9) mobile-friendly touch controls, (10) HostExecutionProvider env filtering

Known gap: responsive CSS layout (mobile 375px / desktop 1280px) written per existing chat-page/git-nav patterns with media query block added, but visual verification in actual browser deferred due to Playwright sandbox localhost limitation (pre-existing, also blocks other e2e tests). User should perform visual check in a real browser when time permits.
</info added on 2026-09-24T15:21:10.459Z>

### 5.5. Integrate file/Git/search/terminal into workspace UI and add integration tests

**Status:** pending  
**Dependencies:** 5.1, 5.2, 5.3, 5.4  

Combine all components into cohesive workspace interface and verify all operations respect security boundaries and work on desktop/mobile.

**Details:**

Integrate all workspace components by: (1) creating unified workspace layout combining file browser, search, Git status, diffs, and terminal, (2) ensuring navigation between views is seamless and state persists, (3) linking changed files from Git status to diff and file views, (4) implementing responsive layout that adapts to desktop and mobile viewports, (5) adding project/repository context display and switching, (6) creating comprehensive integration test suite covering: file operations cannot escape repository roots, Git operations reflect actual state, search respects boundaries, terminal working directory is validated, large/binary files handled safely, all views function on both desktop (1920x1080) and mobile (375x667) viewports, browser reconnection preserves state across all components.

### 5.9. Implement commit workflow with message editing and history persistence

**Status:** done  
**Dependencies:** 5.2, 5.3  

Build commit interface allowing users to compose commit messages, stage changes, and create commits with full history persistence.

**Details:**

Create commit component that: (1) provides text area for composing commit messages with real-time validation, (2) shows summary of staged changes before committing, (3) executes git commit via HostExecutionProvider, (4) handles commit failures with clear error messages, (5) persists commit history in AgentSession records for audit trail, (6) supports amending recent commits when appropriate, (7) validates commit message length and format, (8) implements responsive form layout for mobile devices, (9) provides quick-action buttons for common messages (e.g., 'feat:', 'fix:', 'refactor:'), (10) links commits back to Git log viewer.

### 5.7. Implement file browser and safe file loading

**Status:** done  
**Dependencies:** 5.1, 5.2  

Build repository-scoped file browsing with safety checks for large and binary files, preventing them from loading into the normal editor.

**Details:**

Create file browser UI component that: (1) displays directory tree limited to repository root, (2) validates all file paths against allowed repository roots, (3) implements file size detection to avoid loading files >5MB, (4) detects binary files using magic bytes or file extension analysis, (5) provides safe preview/download options for large/binary files, (6) implements responsive layout for mobile with collapsible tree and scrollable pane, (7) includes file metadata display (size, type, modified date), (8) handles errors gracefully when files are deleted or permissions change.
<info added on 2026-09-24T21:02:02.860Z>
Implementation complete. Directory tree component now displays file metadata columns (type, size, modified date), uses responsive CSS for mobile with collapsible tree navigation, includes file type indicators with emoji icons, and formats file sizes for readability. All unit and integration tests passing (10/10). Component validates paths against allowed repository roots and handles binary/large files safely as specified.
</info added on 2026-09-24T21:02:02.860Z>
