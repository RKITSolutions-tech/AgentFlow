# Session Topics

## 1. Purpose

This document defines **Topics**: a way to group a project's `GENERAL` agent
sessions — interactive developer↔agent discussions, with or without wiki
context — around a specific feature or thread of work, so that starting or
resuming a session in a Topic gets a condensed, feature-scoped context
instead of either repeating the same explanation across sessions or
re-reading large documents to re-establish the current position.

This extends two existing designs rather than replacing them:
docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md (chat capture, warm-start) and
docs/WIKI_INTEGRATION_AND_PRESENTATION.md (wiki folders as session context).
Both are implemented today (`app/sessions/chat.py`, `app/sessions/summarization.py`,
`app/knowledge/wiki_sources.py`) and are the foundation Topics builds on.

## 2. Problem Statement

### 2.1 Current State

- A `GENERAL` session can opt into two kinds of starting context
  (`app/sessions/routes.py` `start_session`):
  - **Warm start**: `chat.get_session_context()` — the last 50 text/ask
    messages from the *whole project*, within the last 24h.
  - **Wiki context**: `wiki_sources.session_context()` — every wiki
    attached to the project, each with its page list and the first 4000
    characters of `index.md`.
- Both are project-wide, not feature-scoped. A project with several
  unrelated threads of work (e.g. "auth rework" and "pipeline retry logic")
  mixes them in warm-start once more than a day passes, or simply misses
  older relevant messages the 24h/50-message window already dropped.
- There is no way to deliberately continue a specific thread: the developer
  either re-explains context by hand in a new session, rereads a design doc
  or wiki page to reconstruct "where did we leave this," or scrolls back
  through an old session's transcript.
- `fork_session` (`app/agents/models.py`) copies one session's *entire*
  history into a new session, which only serves a 1:1 "branch from exactly
  this point" use case — not an open-ended series of separate sessions about
  the same feature over days or weeks.

### 2.2 Desired Outcome

- A developer can mark a session, at any point in the conversation (not
  only at creation), as belonging to a named Topic scoped to the project.
- Topics are visible in the UI: a list per project, and a badge on any
  session that belongs to one, so jumping back into a thread of work is a
  navigation action rather than a search.
- Starting a new session "in" a Topic — or resuming discussion of it —
  gets that Topic's own condensed context first: a short rolling summary of
  prior sessions in the Topic, not the whole project's recent chatter and
  not a full wiki dump. Any attached wiki stays available for the agent to
  consult on demand (via the existing `wiki_*` MCP tools / wiki_context
  checkbox) rather than being the first thing re-read every time.
- This applies equally to sessions with no wiki involved at all — plain
  repository/feature discussions are the primary motivating case, wiki-
  linked discussions are a secondary one.

## 3. Design Principles

### 3.1 Topics Are a Session-Layer Concept, Not a Wiki-Layer One

A Topic groups `agent_sessions` rows directly. A wiki (`wiki_sources`) is
*one* optional thing a Topic can point at for deeper material, the same way
it's optional for any session today — it is not a prerequisite. This keeps
Topics useful for ordinary feature work in a repo that has no wiki folder
set up at all.

### 3.2 Naming Collision with the Research Wiki's `topic` Field

`knowledge_entries.topic` (`app/knowledge/models.py`, the database-backed
research wiki at `/wiki`) is an unrelated, pre-existing concept: a free-text
taxonomy tag on individual KB articles ("language/library/version/topic"),
not a grouping of sessions. Both can reasonably be called "Topic" in UI
copy, but they must not share a table or module. This design uses
`discussion_topics` / `app/sessions/topics.py` to keep them apart in code.

### 3.3 Declared Retroactively, Not Only Up Front

The motivating case is realizing *partway through* a session that it's part
of an ongoing thread — not always planning a Topic before starting a
session. Topic assignment is therefore an action available from an
in-progress or finished session ("Add to Topic" / "Start a Topic from
this session"), in addition to picking a Topic when starting a new one.

### 3.4 Condensed Context, Not Copied History

Unlike `fork_session` (full event-by-event history copy, used for branching
from one exact point), a Topic carries forward a **rolling summary** —
reusing the existing nightly archival machinery's shape
(`app/sessions/summarization.py`'s commit-message-style one-liners) but
triggered per Topic rather than per project-day, and kept short by
re-compressing rather than growing unbounded. The goal is "where did we
leave this" in a few lines, not a transcript.

### 3.5 Project-Scoped, Repo-Agnostic

Like chat history (`docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md` §3.1), a Topic
belongs to a project, not to one repository within it — a feature thread
can span sessions against different repositories of the same project.

## 4. Data Model

### 4.1 `discussion_topics` (New)

```
discussion_topics {
  id: integer primary key
  project_id: integer (FK -> projects.id)
  name: text                  -- short, developer-chosen ("Auth rework")
  status: text                -- 'active' | 'archived'
  rolling_summary: text        -- condensed, commit-message-style lines, newest last
  created_at: datetime
  updated_at: datetime         -- bumped whenever rolling_summary changes
}
```

### 4.2 `agent_sessions.topic_id` (New Column)

A nullable FK column on the existing table, alongside `role`/`project_id`,
rather than inside the JSON `metadata` blob — Topics need to be queried and
joined on ("all sessions in this Topic", "does this project have Topics")
often enough to want a real, indexed column rather than a JSON lookup.

```sql
ALTER TABLE agent_sessions ADD COLUMN topic_id INTEGER NULL
  REFERENCES discussion_topics(id);
CREATE INDEX idx_agent_sessions_topic ON agent_sessions(topic_id);
```

A session's `topic_id` is set at creation (chosen on the start form) or
later (the "Add to Topic" action rewrites it). `fork_session` copies the
parent's `topic_id` by default, same as it copies other session identity.

### 4.3 Rolling Summary Maintenance

No new summary-history table. When a session with a `topic_id` is archived
or otherwise ends, append one commit-message-style line (same prompt
template and length cap as `app/sessions/summarization.py`'s
`SUMMARY_PROMPT_TEMPLATE`/`SUMMARY_LINE_MAX`) to `discussion_topics.rolling_summary`.
If the accumulated summary exceeds a cap (e.g. ~20 lines / ~1500 chars),
re-summarize the whole block down to a shorter one before appending, so it
stays a quick read regardless of how long the Topic has been active.

## 5. Feature Overview

### 5.1 Topic Context at Session Start

Extend the session-start form (`app/sessions/routes.py` `start_session`)
with a Topic selector next to the existing `warm_start`/`wiki_context`
checkboxes: "No topic" (today's behaviour, unchanged), an existing Topic, or
"New topic…" (inline name field). When a Topic is selected:

```
topic_context = discussion_topics.rolling_summary for topic_id
                 + last N text/ask messages from the topic's most recent session
```

built via a new `app/sessions/topics.get_topic_context(db, topic_id)`,
mirroring `chat.get_session_context()`'s shape but scoped to the Topic's own
sessions instead of the whole project, and without the 24h cutoff (a Topic
can go quiet for weeks and still be "current" when picked up again).

`preamble` in `start_session` becomes
`"\n\n".join(p for p in (topic_context, wiki_context, warm_start_context, skills_text) if p)`
— Topic context first since it's the most relevant starting point per the
problem statement, wiki/general warm-start still available underneath for
sessions that opt into them. `injected_context` metadata gains
`"topic_id"`/`"topic_name"` alongside the existing keys, for the same
audit-trail reason skills are recorded there today.

When a Topic is selected, `warm_start` (whole-project recent chat) is
redundant for that session and the checkbox can default off — the Topic's
own context already covers "what did we just discuss," scoped correctly
instead of project-wide.

### 5.2 Declaring a Topic Mid-Conversation

On the session view (`sessions/chat.html`), add an action — alongside the
existing Fork action — "Add to Topic": pick an existing Topic, or "New
topic…" naming one from the current session. This only sets
`agent_sessions.topic_id`; it does not retroactively rewrite the rolling
summary until this session next ends (§4.3), so the action is cheap and
immediate.

### 5.3 Topics List (UI)

A per-project Topics page (`/projects/<id>/topics/`), reached as a view
alongside Sessions in `project_tabs`:

- One row per Topic: name, status, session count, last activity, the latest
  one or two rolling-summary lines.
- Uses `.table-compact` with row actions (`archive`, `rename`) in
  `.row-actions`, consistent with other data tables in the app.
- Clicking a Topic opens its page: full rolling summary, the list of
  sessions that belong to it (including archived ones — a Topic is a
  standing index, not a transient filter), and a "Start session in this
  Topic" button that pre-fills the start form's Topic selector.

### 5.4 Session Badge

The session list and session view show a small Topic chip when
`topic_id` is set, linking to the Topic page — the same visual slot used
for other session metadata badges.

## 6. Relationship to Existing Features

- **Warm start / chat history** (docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md):
  unchanged for Topic-less sessions. Topics are a narrower, opt-in
  alternative scoped to one thread rather than the whole project.
- **Wiki folders** (docs/WIKI_INTEGRATION_AND_PRESENTATION.md §14): unchanged
  as a context source; a Topic does not require one. A natural Phase 2 is
  letting a Topic *reference* a specific wiki page rather than the whole
  wiki being injected, but that is not needed for the core problem here.
- **Fork** (`app/agents/models.py` `fork_session`): stays the mechanism for
  "branch from this exact point with full history." Topics are for "this is
  the Nth independent session about the same feature," which fork does not
  address today.
- **Research wiki `topic` field** (`app/knowledge/models.py`): unrelated;
  see §3.2.

## 7. Implementation Phases

### Phase 1: Data Model & Manual Tagging

- Add `discussion_topics` table and `agent_sessions.topic_id` column/index.
- Add `app/sessions/topics.py`: create/list/rename/archive a Topic,
  `get_topic_context()`.
- Add the Topic selector to the session-start form and the "Add to Topic"
  action on the session view.
- **Deliverable:** Topics exist and can be assigned; no context injection
  or summary rollup yet (Topic sessions behave like today's plain sessions).

### Phase 2: Context Injection

- Wire `get_topic_context()` into `start_session`'s `preamble`.
- Record `topic_id`/`topic_name` in `injected_context`.
- **Deliverable:** Starting a session in a Topic surfaces that Topic's
  prior context.

### Phase 3: Rolling Summary

- On session end/archive for a session with `topic_id` set, generate and
  append a one-liner to `discussion_topics.rolling_summary`, reusing
  `app/sessions/summarization.py`'s prompt template and agent-selection
  (`default_agent_factory` keyed off `PLANNING_AGENT`).
- Add the re-compression step once the cap is exceeded.
- **Deliverable:** Long-running Topics stay a quick read regardless of
  session count.

### Phase 4: UI

- Topics list page, Topic detail page, session badge.
- Mobile layout per the Temporary UI and Mobile Rules (375px/1280px checks,
  `.table-compact`, `.row-actions`).
- **Deliverable:** Topics are a first-class, navigable surface.

## 8. Security & Redaction

No new redaction surface: `rolling_summary` is generated from already-
redacted `agent_events` content via the same summarization agent path
`app/sessions/summarization.py` already uses, which itself only ever reads
redacted rows.

## 9. Performance Considerations

- `idx_agent_sessions_topic` keeps "sessions in this Topic" and "does this
  project have any Topics" queries indexed.
- `rolling_summary`'s cap (§4.3) bounds both storage and the size of the
  context injected per session start.

## 10. Open Questions & Next Steps

1. **Cross-project Topics:** out of scope here (§3.5 fixes a Topic to one
   project) — is a feature ever split across projects in practice, or is
   that always a sign it should be two Topics?
2. **Auto-archival:** should a Topic with no new sessions for N days flip to
   `archived` automatically, or stay manual-only?
3. **Summary trigger:** per-session-end (as proposed) vs. on-demand/batch —
   per-session-end is simpler but means a Topic's summary can lag behind if
   a session is left running indefinitely rather than archived.
4. **Linking a Topic to a Backlog item:** `docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md`
   §6 Phase 5 already floats linking chat to a task; a Topic is a more
   natural anchor for that link than individual sessions. Worth doing here,
   or kept separate?

## 11. Implementation Status

All four phases are implemented (tasks 82-94):

- **Data model:** `discussion_topics` (unique name per project, `active`/
  `archived`) in `app/db.py`'s schema; `agent_sessions.topic_id` added by
  `_migrate()` (`ON DELETE SET NULL`) with `idx_agent_sessions_topic`.
  `AgentSession.topic_id`, `set_session_topic()`, and `fork_session` copies
  the parent's Topic (the fork only contributes messages after the fork
  point to the summary).
- **`app/sessions/topics.py`:** create/list/get/rename/archive/restore
  (names are trimmed, ≤ 80 chars, unique per project case-insensitively),
  `resolve_topic_choice()` for the pickers, `get_topic_context()` (rolling
  summary + last 20 text/ask messages of the Topic's most recent session that
  has any, no time cutoff) and the rolling summary.
- **Session start:** the form has a Topic picker (none / existing / *New
  topic…*); picking one unticks *Include recent project context* unless it
  was set by hand. `app/sessions/starter.py` puts Topic context first in the
  preamble and records `topic_id`/`topic_name`/`topic_context` in
  `injected_context`. `?topic=<id>` pre-selects a Topic.
- **Add to Topic:** a disclosure on the session view (`POST
  /sessions/<id>/topic`, AJAX) assigns an existing or new Topic, or removes it.
- **Rolling summary:** archiving a Topic session appends one line built with
  `summarization.py`'s prompt and 72-character cap from the messages not yet
  summarized (`metadata.topic_summarized_through`, so re-archiving only adds
  what is new). Past 1500 characters or 20 lines the whole block is
  re-compressed by the summarization agent, or, under `PLANNING_AGENT=fake` or
  when the agent fails, trimmed to the newest lines with a "condensed away"
  marker. With a real agent the summary is written on a background thread so
  the archive returns at once; a summarizer failure never blocks the archive.
- **UI:** project tab *Topics* (always shown, so the first Topic can be
  created there), `/projects/<id>/topics/` (active/archived views,
  `.table-compact` with AJAX rename/archive/restore row actions, *Start
  session*) and `/projects/<id>/topics/<topic_id>` (full summary, every
  session including archived ones). `PATCH /projects/<id>/topics/<topic_id>`
  takes `{"name"}`/`{"status"}` JSON. Sessions in a Topic show a chip linking
  to it on the project sessions list, the global sessions list and the
  session view.

Open questions 1-4 above remain open (no auto-archival, summaries per archive).
