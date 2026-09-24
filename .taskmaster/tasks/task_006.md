# Task ID: 6

**Title:** Add basic runs, history, and hardening

**Status:** pending

**Dependencies:** 3 ✓, 5 ⧖

**Priority:** high

**Description:** Add single-Task command and test Runs with inspection, persistence, recovery, and security controls.

**Details:**

Follow docs/RUN_AND_RALPH.md for the Phase 1 subset. Record step status, timing, logs, prompts, replies, and artifact metadata in SQLite while storing artifact content and large logs on the filesystem. Support pause/stop, restart reconciliation, Run history, and step inspection. Mark a Run blocked when its previously active process is missing after restart. Redact secrets before persistence where practical and require explicit configuration for unsafe all-interface binding.

**Test Strategy:**

Add an integration check that runs a command or test and verifies status, timing, output references, and artifacts. Simulate restart with a missing process and verify the Run becomes blocked. Verify secret redaction, explicit unsafe-bind configuration, and usable history/step inspection on desktop and mobile.
