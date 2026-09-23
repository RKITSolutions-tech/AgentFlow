# Task ID: 2

**Title:** Implement host execution and event streaming

**Status:** pending

**Dependencies:** 1

**Priority:** high

**Description:** Implement the canonical HostExecutionProvider and reconnectable delivery of command events.

**Details:**

Follow docs/EXECUTION_PROVIDER.md. Validate working directories, filter environments, own spawned processes, enforce timeouts, terminate process groups, and stream stdout and stderr. Persist ordered events so reconnecting clients can continue from their last event position. Return exit status and support cancellation.

**Test Strategy:**

Add one integration check covering a successful command, a failing command, and cancellation. Verify execution is limited to an allowed Project directory, child processes are terminated, and persisted output can resume from a recorded event position.
