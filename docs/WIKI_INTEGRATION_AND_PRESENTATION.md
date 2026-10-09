# Wiki Integration and Presentation

## 1. Purpose

This document defines an improved wiki reading and editing experience within AgentFlow, replacing the current CloudCLI-based generic browsing with a tailored, domain-aware interface optimized for desktop and mobile. Wikis are scoped to projects and sprints, with integrated search and collaboration features.

## 2. Problem Statement

### Current State

- Wikis are accessed via the CloudCLI tool (`app/mcp/tools/wikis.py`), which provides generic file browsing and is not optimized for technical documentation workflows.
- No distinction between project-level wikis (design docs, architecture) and sprint-level wikis (task specifics, decision logs).
- Mobile experience is poor: generic file tree navigation doesn't render well on narrow screens; no mobile-optimized rendering.
- No integrated search across project wikis.
- Wiki creation and editing are external processes; no in-app authoring workflow.
- Wikis are not linked to tasks, decisions, or sprint context, making navigation cumbersome.

### Desired Outcome

- **Tailored Presentation:** Wiki content is rendered with semantic understanding (e.g., decision logs, architecture diagrams, design rationales) rather than as generic files.
- **Mobile-First:** Content reflows cleanly on narrow screens; touch-friendly navigation and readability.
- **Project & Sprint Scoping:** Clear distinction and quick navigation between project wikis and sprint-specific documentation.
- **Integrated Search:** Search across all wikis (project and sprint) from the main interface; results are ranked by relevance and recency.
- **In-App Editing:** Create and edit wiki pages directly within AgentFlow (Phase 2+).
- **Linked Context:** Wikis can reference and be referenced from tasks, decisions, and run artifacts.
- **CloudCLI Deprecation:** CloudCLI wiki tool is retained for backward compatibility but not surfaced in the UI.

## 3. Design Principles

### 3.1 Domain-Aware Rendering

Wikis are not generic markdown; they follow domain conventions:
- **Architecture pages** (e.g., `ARCHITECTURE.md`) → structured, with diagram/table support.
- **Decision logs** (e.g., `DECISIONS.md`, `ADRs/`) → chronological, with status and stakeholders.
- **Task documentation** (e.g., sprint wikis) → linked to Task Master tasks.
- **API documentation** (e.g., `docs/api/`) → schema and example rendering.

Recognition is via path convention (e.g., files in `docs/DECISIONS/` are ADRs) or frontmatter tags.

### 3.2 Mobile-First Rendering

Content reflows to single-column layouts below 640px. Navigation is collapsible; code blocks are scrollable; images scale responsively.

### 3.3 Project & Sprint Separation

- **Project Wikis:** Live in `docs/` and are tied to the repository's canonical documentation.
- **Sprint Wikis:** Live in sprint metadata (or a dedicated `sprints/<sprint_id>/docs/` directory) and capture sprint-specific knowledge.
- Clear tabs or sections in the UI to switch between project and sprint wikis.

### 3.4 Ephemeral vs. Persistent

- **Persistent:** Project wikis are version-controlled (in the repo).
- **Ephemeral:** Sprint wikis may be created, edited, and archived within AgentFlow; they are synced to the repo if enabled, or kept in the database.

### 3.5 Contribution Workflow

Wiki edits are captured as Task Master commits (if repo is linked) or as sprint artifacts. A "Recent Edits" sidebar shows who changed what and when.

## 4. Feature Overview

### 4.1 Wiki Browser (Read)

A dedicated wiki view (`/projects/<id>/wiki/` or `/sprints/<id>/wiki/`) shows:

#### 4.1.1 Left Sidebar: Table of Contents

- **Project Wikis** section: list of docs in `docs/` (hierarchy, e.g., `docs/ARCHITECTURE.md`, `docs/ADRs/...`)
- **Sprint Wikis** section (if sprint is active): sprint-specific docs.
- Search field with instant filtering.
- "Sort by" dropdown (alphabetical, modified date, relevance).

#### 4.1.2 Main Content: Rendered Markdown

- Full-width on desktop; single-column on mobile.
- Tables, code blocks, images render responsively.
- Syntax-highlighted code blocks with copy-to-clipboard.
- Expandable/collapsible headings (useful for long documents).
- Smooth scroll navigation to section anchors.

#### 4.1.3 Right Sidebar: Document Metadata

- Last modified date and author.
- Related tasks or sprint items (if linked).
- Related decisions or ADRs.
- "Share" and "Copy Link" actions.
- "Edit" button (if user has permissions; see § 4.2).

### 4.2 Wiki Editor (Write, Phase 2+)

An in-app editor (using CodeMirror or similar) for creating and editing wiki pages:

- **Markdown editor** with live preview.
- **Template picker** for common types (ADR, architecture, task doc, decision log).
- **Save** triggers a commit to the repo (if enabled) or saves to the database.
- **Staging:** Edits are draft until "publish" is clicked.

### 4.3 Wiki Search

A global search box in the wiki view:

- Searches across all project and sprint wikis.
- Returns results ranked by match score and recency.
- Result preview shows snippet with query highlighted.
- Click to navigate to that page and section.

**Implementation:**
- Full-text search via SQLite FTS (if wikis are in the database) or `grep`-based indexing (if in the repo).
- Regenerate index nightly or on-demand.

### 4.4 Wiki Navigation & Linking

- **Breadcrumb** in the content area: "Project > Docs > Architecture" or "Sprint 5 > Wiki > Sprint Plan".
- **Related Pages** sidebar: suggests similar or linked pages.
- **Backlinks:** Show which pages link to the current one.

### 4.5 Decision Log and ADR Support

Pages matching pattern `docs/DECISIONS/` or `docs/ADRs/` are rendered as a timeline:

```
📋 Architecture Decision Record — Session Handling

Status: Accepted (2026-10-01)
Author: Ryan Kenning
Stakeholders: Alice (PM), Bob (Eng Lead)

Problem: Session tokens need secure storage per compliance requirements

Decision: Use JWT + encrypted session store

Reasoning:
- ...

Consequences:
- ...

Alternatives Considered:
- ...
```

Sorted by date (newest first).

### 4.6 Backlog/Sprint Integration

Sprint wikis can include:

- **Sprint Overview:** Goals, definition of done, stakeholders.
- **Task References:** Link to Task Master tasks; vice versa, tasks can link to sprint wiki docs.
- **Status Updates:** Manual notes on progress, blockers, learnings.
- **Retrospective:** Post-sprint notes (created near end of sprint).

Sprint wiki pages are automatically created from sprint templates at sprint creation.

## 5. Data Model

### 5.1 WikiPage (New, Optional)

If wikis are stored in the database (vs. repo-only):

```
WikiPage {
  id: uuid
  project_id: uuid (FK → Project.id)
  sprint_id: uuid | NULL (FK → Sprint.id, if sprint-scoped)
  title: text
  slug: text  // for URLs
  path: text  // relative path in repo (if version-controlled)
  content: text  // markdown
  version: integer  // revision number
  created_at: datetime
  updated_at: datetime
  created_by: text  // user email
  updated_by: text  // user email
  published: boolean  // draft vs. live
  metadata: json  // {"type": "adr", "status": "accepted", ...}
}
```

### 5.2 WikiEdit (New, Optional)

If audit trail is needed:

```
WikiEdit {
  id: uuid
  wiki_page_id: uuid (FK)
  version: integer
  content_diff: text  // unified diff
  edited_by: text
  edited_at: datetime
  commit_sha: text | NULL  // git commit if synced to repo
}
```

### 5.3 Configuration (New)

```
app/settings/wiki_config.py:
  - WIKI_REPO_SYNC: bool (default: true) — sync edits to git repo
  - WIKI_STORAGE: enum ('repo', 'database', 'hybrid') — where wikis live
  - WIKI_SEARCH_INDEX_INTERVAL: int (seconds, default: 3600) — reindex frequency
```

## 6. Implementation Phases

### Phase 1: Wiki Browser (Read-Only)

- Scan project's `docs/` directory for markdown files.
- Build navigation tree (sidebar TOC).
- Render markdown with responsive styling.
- Add metadata sidebar (modified date, related tasks).
- Add search across wikis.
- **Deliverable:** Wikis are readable and searchable within AgentFlow.

### Phase 2: Wiki Editor (Write)

- Add in-app markdown editor.
- Add template picker for common types (ADR, task doc, decision).
- Save new pages to `docs/` (with git integration).
- Add staging/draft workflow.
- **Deliverable:** Users can author wikis in-app.

### Phase 3: Sprint Wikis & Integration

- Create sprint wiki scaffold at sprint creation.
- Link sprint docs to Task Master tasks.
- Add sprint overview and status update templates.
- Add retrospective support.
- **Deliverable:** Sprint-specific wikis are first-class.

### Phase 4: ADR & Decision Support

- Recognize and render ADR pattern.
- Add ADR timeline view.
- Support ADR status transitions (Proposed → Accepted/Rejected).
- **Deliverable:** Decision logs are a first-class feature.

### Phase 5: Advanced (Optional)

- Wiki annotations (comments on specific sections).
- Collaborative editing (multiple editors).
- Wiki versioning and diff viewer.
- Sync to external platforms (GitHub wiki, Notion, etc.).

## 7. UI/UX Details

### 7.1 Desktop Layout

```
┌─────────────────────────────────────────────────────────────┐
│ Project Tabs (Repository, Wiki, Chat, Settings)             │
├──────────────┬──────────────────────────────────┬────────────┤
│              │                                  │            │
│ TOC          │ Rendered Content                 │ Metadata   │
│              │                                  │            │
│ 🔍 Search   │ [Breadcrumb]                     │ Last Edit  │
│              │                                  │ Date/User  │
│ 📋 Project  │ # Architecture                   │            │
│  Docs        │                                  │ Related    │
│  ├ Arch...  │ [Full content]                   │ Tasks/ADRs │
│  ├ API...   │                                  │            │
│  ├ ADRs/    │                                  │ [Edit]     │
│  │ ├ 001... │                                  │ [Share]    │
│              │                                  │            │
│ 📌 Sprint    │                                  │            │
│  Docs        │                                  │            │
│  ├ Overview │                                  │            │
│  ├ Status   │                                  │            │
└──────────────┴──────────────────────────────────┴────────────┘
```

### 7.2 Mobile Layout

Below 640px:

```
┌────────────────────────────────────┐
│ ☰ Wiki  [Project Name]      [⚙]    │
├────────────────────────────────────┤
│ 🔍 Search wikis...                 │
│                                    │
│ 📋 Project Docs (3)        ▼       │
│  • Architecture             [edit] │
│  • API Reference                   │
│  • ADRs (2)                        │
│                                    │
├────────────────────────────────────┤
│ # Architecture                     │
│                                    │
│ [Full-width content reflows to     │
│  single column; images/tables      │
│  scroll horizontally if needed]    │
│                                    │
│ Last edited: Oct 5, 2026           │
│ by Ryan Kenning                    │
│                                    │
│ [Related] [Edit] [Share]           │
└────────────────────────────────────┘
```

- Sidebar collapses into a drawer.
- Search is always accessible.
- Content is single-column and reflows.
- Touch targets are 44px+.

### 7.3 Styles

- Reuse `app/static/app.css` for base styles.
- Add `.wiki-content` class for rendered markdown styling (headings, tables, code blocks, images).
- Support dark/light theme via existing tokens.

## 8. Mobile Responsive Behavior

- **< 640px:** Single column; sidebar drawer; full-width content.
- **640px–1280px:** Two-column; sidebar and content side-by-side.
- **> 1280px:** Three-column; sidebar, content, metadata.

## 9. Search Implementation

**Option A: SQLite FTS (if wikis are in database)**
- Add FTS virtual table for wiki content.
- Reindex on edit.
- Fast, no external dependency.

**Option B: Repo Scan (if wikis are version-controlled)**
- Scan `docs/` on startup; build in-memory index.
- Re-scan nightly or on-demand.
- Lite, no database overhead.

**Recommended:** Option B for Phase 1 (leverages repo state); Option A for Phase 2+ if database storage is added.

## 10. CloudCLI Transition

**Current State:**
- CloudCLI `wikis` tool provides generic file browsing via MCP.

**Transition Plan:**
- Phase 1: Launch wiki browser in AgentFlow UI; mark CloudCLI tool as "legacy" in documentation.
- Phase 2: Remove CloudCLI wiki tool from MCP default exports (keep available via opt-in if needed).
- Post-Phase 2: Deprecation warning in CloudCLI docs.

No breaking changes; backward compatibility is maintained.

## 11. Security & Permissions

- **Read:** All authenticated users can read project wikis.
- **Write:** Only users with `project_admin` or `editor` role can edit (Phase 2+).
- **Audit:** All edits are logged with user and timestamp.
- **Redaction:** If wikis contain secrets, existing `app/security.py` redaction rules apply.

## 12. Performance Considerations

- **Search Index:** Rebuild nightly or on-edit; cache results for 1 hour.
- **Markdown Rendering:** Use a fast parser (e.g., `markdown2` or `mistune`); cache rendered HTML for static pages.
- **Image Embedding:** Images in wikis are relative paths; server proxies or embeds them.
- **File Size:** Limit single wiki page to 10MB (or configurable).

## 13. Related: Session Chat Integration

Wikis and session chat (docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md) are complementary:
- **Chat** captures real-time developer-agent conversations.
- **Wikis** capture persistent, curated documentation and decisions.
- Wiki pages can link to relevant chat threads (future enhancement).
- Chat summaries can reference wiki pages (e.g., "see DECISIONS.md for context").

## 14. Wiki Folders: a Knowledge Store for Agent Discussions (Local or Remote)

A wiki can be set up as a **folder of markdown pages**, either on this machine
or on a remote AgentFlow instance (docs/REMOTE_INSTANCE_SETUP.md). It acts as
a project's knowledge store: agent sessions read it for context and record
decisions and findings in it. This is separate from the database-backed
research wiki (`knowledge_entries`, `/wiki`). Presentation (markdown
rendering, in-app editing) is deliberately minimal for now.

- **Set up:** Wiki → *wiki folders* (`/wiki/sources`). Give a name, choose
  which project it belongs to (or *All projects*), choose *This machine* or
  *Remote: &lt;instance&gt;*, and enter the folder path as it exists on that
  machine. Tick *Create it* to make a missing folder with a starter
  `index.md`; an existing folder is never modified on setup.
- **Context at session start:** when a project has wikis, the session start
  form shows *Include the project wiki* (checked by default). The starting
  prompt then lists each wiki (id, name, where it lives, page list and the
  first 4000 characters of `index.md`), and asks the agent to check it and to
  record settled decisions there. An unreachable remote wiki is listed as
  unavailable rather than failing the session start. Recorded as
  `injected_context.wiki_context` on the session.
- **During the discussion (MCP tools):** a session with AgentFlow tools
  enabled gets `wiki_list`, `wiki_search` (all words must match; ranked by
  occurrences, path matches weighted), `wiki_read` and `wiki_write` (create
  or replace a `.md` page, sub-folders created, secrets redacted). A session
  only reaches its own project's wikis and shared ones (`app/mcp/tools/wiki.py`).
  The MCP subprocess receives `AGENTFLOW_ALLOWED_ROOTS`/`AGENTFLOW_REDACT_PATTERNS`
  from the app (`app/agents/mcp_config.py`) so it applies the same policy.
- **Path policy:** the folder must be under the `ALLOWED_PROJECT_ROOTS` of the
  machine it lives on. For a remote folder the *remote* instance checks this,
  and applies its own redaction to writes, through its federation API
  (`/federation/api/wiki/setup|pages|page|search`, bearer-token protected like
  the rest of federation).
- **Browse/search (UI):** `/wiki/sources/<id>` lists every `.md`/`.markdown`
  page (hidden files and folders skipped, capped at 2000), searches the wiki,
  and renders a page as markdown (`index.md` by default; same renderer as
  §16, raw HTML escaped, links to other pages kept inside the viewer). A
  *local* wiki folder of a project also appears in that project's wiki
  browser (§16).
- **Remove:** unregisters the wiki only; the folder and pages stay on disk.

Code: `app/knowledge/wiki_folders.py` (per-host setup/list/search/read/write),
`app/knowledge/wiki_sources.py` (`wiki_sources` table, project scoping,
local/remote dispatch, `session_context()`), `app/mcp/tools/wiki.py`, routes
in `app/knowledge/wiki_views.py`.

Not yet: in-app editing, an edit history/audit trail of agent writes beyond
the folder's own git history, and sprint scoping (§3.3).

## 15. Open Questions & Next Steps

1. **Storage:** Should wikis be repo-only (version-controlled) or in the database (ephemeral)? (Decided for Phase 1: repo-only, see §16.)
2. **Linking:** Should wiki pages automatically link to related tasks or ADRs, or manual only?
3. **Templates:** Which wiki templates are most important? (ADR, task doc, sprint overview, API spec?)
4. **CloudCLI:** Should the CloudCLI wiki tool be removed entirely, or kept as a fallback?
5. **Collaboration:** Should wiki editing support real-time collaboration (future phase)?

## 16. Phase 1 Status: Project Wiki Browser (Implemented)

Phase 1 (§6) shipped as a read-only, repo-backed browser (`WIKI_STORAGE=repo`,
§9 Option B — no wiki table; the `WikiPage` dataclass in `app/wikis/models.py`
keeps the §5.1 shape for a later database/hybrid store).

**Where pages come from.** `/projects/<id>/wiki/` (project tab *Wiki*) shows
one *root* per source: each repository's `docs/` folder (`WIKI_DOCS_DIRS`)
and each *local* wiki folder attached to the project or shared (§14). Remote
wiki folders stay on `/wiki/sources/<id>`. Roots must be under
`ALLOWED_PROJECT_ROOTS`; hidden files/folders are skipped and symlinks that
leave the root are ignored.

**Features.**

- *Page list* (left): each root as a collapsible folder tree, or a flat list
  with dates under *Sort: Recently modified*; decision pages are tagged.
  Typing in the search box filters it by title at once.
- *Search*: after a 300 ms pause the box shows full-text results above the
  content (`/projects/<id>/wiki/search?q=`, JSON for AJAX callers, a results
  page otherwise). Every term must match (as a word, a 3+ character prefix,
  or in the path); ranked by term frequency × idf (title words ×3, path +5)
  with up to +50% for pages changed recently, fading over a year. Snippets
  are escaped with the terms in `<mark>`.
- *Rendering* (`app/wikis/renderer.py`, mistune 3 + Pygments): raw HTML is
  escaped and `javascript:` links neutralised; headings get anchors and
  collapse toggles; fenced code is highlighted with a copy button; tables
  scroll sideways in a wrapper; images lazy-load and are served from the
  wiki root; relative links to pages/folders/images stay inside the browser;
  frontmatter is hidden. Rendered HTML is cached per file version.
- *Page information* (right on > 1280px, below the content otherwise,
  collapsed on phones): last modified, last commit author (`git log`), type,
  file, *Edit* (the repository file editor), *Copy link*, *Share* (a markdown
  link), related Task Master tasks (tasks whose text names the file),
  *Links to* and *Linked from* (markdown links, or a mention of the file name).
- *Breadcrumbs*: project wiki › root › folders › page; folders list their
  pages and sub-folders.
- *Decision records* (§4.5, Phase 4 rendering): pages under `ADRs/`, `adr/`,
  `DECISIONS/`, … or with `type: adr|decision` frontmatter render as a card
  (status badge, date, author, stakeholders, then Problem / Decision /
  Reasoning / Consequences / Alternatives taken from frontmatter, a
  `**Status:** …` line or `##` sections). A folder of decisions renders as a
  timeline, newest first, colour-coded by status.
- *Layout* (§8): < 640px one column with the page list behind a *Pages*
  drawer button; 640–1280px list + content; > 1280px list + content + page
  information. Touch targets are ≥ 44px on narrow screens.

**Indexing.** `app/wikis/scanner.py` caches each file's parsed metadata and
term counts keyed by mtime/size, so a request costs a directory walk and a
`stat` per file; a root is fully re-walked when its folder tree changes, after
`WIKI_SEARCH_INDEX_INTERVAL` seconds, or via the *Rescan* button
(`POST /projects/<id>/wiki/rescan`). The search index rebuilds only when a
page's mtime changes.

**Configuration** (`app/settings/wiki_config.py`, Flask config `WIKI_*` or
env `AGENTFLOW_WIKI_*`): `WIKI_STORAGE` (`repo`; `database`/`hybrid` reserved
and treated as `repo`), `WIKI_REPO_SYNC` (Phase 2), `WIKI_SEARCH_INDEX_INTERVAL`
(3600), `WIKI_DOCS_DIRS` (`docs`), `WIKI_MAX_PAGE_BYTES` (2 MB; larger pages are
listed but not rendered), `WIKI_MAX_PAGES` (2000 per root).

**Known limitations.** Read-only (edit through the repository editor, an
agent's `wiki_write` tool, or git); no sprint wikis (`list_by_sprint` returns
nothing; §6 Phase 3); no ADR status transitions (Phase 4); remote wiki folders
are not part of the project browser or its search; one docs folder per
repository (the first of `WIKI_DOCS_DIRS` that exists).

**CloudCLI.** The CloudCLI `wikis` tool is legacy for AgentFlow projects: use
the project wiki browser to read and the `wiki_*` MCP tools to write (§10).

**Roadmap.** Phase 2 editor and templates with `WIKI_REPO_SYNC` commits;
Phase 3 sprint wikis; Phase 4 ADR status transitions; Phase 5 annotations,
versioning/diffs and external sync (§6).

Code: `app/wikis/` (`models.py`, `persistence.py` roots and page lookup,
`scanner.py`, `renderer.py`, `search.py`, `metadata.py`, `adr.py`,
`routes.py`), `app/templates/wikis/browser.html`, tests in `tests/wikis/`.
