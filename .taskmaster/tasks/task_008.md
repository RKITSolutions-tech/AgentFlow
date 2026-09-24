# Task ID: 8

**Title:** Fix 5 hardcoded Playwright UI tests to work without pre-started dev server

**Status:** pending

**Dependencies:** 3 ✓, 5 ⧖, 6

**Priority:** medium

**Description:** Repair 5 failing Playwright tests that hardcode http://127.0.0.1:5000 and require a manually-started Flask server, causing false failures in pytest runs without external setup. Make them self-contained by either spinning up an ephemeral Flask server fixture or guarding them with a skip check.

**Details:**

Investigate why tests/test_chat_message.py::test_send_and_display_chat_message, tests/test_chat_message.py::test_chat_form_input_and_state, tests/test_codex_chat_response.py::test_codex_message_and_response, tests/test_hello_codex.py::test_send_hello_get_codex_response, and tests/test_hello_message.py::test_send_hello_message_via_ui were written against a live external server instead of using the Flask test client fixture pattern documented in CLAUDE.md.

Choose one of two approaches:

(a) Self-contained server fixture: Create a session-scoped pytest fixture (or conftest helper) that:
  - Spawns a Flask dev server in a subprocess using multiprocessing or threading
  - Binds to an ephemeral port (0) and discovers the assigned port
  - Passes the server URL (e.g., http://127.0.0.1:<dynamic-port>) to Playwright tests via pytest parametrization or indirect fixture injection
  - Tears down the server after the test session completes
  - Ensures the server is reachable before tests run (wait with retry/timeout)

(b) Skip guard (if integration with live server is essential):
  - Add a pytest skip check following the existing shutil.which(...) convention in CLAUDE.md
  - Create a helper function that attempts to connect to http://127.0.0.1:5000 with a short timeout (e.g., socket or requests.head)
  - Decorate each test with @pytest.mark.skipif(not is_server_reachable(), reason="Flask dev server not running on port 5000")
  - Ensure the skip is clean and does not report as a failure

Prefer approach (a) if Playwright automation and end-to-end UI verification are core to the test strategy; use (b) only if there is a genuine reason these tests must exercise a live running server rather than a fixture-managed one.

Update the hardcoded base URL references in all 5 tests to use the dynamically assigned URL (via fixture or conftest variable).

**Test Strategy:**

Run pytest from a clean state with no pre-started Flask dev server on port 5000 and verify:
  1. All 5 tests pass (or skip cleanly if using approach b)
  2. No timeout errors or Page.wait_for_selector failures for #chatContainer
  3. Full pytest run reports 0 failures from these test files
  4. If using approach (a), confirm the ephemeral server is started before tests and cleaned up after
  5. If using approach (b), confirm tests skip gracefully and pytest exit code is 0
  6. Run pytest with -v to show each test result and confirm the expected behavior
