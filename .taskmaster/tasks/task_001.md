# Task ID: 1

**Title:** Build the application and project foundation

**Status:** done

**Dependencies:** None

**Priority:** high

**Description:** Create the host-run Flask application shell, SQLite persistence, responsive navigation, and Project/repository management.

**Details:**

Follow docs/HIGH_LEVEL_DESIGN.md and docs/DOMAIN_AND_ORCHESTRATION_DESIGN.md. Bind to loopback by default. Support creating, viewing, editing, and listing Projects. Each Project may contain one or more validated repository paths and one default repository. Reject paths outside configured allowed roots and persist records across restarts.

**Test Strategy:**

Start the application on the host and verify loopback binding. Add one automated check that creates and reloads a Project, accepts a repository under an allowed root, and rejects a path outside allowed roots.
