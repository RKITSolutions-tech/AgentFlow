# Task ID: 9

**Title:** Implement Phase 1 document lifecycle data layer

**Status:** done

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

## Subtasks

### 9.1. Extend app/db.py SCHEMA with documents and document_refs tables

**Status:** done  
**Dependencies:** None  

Add the documents table with columns for id, project_id, repo_id, chain_id, doc_type, path, title, status, current_git_sha, supersedes_id, created_at, updated_at. Add document_refs table with composite primary key (document_id, referenced_document_id) and relationship column.

**Details:**

Modify app/db.py to add both table definitions to the SCHEMA constant. Documents table tracks document metadata and lifecycle state. Document_refs table maintains the relationship graph between documents. Use INTEGER for IDs, TEXT for enums, TIMESTAMP for date fields. Ensure foreign key constraints are properly defined with cascade behavior documented.

### 9.2. Extend agent_sessions table with document tracking columns

**Status:** done  
**Dependencies:** 9.1  

Add document_id (FK to documents) and reviewed_git_sha columns to the agent_sessions table in app/db.py SCHEMA. These columns track which document an agent session is working with and the git state at review time.

**Details:**

Update the agent_sessions table definition to include two new nullable columns: document_id (INTEGER, foreign key to documents.id) and reviewed_git_sha (TEXT). Ensure these are nullable to maintain backward compatibility with existing sessions. Add appropriate foreign key constraint for document_id.

### 9.3. Add review event types to agent_events

**Status:** done  
**Dependencies:** 9.2  

Extend the agent_events table to support three new event_type values: review_comment (with line_start, line_end, body, severity in data), review_verdict (with verdict in data), and doc_read (with document_id, git_sha in data).

**Details:**

Modify app/db.py to document the three new event_type enums. Update any event type validation or documentation to include these types. The data column in agent_events (JSON) will store the event-specific fields. No new table is needed—reuse the existing agent_events table. Document the schema for each event type's data payload.

### 9.4. Create app/documents/models.py with Document and DocumentRef dataclasses

**Status:** done  
**Dependencies:** 9.3  

Implement Document and DocumentRef dataclasses mirroring the structure established in app/projects/models.py and app/agents/models.py. Include all required fields and implement CRUD helper functions following existing raw-SQL patterns.

**Details:**

Create app/documents/models.py with: (1) Document dataclass with fields matching the documents table schema; (2) DocumentRef dataclass with document_id, referenced_document_id, relationship fields; (3) Implement CRUD helpers: create_document, update_document_status, get_document, list_documents, supersede_document, create_document_ref, get_document_refs, get_referencing_documents. Use raw SQL following the style in app/agents/models.py (not ORM). Validate status transitions: DRAFT -> IN_REVIEW -> APPROVED and DRAFT -> SUPERSEDED only.

### 9.5. Validate foreign key constraints and cascade behavior

**Status:** done  
**Dependencies:** 9.4  

Test and document cascade behavior for foreign key relationships, particularly when documents are deleted and how document_refs should be handled. Ensure referential integrity is maintained and all constraint violations are properly caught.

**Details:**

Enable foreign key constraints in SQLite (PRAGMA foreign_keys = ON). Test cascade delete scenarios: when a document is deleted, verify document_refs referencing it are handled per design intent (likely cascade delete). Test supersedes_id self-reference integrity. Create comprehensive tests covering constraint violations and proper error handling. Document the cascade behavior in code comments and ensure it matches the document lifecycle design.
