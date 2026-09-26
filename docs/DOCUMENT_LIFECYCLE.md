# AgentFlow Document Lifecycle and Review Design

## 1. Purpose

This document defines how AgentFlow should support the document-driven
delivery process already used to plan AgentFlow itself: a design
discussion is written up, reviewed across several agent sessions (Claude
and Codex both commenting), then decomposed into an implementation
document, a testing document, an implementation plan, and finally Task
Master tasks.

It answers one specific question raised while building AgentFlow: since
`DOMAIN_AND_ORCHESTRATION_DESIGN.md` §35 and `HIGH_LEVEL_DESIGN.md` §21
already say artifact content lives on the filesystem with only metadata
in SQLite, does that principle hold up for a document-heavy pipeline
where agents need to read many related documents, and where review
comments need more granularity than "the file changed"? This document
argues yes, and defines the metadata model that makes the review loop
visible.

This document does not redefine `Documentation`, `Skill`, `Artifact`, or
`Task`; those are owned by `DOMAIN_AND_ORCHESTRATION_DESIGN.md` and
`SPRINT_PLANNING_AND_BACKLOG.md`. It defines the lifecycle and review
model that sits on top of them, upstream of Backlog/Sprint, at the
Capability/Epic level of `HIGH_LEVEL_DESIGN.md` §27's delivery
decomposition.

## 2. Problem Statement

The current process (used for AgentFlow's own `docs/`) is:

```text
Design discussion
  ↓
docs/[feature]/design.md drafted
  ↓
reviewed across several fresh Claude/Codex sessions
  ↓ (repeated until stable, checking against docs/standards/*)
implementation.md drafted
  ↓
testing.md drafted
  ↓
implementation-plan.md drafted
  ↓
Task Master tasks generated
```

This works, but has no structured record of: which session reviewed
which revision of a document, what each reviewer actually flagged, which
comments were addressed versus dropped, or which standards documents a
given review pass actually consulted. The only trace is the document's
own edit history and whatever the agent said in free text.

The concern that motivated this document: if AgentFlow's persistence is
SQLite, can it still give agents access to an arbitrary, growing set of
markdown documents (design docs, standards docs, other features' design
docs) during this pipeline? The answer is that document *content* should
never have been a SQLite concern in the first place.

## 3. Governing Principle

```text
Filesystem (Git-tracked)
  owns document content, full history, diffs

SQLite
  owns document identity, lifecycle status, references between
  documents, and review activity (who reviewed what revision and said
  what)
```

This is the same split already established for Artifacts
(`DOMAIN_AND_ORCHESTRATION_DESIGN.md` §35: "Artifacts should be stored
outside SQLite, with metadata in the database") and Prompts/Replies
(`PIPELINE_ENGINE.md` §11: "transcript artifacts stored on the
filesystem, with metadata and references in SQLite"). A design/
implementation/testing/plan document is a Documentation artifact that
happens to go through an authoring and review lifecycle before it is
"done" — it does not need a different storage model, only a lifecycle
and a review record on top of the existing one.

Agents get document access the same way they already get source access:
through the repository file browsing and project-wide search built for
task 5.1/5.2 (`app/projects/routes.py`), not through a DB blob. Nothing
about SQLite blocks an agent from reading fifteen related markdown files
in a repo — that was never the constraint.

## 4. Document Types

```text
DESIGN
IMPLEMENTATION
TESTING
PLAN
STANDARD
```

`DESIGN`, `IMPLEMENTATION`, `TESTING` and `PLAN` normally form a chain
for one feature/capability (mirroring `HIGH_LEVEL_DESIGN.md` §27:
Capability → Epic). `STANDARD` documents (coding standards, UI rules,
architecture decisions) are project-scoped and cross-cutting — referenced
by many chains, not part of one.

This mirrors the distinction already drawn in
`SPRINT_PLANNING_AND_BACKLOG.md` §12 between Project documentation
(`architecture.md`, `coding-standards.md`) and Sprint/Task documentation
(`job-redesign.md`, `photo-upload.md`): `STANDARD` documents sit at the
Project level, `DESIGN`/`IMPLEMENTATION`/`TESTING`/`PLAN` sit at the
feature level below it.

## 5. Document Status

```text
DRAFT
IN_REVIEW
APPROVED
SUPERSEDED
```

Reusing the provenance vocabulary already established for
`PlanningProposal` (`SPRINT_PLANNING_AND_BACKLOG.md` §15: DRAFT / REVIEW
/ APPROVED / REJECTED / SUPERSEDED) rather than inventing a second one.
`IN_REVIEW` corresponds to that document's `REVIEW`; a rejected document
returns to `DRAFT` rather than needing its own terminal state, since
document authoring is normally iterate-in-place rather than
reject-and-restart.

A document reaching `APPROVED` is what unblocks the next document in the
chain — the implementation doc should not be drafted against a design
doc that is still `IN_REVIEW`.

## 6. Document Entity

```text
id
project_id
repo_id
chain_id            -- groups DESIGN → IMPLEMENTATION → TESTING → PLAN for one feature; NULL for STANDARD
doc_type            -- DESIGN | IMPLEMENTATION | TESTING | PLAN | STANDARD
path                -- relative path in the repository
title
status              -- DRAFT | IN_REVIEW | APPROVED | SUPERSEDED
current_git_sha     -- blob/commit sha of the reviewed content; bumped on every content change
supersedes_id       -- previous Document this replaces, if any
created_at
updated_at
```

A `STANDARD` document has `chain_id = NULL` and is referenced rather
than superseded in the normal case; it still carries `current_git_sha`
so a review comment can be pinned to the revision it was actually read
against.

## 7. Document References

```text
document_id           -- the document doing the referencing
referenced_document_id -- the document (STANDARD or otherwise) it depends on
relationship           -- DERIVES_FROM | REFERENCES | SUPERSEDES
```

`DERIVES_FROM` expresses the chain (implementation.md derives_from
design.md). `REFERENCES` expresses a standards or cross-feature
citation (implementation.md references coding-standards.md, or
references another feature's design.md for an integration point).

When a pipeline stage starts, its document's `REFERENCES` +
`DERIVES_FROM` edges resolve to a flat list of `(repo, path, git_sha)`
handed to the agent as required reading — the same resolution pattern
already used for `task_document_refs` / `sprint_document_refs` in
`SPRINT_PLANNING_AND_BACKLOG.md` §41, just one level up the
decomposition, and it costs nothing extra: it's a join, not a content
copy.

## 8. Review Activity

A review pass is one agent session reading a document (at a specific
`current_git_sha`) and responding. This does not need a new table family
— it is an `AgentSession` (already scoped to a Project, already logging
`AgentEvent`s) with two additions:

```text
agent_sessions.document_id   -- nullable FK; which Document this session reviewed
agent_sessions.reviewed_git_sha -- the sha reviewed, captured at session start
```

Review comments are `AgentEvent` rows with new `event_type` values:

```text
review_comment   data = {"line_start":42,"line_end":48,"body":"...","severity":"blocking|note"}
review_verdict   data = {"verdict":"approved|changes_requested"}
doc_read         data = {"document_id":7,"git_sha":"..."}   -- logged whenever the agent pulls in a referenced doc beyond the one it's reviewing
```

This gives exactly the granularity the current ad hoc process lacks:
"which session, reviewing which revision, flagged what, and did it
approve or request changes" is a query over `agent_events` joined to
`agent_sessions`, not something reconstructed by re-reading a diff. It
also composes with `PIPELINE_ENGINE.md` §11's requirement that prompts
and replies are always persisted — a review session's transcript is
already captured for free once Codex/Claude sessions are backed by
`AgentSession`.

## 9. Review Loop

```text
Document DRAFT
  ↓
author session drafts/revises content, commits
  ↓
Document → IN_REVIEW, current_git_sha updated
  ↓
review session(s): one per reviewing agent, each a fresh AgentSession
  tied to the document at this git_sha
  ↓
each review session emits review_comment* and one review_verdict
  ↓
any changes_requested
  → back to author session against the same Document (still IN_REVIEW)
all approved
  → Document → APPROVED
  ↓
next document in the chain may now start (its DERIVES_FROM target is
now APPROVED)
```

Phase 1: this loop is driven manually — a person starts each review
session and flips status via the UI, the same way sessions are
started/stopped today. Phase 2, once `PIPELINE_ENGINE.md`'s Pipeline
Engine exists, this same loop can run as an ordinary pipeline (author
`AgentStep` → N reviewer `AgentStep`s → `ManualReviewStep` gate),
identical in shape to the design-mockup review pattern already specified
in `PIPELINE_ENGINE.md` §16. Nothing in this document requires Pipeline
Engine to exist first; it only requires `AgentSession`/`AgentEvent`,
which already exist.

## 10. Chain Completion → Task Master

When a `PLAN` document reaches `APPROVED`, it becomes the input to the
existing Task Generation flow (`SPRINT_PLANNING_AND_BACKLOG.md` §18):
the plan is decomposed into Tasks, each carrying a `task_document_refs`
entry back to the `PLAN` document (and transitively, via `DERIVES_FROM`,
to the `IMPLEMENTATION`/`TESTING`/`DESIGN` documents above it). This is
also the point at which Task Master itself is invoked for AgentFlow's
own delivery today; for projects AgentFlow manages, the same step should
call the Planning Agent (`SPRINT_PLANNING_AND_BACKLOG.md` §31) rather
than assume Task Master specifically.

This document does not change Task Generation; it only guarantees that
by the time a Task exists, its full document chain is `APPROVED` and
traceable.

## 11. Database Entities

Likely additions to `app/db.py`'s `SCHEMA`:

```text
documents
document_refs
```

Likely additions to existing tables:

```text
agent_sessions.document_id
agent_sessions.reviewed_git_sha
```

No new table is needed for review comments; they are `agent_events`
rows with the `event_type` values in §8.

## 12. What This Does Not Solve

- Keeping `current_git_sha` accurate requires a write-time hook wherever
  the app edits a tracked document (or a reconcile step); this document
  assumes that hook exists but does not design it.
- Line-anchored comments (`line_start`/`line_end`) go stale if the
  document is edited between review passes without bumping
  `current_git_sha` first; the review loop in §9 must enforce
  re-review-on-change rather than silently reusing stale line numbers.
- `STANDARD` documents living outside a fixed folder name (this repo has
  no `docs/standards/`; other projects the user works on do) means
  `Document.path` must be freely chosen per project, not defaulted to a
  convention — confirmed against this repo, which has no such directory.

## 13. Phase Placement

Phase 1: `documents` + `document_refs` tables, the two `agent_sessions`
columns, and the four `agent_events` event types — enough to make the
review loop queryable manually through the existing session UI. This is
additive to P1.9 (persistence) and does not require Pipeline Engine.

Phase 2: wiring the review loop into Pipeline Engine as a reusable
`AgentStep`/`ManualReviewStep` pipeline once `PIPELINE_ENGINE.md` P2.4
lands, and surfacing chain status in Sprint Documentation (§12 of
`SPRINT_PLANNING_AND_BACKLOG.md`).

## 14. Open Questions

Proposed answers to all three are recorded in `PHASE2_PLANNING.md` §4, along
with the status of the §13 Phase 1 slice.

```text
Should STANDARD documents be versioned the same way (SUPERSEDED chain)
  or treated as always-current with edit history left entirely to Git?
Does a review_verdict of changes_requested need to name which specific
  review_comment rows it's blocking on, or is "any blocking comment
  unresolved" sufficient to keep a Document out of APPROVED?
Should doc_read events be deduplicated per session, or is one row per
  read acceptable given expected volume?
```
