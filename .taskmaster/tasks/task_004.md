# Task ID: 4

**Title:** Implement Codex sessions

**Status:** done

**Dependencies:** 3 ✓

**Priority:** high

**Description:** Integrate Codex through HostExecutionProvider using the shared AgentAdapter contract.

**Details:**

Detect Codex availability and version. Support session creation, listing, discovery and resume when supported, prompt input, live output, stop, and persisted prompt/reply history. Persistently map external Codex identifiers to AgentFlow session identifiers and report unsupported capabilities explicitly. Keep FakeAgent as the automated-test adapter.

**Test Strategy:**

Use FakeAgent for deterministic automated contract tests. Add a guarded Codex smoke check that verifies availability detection and, when Codex is installed, starting, observing, stopping, and reopening a session from persisted history.

## Subtasks

### 4.1. Detect Codex availability and version

**Status:** done  
**Dependencies:** None  

Implement detection logic to check if Codex is available on the host system and determine its installed version.

**Details:**

Create a CodexAdapter class that extends AgentAdapter. Implement availability detection by attempting to import or invoke Codex. Capture and parse version information. Store availability state and version in the adapter instance. Handle cases where Codex is not installed gracefully without raising exceptions.
<info added on 2026-09-23T09:17:39.475Z>
Implemented CodexAdapter in app/agents/codex.py with available() and version() methods that detect the codex binary via shutil.which and parse 'codex --version' output (e.g., 'codex-cli 0.147.0' -> '0.147.0'), caching results on first call. Detection failures (missing binary, non-zero exit, timeout) are handled gracefully without raising exceptions, returning available()=False instead. Remaining AgentAdapter methods (discover_sessions, start, resume, send, stop, status, stream) raise NotImplementedError with pointers to subtasks 4.2-4.4 for future implementation. Tests in tests/test_codex_adapter.py cover missing binary, successful detection, caching verification, failed version command, timeout, NotImplementedError stubs, and a guarded smoke test against Codex CLI 0.147.0. All 18 tests passed.
</info added on 2026-09-23T09:17:39.475Z>

### 4.2. Implement Codex session lifecycle (create, list, resume)

**Status:** done  
**Dependencies:** 4.1  

Implement session creation, discovery/listing, and resumption capabilities through the AgentAdapter contract.

**Details:**

Add methods to CodexAdapter for: creating new Codex sessions, listing active/discoverable sessions, and resuming existing sessions. Map external Codex session identifiers to AgentFlow session IDs in a persistent mapping table. Support resumption only when Codex reports the capability. Validate that FakeAgent remains the automated-test adapter and is unaffected.

### 4.3. Implement prompt input and live output streaming

**Status:** done  
**Dependencies:** 4.2  

Add prompt input handling and real-time output streaming from Codex sessions through the AgentAdapter contract.

**Details:**

Implement prompt_input() and stream_output() methods in CodexAdapter that send prompts to active Codex sessions and receive streamed responses. Wire output streaming through HostExecutionProvider's event system to deliver to clients. Handle partial output chunks and maintain output ordering. Ensure streaming works with reconnecting clients using event position tracking.

### 4.4. Implement session stop and persisted history

**Status:** done  
**Dependencies:** 4.3  

Add session termination support and persistent storage of prompt/reply history.

**Details:**

Implement stop() method in CodexAdapter to cleanly terminate Codex sessions. Add database schema for persisting prompt/reply history keyed by (project, session_id, timestamp). Store full prompts and replies in SQLite. Implement retrieval methods to fetch history for active sessions. Ensure history persists across session restarts and is available for resume scenarios.
<info added on 2026-09-23T18:38:52.678Z>
Implemented CodexAdapter.stop(): looks up the session (ValueError if unknown), if a turn is in progress calls execution_provider.stop_process(process_id) and sets metadata.turn_finalized=True (so a later stream()/_sync_events call doesn't resurrect the killed process as an AgentError/FAILED turn), then sets session status STOPPED and appends an AgentStatus/STOPPED event, mirroring FakeAgentAdapter.stop(). Guarded send() to raise ValueError when session.status == STOPPED (can't accept further input), while resume() remains the deliberate way to reopen a stopped session. Persisted prompt/reply history: leveraged existing agent_events table (session_id, event_type, data, created_at) plus agent_sessions.project_id—no new schema needed, as this already satisfies the (project, session_id, timestamp) requirement; models.list_agent_events()/CodexAdapter.stream() are the existing retrieval path used for history-after-restart. Added FakeExecutionProvider.stop_process() test double support, replaced stale NotImplementedError test with test_stop_terminates_running_turn_and_blocks_further_input, test_stop_without_turn_in_progress_still_marks_stopped, test_stop_unknown_session_raises in tests/test_codex_adapter.py, and added test_fake_agent_stop_then_resume to tests/test_agents.py per FakeAgent contract test strategy. Full suite: 31 passed.
</info added on 2026-09-23T18:38:52.678Z>

### 4.5. Report unsupported capabilities and finalize integration

**Status:** done  
**Dependencies:** 4.4  

Explicitly report unsupported Codex capabilities and complete CodexAdapter integration with comprehensive testing.

**Details:**

Add capability reporting mechanism to CodexAdapter (e.g., supported_capabilities property or get_capabilities() method) that explicitly lists which operations are supported based on Codex version and availability. Create error handling that gracefully handles unsupported operations (resume if not available, discovery if not available). Run full contract compliance tests. Verify FakeAgent remains unchanged and functional as the automated-test adapter. Document any Codex-specific requirements.
<info added on 2026-09-23T18:55:35.524Z>
Implemented graceful degraded-mode handling in app/agents/codex.py where start()/resume()/send() now catch OSError from ExecutionProvider.execute()/start_process() (e.g., missing/unauthenticated codex binary) via a new _fail_launch() helper, emitting an AgentError event and setting FAILED status instead of allowing raw OSError to escape, matching the existing turn-failure convention. The capabilities() method left as a static adapter-type declaration (resume, session_discovery, structured_events), kept separate from available()/version() (runtime reachability checks) so unit tests don't depend on real environment detection. The discover_sessions() method already graceful (filesystem-only scan of CODEX_HOME, no subprocess call). Added comprehensive tests in tests/test_codex_adapter.py: contract-compliance test verifying CodexAdapter.__abstractmethods__ is empty, FakeExecutionProvider.fail_next_launch() helper, and 3 tests covering start/resume/send paths hitting missing-binary OSError. Documented Codex-specific requirements in docs/AGENT_ADAPTER.md section 17 (codex login auth, CODEX_HOME environment, degraded-mode behaviour). Full test suite passes (35 tests green), including guarded real-binary smoke test since codex is installed on host.
</info added on 2026-09-23T18:55:35.524Z>
