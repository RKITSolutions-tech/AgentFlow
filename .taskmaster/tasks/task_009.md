# Task ID: 9

**Title:** Implement Phase 1 document lifecycle data layer

**Status:** pending

**Dependencies:** 1 ✓, 3 ✓, 4 ✓

**Priority:** medium

**Description:** Add documents and document_refs tables to the SQLite schema, extend agent_sessions with document tracking columns, and implement document CRUD helpers and dataclasses following existing app patterns.

**Details:**

1. Extend app/db.py SCHEMA with two new tables:
   - documents: id (PK), project_id (FK), repo_id (FK), chain_id, doc_type [DESIGN|IMPLEMENTATION|TESTING|PLAN|STANDARD], path, title, status [DRAFT|IN_REVIEW|APPROVED|SUPERSEDED], current_git_sha, supersedes_id (self-FK, nullable), created_at, updated_at
   - document_refs: document_id (FK), referenced_document_id (FK), relationship [DERIVES_FROM|REFERENCES|SUPERSEDES], primary key (document_id, referenced_document_id)

2. Extend agent_sessions table with two new nullable columns:
   - document_id (FK to documents)
   - reviewed_git_sha

3. Add three new event_type values to agent_events (reuse existing table, no new table):
   - review_comment with data={line_start, line_end, body, severity}
   - review_verdict with data={verdict}
   - doc_read with data={document_id, git_sha}

4. Create app/documents/models.py following the pattern established in app/projects/models.py and app/agents/models.py:
   - Define Document and DocumentRef dataclasses mirroring agent_sessions/agent_events structure
   - Implement CRUD helpers: create_document, update_document_status, get_document, list_documents, supersede_document, create_document_ref, get_document_refs, get_referencing_documents
   - Use raw SQL following the existing style in app/agents/models.py (add_agent_event, create_agent_session)
   - Ensure document status transitions follow DRAFT -> IN_REVIEW -> APPROVED and DRAFT -> SUPERSEDED paths

5. Validate foreign key constraints and cascade behavior (e.g., deleting a document should handle document_refs appropriately per design intent).

6. This is data-layer only—no UI, no pipeline wiring, no review-loop automation per DOCUMENT_LIFECYCLE.md section 13.

**Test Strategy:**

1. Write pytest tests in tests/test_documents.py mirroring the structure of tests/test_projects.py:
   - Test document creation with all required and optional fields
   - Test status transitions: DRAFT -> IN_REVIEW -> APPROVED, and DRAFT -> SUPERSEDED
   - Verify supersedes_id chain linkage and lookup
   - Test document_refs creation and multi-hop chain traversal
   - Test get_referencing_documents to find reverse refs

2. Test agent_sessions/agent_events integration:
   - Create an agent_session with document_id and reviewed_git_sha
   - Add review_comment, review_verdict, and doc_read events
   - Round-trip session and events from database and verify columns and event data persist
   - Verify event type filtering and data field access

3. Test CRUD helpers:
   - Create multiple documents, list them, filter by project/repo/status
   - Update document status and verify transitions
   - Verify foreign key constraints (project_id and repo_id point to valid projects/repositories)
   - Test nullable fields (chain_id, supersedes_id, document_id in agent_sessions, reviewed_git_sha)

4. Run full test suite and verify no schema migration errors or constraint violations.
