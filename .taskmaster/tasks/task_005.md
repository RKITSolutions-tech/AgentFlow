# Task ID: 5

**Title:** Build the operational project workspace

**Status:** pending

**Dependencies:** 1, 2

**Priority:** medium

**Description:** Add file, search, Git, and persistent terminal tools to the Phase 1 Project workspace.

**Details:**

Provide repository-scoped file browsing and basic editing, search, Git status/diff/log/stage/unstage/commit, and a reconnectable tmux-backed terminal. Reuse Project repository validation and HostExecutionProvider boundaries. Link changed files to their diff and file view, and avoid loading large or binary files into the normal editor. Keep essential actions usable on desktop and mobile.

**Test Strategy:**

Verify file and Git operations cannot escape allowed repository roots, changed-file links open the correct diff and file, large and binary files are handled safely, and a tmux terminal survives browser disconnect and reconnect. Exercise essential actions at desktop and mobile viewport sizes.
