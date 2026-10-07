# Session History and Chat Context

## 1. Purpose

This document defines the design for a per-project session history and chat context feature in AgentFlow. The system captures, summarizes, and presents developer conversations scoped to individual repositories, enabling quick context recovery and fast project switching across single and multi-machine deployments.

## 2. Problem Statement

### Current State

- AgentFlow tracks agent sessions, runs, and execution history, but does not persistently capture general developer-to-agent conversations.
- Developers must reconstruct context by reviewing scattered agent session logs, run artifacts, and mental notes.
- When working across multiple projects or machines simultaneously, there is no central index of recent conversations per project.
- Switching between projects requires context re-loading from disparate sources.

### Desired Outcome

- Every project has a conversational history accessible from the project view.
- Recent chat (last 24h) is immediately visible; older conversations are archived as commit-message-style summaries.
- Conversations are filtered to exclude purely operational sessions (agent spawns, pipeline runs, Ralph iterations) so the chat surface stays focused on human intent.
- A developer can select a project, see what they discussed recently, and jump back into context quickly.
- Chat history syncs across machines so work is visible regardless of which AgentFlow instance initiated the session.

## 3. Design Principles

### 3.1 Conversation-scoped to Repository

Chat and session history are bound to a project's repository, not to an agent or run. A conversation may involve multiple agents or runs, but is recorded as a single conversational thread tied to the project.

### 3.2 Operational Sessions Are Excluded

Chat history surfaces human intent and discovery. Sessions that are purely operational (agent spawning, autonomous Ralph loops, pipeline choreography) are not included. Examples:
- Ralph autonomous run iterations → excluded (captured in Run/Ralph history)
- Agent session spawning without explicit developer message → excluded
- Pipeline execution choreography → excluded
- Developer-initiated interactive agent session → included

### 3.3 Summarization for Archival

Recent conversations (last 24 hours) are shown in full; older ones (1–30 days) are summarized into one-line summaries in commit-message style so a developer can scan what was discussed without reading full logs.

### 3.4 Multi-Machine Visibility

Chat history is persisted centrally (database) so that any AgentFlow instance can fetch and display the full conversation index for a project, regardless of which machine initiated a session.

### 3.5 Fast Context Recovery

The project view surfaces a "recent chat" widget that displays:
- Last 5 messages or up to 24h of conversation, whichever is smaller
- A "view all" link that opens the full chat history dialog
- An archive view for older summarized conversations

## 4. Data Model

### 4.1 Session

An existing concept; each agent session already captures `session_id`, `agent_type`, `project_id`, `created_at`, `status`.

No changes needed to existing `AgentSession` model.

### 4.2 SessionMessage (New)

Captures a single message within a session. Recorded whenever a developer sends input or an agent produces a response.

```
SessionMessage {
  id: uuid
  session_id: uuid (FK → AgentSession.id)
  project_id: uuid (FK → Project.id)
  timestamp: datetime
  role: enum ('developer', 'agent', 'system')
  content: text
  message_type: enum ('text', 'ask', 'write_file', 'command', 'result', 'error')
  redacted: boolean (default: false)
  metadata: json  // e.g., { "file": "path", "tool": "...", "exit_code": 0 }
}
```

### 4.3 SessionSummary (New)

Stores summarized versions of older conversations (> 24h old). Run nightly or on-demand.

```
SessionSummary {
  id: uuid
  project_id: uuid (FK → Project.id)
  period_start: datetime
  period_end: datetime
  summary: text  // one-line commit-message style
  message_count: integer
  session_ids: json array (list of summarized session IDs)
  created_at: datetime
}
```

### 4.4 SessionMessageFilter (Config, New)

Defines which sessions are included in chat history. Examples:

- Exclude sessions with `agent_type = 'planning_agent'` (Sprint planning)
- Exclude sessions with `role = 'system'` (agent spawn logs)
- Exclude Ralph orchestrator runs (captured separately)
- Exclude pipeline execution sessions

Stored as configuration in `app/settings/` or as a constant in `app/sessions/`.

## 5. Feature Overview

### 5.1 Chat Capture (Real-time)

When a session message is recorded (via agent adapter), a `SessionMessage` row is inserted. Message redaction follows existing `app/security.py` patterns.

**Trigger points:**
- Developer sends a prompt → `SessionMessage` with role='developer'
- Agent returns a response → `SessionMessage` with role='agent'
- Agent uses a tool or writes a file → `SessionMessage` with role='agent', message_type='write_file'
- Error occurs → `SessionMessage` with role='system', message_type='error'

**Implementation:**
- Adapters call a new hook, `record_session_message(session_id, role, content, message_type, metadata)`, which is defined in `app/sessions/message_recorder.py`.
- The hook is called from `CodexAdapter`, `ClaudeAdapter`, and test adapters (FakeAgent).
- Non-interactive sessions (Ralph, pipeline) do not call this hook.

### 5.2 Chat History View (UI)

#### 5.2.1 Project View Widget

The project page (`app/projects/views.py` → `project_detail.html`) shows a "Recent Chat" card:

```
┌─────────────────────────────────────────┐
│ Recent Chat                             │
├─────────────────────────────────────────┤
│ 14:32 You: clarify auth flow            │
│ 14:35 Agent: suggests JWT approach      │
│ 14:40 You: add session storage          │
│                                         │
│ [View Full History] [Archive]           │
└─────────────────────────────────────────┘
```

- Shows last 5 messages or up to 24h, whichever is smaller.
- Timestamps and abbreviated content (truncate to ~60 chars).
- Links to full history dialog and archive view.

#### 5.2.2 Full Chat History Dialog

A modal or dedicated page (`/projects/<id>/chat/` or `/projects/<id>/chat-history.html`) showing:

- **Recent (last 24h):** Full messages with timestamps, role badges, metadata.
- **Archive (1-30 days):** Collapsed, one-line summaries with "expand" action to see full messages.
- **Older (>30 days):** Optional; can be pruned or archived to cold storage.

**Sorting:** Newest first, with jump-to-date picker.

**Filters:**
- By role (all, developer-only, agent-only)
- By session (dropdown of session IDs)
- By date range

#### 5.2.3 Archive Summary View

A simple list of historical summaries per project:

```
Date Range              Summary
─────────────────────────────────────────────
Oct 5–6, 2026          session: refined auth flow, fixed token expiry bug
Oct 4–5, 2026          session: initial JWT implementation, tested login
Oct 1–4, 2026          session: requirements gathering, database schema
```

Clicking a summary expands to show the full messages from that period.

### 5.3 Summarization (Batch)

A scheduled task (or on-demand) that runs nightly:

1. Identify all `SessionMessage` rows older than 24h and newer than 30 days.
2. Group by `project_id` and day (or configurable window).
3. For each group, call a summarization agent (uses existing MCP tool or inline LLM call) to produce a one-line summary.
4. Insert `SessionSummary` row.
5. Delete or soft-delete the original `SessionMessage` rows (configurable retention).

**Summarization prompt:**
```
Summarize the following developer-agent conversation into one commit-message-style line (under 72 chars):

[messages]

Summary:
```

**Example outputs:**
- `session: clarified auth flow, JWT tokens, session storage`
- `session: fixed token expiry bug, added refresh logic`
- `session: planning API versioning, deprecation strategy`

### 5.4 Multi-Machine Sync

Chat history is stored in the central SQLite database (already synced across machines via shared artifact storage or networked DB). No additional sync mechanism needed if machines share the same database.

If machines have separate databases (not recommended for AgentFlow), a future enhancement could sync via an API endpoint or webhook, but the initial design assumes single centralized persistence.

### 5.5 Chat as Session Context for Agents

When starting a new agent session in a project, optionally include recent chat as context:

```
[System Prompt]
Recent conversation context for project [repo_name]:

14:32 You: clarify auth flow
14:35 Agent: suggests JWT approach
14:40 You: add session storage

[Continue with task...]
```

This is an optional "warm start" feature that can be enabled per session. Controlled via a checkbox on the session start page.

## 6. Implementation Phases

### Phase 1: Message Capture (Foundation)

- Add `SessionMessage` model and migrations.
- Add `record_session_message()` hook to adapters.
- Add basic capture logic to `CodexAdapter` and `ClaudeAdapter`.
- Add tests for message recording.
- **Deliverable:** Session messages are recorded; no UI yet.

### Phase 2: UI Scaffolding (Basic Display)

- Add `SessionMessage` query helpers in `app/sessions/`.
- Add recent chat widget to project view.
- Add full chat history page.
- Add basic filtering and sorting.
- **Deliverable:** Chat history is visible and navigable.

### Phase 3: Summarization (Archive)

- Add `SessionSummary` model and migrations.
- Build summarization batch job (cron or Task Master).
- Add archive view to chat history page.
- Test summarization quality.
- **Deliverable:** Older conversations are archived as summaries.

### Phase 4: Polish & Multi-Machine

- Ensure sync works across machines (validate centralized DB setup).
- Add mobile-responsive styling to chat views.
- Add search across chat history.
- Add export/download of conversation thread.
- **Deliverable:** Production-ready feature across all deployment scenarios.

### Phase 5: Optional Enhancements

- Chat as session context for agent warm-start.
- Chat annotations (e.g., tag a message as "decision point").
- Integration with Sprint/Backlog (link chat to a task).
- Conversation threading (group related messages).

## 7. Security & Redaction

- Existing `app/security.py` redaction rules apply to chat messages.
- Redacted messages are persisted with `redacted=true` flag.
- Summarization agent sees only redacted content.
- No unredacted content is visible in UI.

## 8. Performance Considerations

### 8.1 Database

- Add index on `(project_id, timestamp DESC)` for fast recency queries.
- Add index on `(session_id, timestamp)` for session-specific queries.
- Batch insert `SessionMessage` rows if high volume.

### 8.2 Summarization

- Run nightly during low-traffic window.
- Limit summarization to projects with activity in the past 30 days.
- Optionally use a background worker (Redis/Celery) if SQLite perf becomes a bottleneck.

### 8.3 Archival

- Archive `SessionMessage` rows older than 30 days to a separate table or cold storage.
- Keep `SessionSummary` for 90 days.
- Add configuration for retention policies.

## 9. Related: Wiki Improvements

As noted in user feedback, Wikis are currently serviced via CloudCLI tool. A future design phase should address:

- **Dedicated Wiki UX:** Move beyond CloudCLI's generic browsing to a tailored wiki reader/editor within AgentFlow.
- **Domain-scoped Wikis:** Tie wikis to projects and sprints (e.g., "API Design Wiki for Project X").
- **Mobile-friendly Rendering:** Render wiki content with responsive design; avoid CloudCLI's limitations on narrow screens.
- **Integrated Search:** Search across project wikis from the main interface.
- **Editing:** Allow in-app wiki creation/editing alongside session chat (future phase).

This should be scoped as a separate feature epic after session history is complete.

## 10. Open Questions & Next Steps

1. **Retention policy:** How long should `SessionMessage` be kept in full form? (Proposed: 1–7 days, with summaries kept 30+ days.)
2. **Summarization cost:** Which model should summarize? (Proposed: cheapest/fastest available, e.g., GPT-4o mini or Claude Haiku.)
3. **Session filtering:** Are the exclusion rules in § 3.2 correct, or should some Ralph/pipeline messages be included?
4. **Warm-start context:** Should chat context be automatically included in new sessions, or only on opt-in?
5. **Wiki design:** Should wikis be a separate UI surface or integrated into project chat/context?

---

**Next:** Convert this design into Task Master tasks, then validate with user feedback before implementation begins.
