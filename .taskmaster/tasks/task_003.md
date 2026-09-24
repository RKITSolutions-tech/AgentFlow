# Task ID: 3

**Title:** Create the agent contract and FakeAgent vertical slice

**Status:** done

**Dependencies:** 1 ✓, 2 ✓

**Priority:** high

**Description:** Define the shared AgentAdapter contract and prove it with a deterministic FakeAgent end-to-end workflow.

**Details:**

Follow docs/AGENT_ADAPTER.md. Implement scripted FakeAgent messages, file changes, failure, retry, and success. Complete the vertical slice: open a Project, start FakeAgent, stream output, modify a fixture file, show its Git diff, run a configured test, and persist prompts, replies, events, session metadata, and the result.

**Test Strategy:**

Add one deterministic end-to-end check that runs without an external model and proves the full Project-to-FakeAgent-to-diff-to-test-to-persisted-result flow, including a scripted failure and retry.
