# Task ID: 7

**Title:** Review Phase 1 design decisions and consolidate Phase 2 planning

**Status:** pending

**Dependencies:** 1 ✓, 2 ✓, 3 ✓, 4 ✓, 5 ⧖, 6

**Priority:** medium

**Description:** Audit all design documents (AGENT_ADAPTER.md, EXECUTION_PROVIDER.md, HIGH_LEVEL_DESIGN.md, TASK_INTELLIGENCE.md, DOCUMENT_LIFECYCLE.md, and implementation notes) to identify deferred decisions and open questions from Phase 1 work, then consolidate findings into Phase 2 planning artifacts.

**Details:**

Systematically review:

1. docs/AGENT_ADAPTER.md section 22 and surrounding context regarding project-level context files (analogous to CLAUDE.md), on-demand context file surfacing, and the three open design questions:
   - Where do project-level context files live and how are they discovered?
   - How are on-demand context files surfaced to agents?
   - Should assembly of context be AgentFlow-side or delegated to adapters?

2. docs/EXECUTION_PROVIDER.md for deferred considerations or unresolved architectural points.

3. docs/HIGH_LEVEL_DESIGN.md for deferred features or design uncertainties.

4. docs/TASK_INTELLIGENCE.md, particularly section 9, which lists open questions regarding the Task Master-inspired requirement-elaboration, decomposition, and dependency-provenance design (backlog free text -> AI-elaborated PlanningProposal -> reviewed Task, AI-suggested dependency edges tagged and gated behind review, complexity-driven decomposition folded into Planning Profile selection). Document which open questions from section 9 have been resolved during Phase 1 and which remain for Phase 2.

5. docs/DOCUMENT_LIFECYCLE.md, particularly section 14 Open Questions (STANDARD document versioning, whether review_verdict needs to name specific blocking review_comment rows, doc_read event dedup). Note that section 13 already scopes its own Phase 1 schema slice (documents/document_refs tables, agent_sessions.document_id/reviewed_git_sha columns, review_comment/review_verdict/doc_read agent_events types) into a separate new task rather than this review task. This task's job is only to audit DOCUMENT_LIFECYCLE.md's open questions and confirm nothing else from it is being lost, not to implement it.

6. All update-subtask notes from tasks 1-6 implementation (accessible via task-master show 1.1, 1.2, etc.) to capture any additional points flagged but postponed during Phase 1 work.

7. Consolidate findings into one of the following outcomes:
   - Update docs/PHASED_DELIVERY_PLAN.md Phase 2 feature list to include newly clarified requirements.
   - Update docs/TASK_INTELLIGENCE.md with resolved open questions and rationale.
   - Update docs/DOCUMENT_LIFECYCLE.md with resolved open questions from section 14 and rationale.
   - Create a dedicated Phase 2 design task if significant unresolved architectural questions warrant focused design work before implementation.

Ensure no raised concerns or deferred decisions from Phase 1 are lost; create a single source of truth for Phase 2 direction.

**Test Strategy:**

Verification involves:

1. Confirm all sections of AGENT_ADAPTER.md, EXECUTION_PROVIDER.md, HIGH_LEVEL_DESIGN.md, TASK_INTELLIGENCE.md, and DOCUMENT_LIFECYCLE.md have been read and reviewed.

2. Verify that section 9 of docs/TASK_INTELLIGENCE.md has been examined and all listed open questions have been addressed (either resolved, incorporated into Phase 2 plans, or explicitly deferred with justification).

3. Verify that section 14 of docs/DOCUMENT_LIFECYCLE.md has been examined and all listed open questions have been addressed (either resolved, incorporated into Phase 2 plans, or explicitly deferred with justification). Confirm that the Phase 1 schema scope in section 13 is being handled separately and not duplicated here.

4. Verify that update-subtask notes from tasks 1-6 subtasks have been examined for deferred points (run task-master show 1.1 through 6.* and review all notes).

5. Ensure docs/PHASED_DELIVERY_PLAN.md Phase 2 section has been updated with newly identified requirements and that docs/TASK_INTELLIGENCE.md and/or docs/DOCUMENT_LIFECYCLE.md have been updated with resolved questions, OR that a new Phase 2 design task has been created with clear design questions.

6. Create a summary document or task describing:
   - All open questions identified (e.g., the three context file questions from AGENT_ADAPTER.md, those from TASK_INTELLIGENCE.md section 9, and those from DOCUMENT_LIFECYCLE.md section 14).
   - Which questions have been resolved or incorporated into Phase 2 plans.
   - Which questions remain open and require design discussion.
   - Rationale for deferrals to Phase 2 or beyond.

7. Verify the summary is added to the Phase 2 design task or planning document so Phase 2 implementation tasks can reference and resolve them.
