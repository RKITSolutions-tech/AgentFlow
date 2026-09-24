# Task ID: 2

**Title:** Implement host execution and event streaming

**Status:** done

**Dependencies:** 1 ✓

**Priority:** high

**Description:** Implement the canonical HostExecutionProvider and reconnectable delivery of command events.

**Details:**

Follow docs/EXECUTION_PROVIDER.md. Validate working directories, filter environments, own spawned processes, enforce timeouts, terminate process groups, and stream stdout and stderr. Persist ordered events so reconnecting clients can continue from their last event position. Return exit status and support cancellation.

IMPLEMENTATION COMPLETE:
- Created app/execution/ package: base.py (ExecutionProvider ABC), models.py (ExecutionContext/Process/ProcessEvent dataclasses + raw-SQL CRUD following app/projects/models.py pattern), host.py (HostExecutionProvider)
- Added SQLite tables in app/db.py: execution_contexts, processes, process_events (event id doubles as the reconnect position for stream_output(process_id, after_id))
- Working directory validated via existing validate_repository_path/ALLOWED_PROJECT_ROOTS
- Environment filtered to safe base allowlist (PATH/HOME/LANG/LC_ALL/TERM/TZ/USER/SHELL) plus explicit overrides, not full os.environ passthrough
- Processes spawned with start_new_session=True for process-group ownership
- Process termination: stop_process/timeout both terminate via SIGTERM then SIGKILL after grace period
- Fixed critical bug: grace-period liveness check now uses popen.poll() for immediate zombie detection instead of os.kill(pid,0) which succeeded on zombies, causing unnecessary 5s delays
- All tests passing: 10/10 existing suite + new test_execution.py covering success, failure exit code, cancellation, execution timeout, path-outside-allowed-roots rejection, and event-resume-from-position

**Test Strategy:**

Integration tests in tests/test_execution.py covering: (1) successful command execution, (2) failing command with non-zero exit code, (3) cancellation, (4) execution timeout enforcement, (5) rejection of working directories outside allowed roots, (6) event-resume-from-position allowing reconnecting clients to continue from last event. All tests pass (10/10).
