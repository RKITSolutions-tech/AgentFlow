# Task ID: 4

**Title:** Implement Codex sessions

**Status:** pending

**Dependencies:** 3

**Priority:** high

**Description:** Integrate Codex through HostExecutionProvider using the shared AgentAdapter contract.

**Details:**

Detect Codex availability and version. Support session creation, listing, discovery and resume when supported, prompt input, live output, stop, and persisted prompt/reply history. Persistently map external Codex identifiers to AgentFlow session identifiers and report unsupported capabilities explicitly. Keep FakeAgent as the automated-test adapter.

**Test Strategy:**

Use FakeAgent for deterministic automated contract tests. Add a guarded Codex smoke check that verifies availability detection and, when Codex is installed, starting, observing, stopping, and reopening a session from persisted history.
