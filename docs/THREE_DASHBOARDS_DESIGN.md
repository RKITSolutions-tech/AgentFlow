# Three Dashboards Design

## Overview

AgentFlow will feature three interconnected dashboards to provide quick access to recent work, knowledge bases, and project status. Each dashboard is mobile-first, touch-friendly, and serves as an entry point for starting new sessions with contextual awareness.

| Dashboard | Purpose | Primary Actions |
|-----------|---------|-----------------|
| **Home** | Recent activity hub; quick overview of ongoing work | Resume session/pipeline/backlog item; start blank session; continue topic |
| **Wikis** | Browse and search knowledge bases | Search docs; view wiki; start session about wiki topic |
| **Project** | Sprint planning and project status; dev-focused hub | View sprint; triage backlog; run pipeline; start project-scoped session |

---

## 1. Home Dashboard

### Purpose
Quick-access hub for resuming recent work. Shows recent sessions, active pipelines, recent backlog items, and wikis you've been in. Entry point for "new session" with optional context.

### Data Model
```sql
-- Recent sessions (GENERAL role, sorted by last activity)
SELECT id, title, agent_type, status, created_at, updated_at, model
FROM agent_sessions
WHERE role = 'GENERAL' AND project_id IS NULL
ORDER BY updated_at DESC
LIMIT 5

-- Active pipelines (RUNNING/PAUSED)
SELECT id, name, status, created_at, project_id
FROM pipeline_executions
WHERE status IN ('RUNNING', 'PAUSED')
ORDER BY updated_at DESC
LIMIT 5

-- Recent backlog items (sorted by activity)
SELECT id, title, status, priority, project_id
FROM backlog_items
ORDER BY updated_at DESC
LIMIT 5

-- Recent wikis accessed (requires session_context audit trail or browser history)
-- For MVP: just list available wikis, sorted by last-modified mtime
```

### Layout (Mobile-first)

**Mobile (375px):**
```
┌─ Home
├─ [+ New Session] (full-width button)
├─ Recent Sessions
│  ├─ [Session Title]    [Resume] [more...]
│  ├─ [Session Title]    [Resume] [more...]
│  └─ [See all]
├─ Active Pipelines
│  ├─ [Pipeline Name] 🟢 [View] [Stop]
│  └─ [Pipeline Name] 🟡 [View]
├─ Recent Backlog
│  ├─ [Item: Title]      [Open] [more...]
│  └─ [Item: Title]      [Open]
└─ Recent Wikis
   ├─ [Wiki/Project]     [Browse] [Edit]
   └─ [Wiki/Project]     [Browse]
```

**Desktop (1280px):**
```
Grid layout (2–3 columns):
┌───────────────────────────────────────────────────────┐
│ Home Dashboard                          [+ New Session] │
├───────────────────┬───────────────────┬────────────────┤
│ Recent Sessions   │ Active Pipelines  │ Recent Backlog │
│                   │                   │                │
│ • Session A       │ • Pipeline X 🟢   │ • Item 1       │
│   Last: 2h ago    │   Running         │   High         │
│   [Resume]        │   [View] [Stop]   │   [Open]       │
│                   │                   │                │
│ • Session B       │ • Pipeline Y 🟡   │ • Item 2       │
│   [Resume]        │   Paused          │   Medium       │
│                   │   [Resume]        │   [Open]       │
│                   │                   │                │
│ [See all...]      │ [See all...]      │ [See all...]   │
├───────────────────┴───────────────────┴────────────────┤
│ Recent Wikis                                           │
│ • Wiki/Project A [Browse] [Search]   • Wiki/Shared    │
└───────────────────────────────────────────────────────┘
```

### New Session Modal

When user taps `[+ New Session]`:

```
┌─ Start New Session
├─ Conversation subject (text input, optional)
├─ Context (radio group):
│  ◯ Blank slate
│  ◯ Continue topic: "API Design" (if recent topics exist)
│  ◯ Continue topic: "Bug Fixes"
├─ Model (dropdown, default to last-used)
└─ [Start] [Cancel]
```

If user fills in subject, it becomes the session title. Topic selection pre-loads rolling summary into context.

---

## 2. Wikis Dashboard

### Purpose
Browse, search, and manage knowledge bases. See statistics (size, last updated) and recent changes. Start sessions about wiki topics.

### Data Model
```sql
-- Available wikis (project-scoped + shared)
SELECT id, name, source_type, project_id, path, mtime
FROM wiki_sources
ORDER BY project_id, name

-- Wiki docs (from folder scanner)
-- For each wiki: scan docs/, index title + content + mtime
-- Store in memory or cache (existing wiki_folders.py scanner)

-- Recent wiki views (via session metadata if captured)
-- For MVP: just mtime-based sorting
```

### Layout (Mobile-first)

**Mobile (375px):**
```
┌─ Wikis
├─ [Search wikis...] (search box)
├─ Project Wikis
│  ├─ [Project Name]     [Browse] [Search]
│  ├─ [Project Name]     [Browse] [Search]
├─ Shared Wikis
│  ├─ [Shared]           [Browse] [Search]
├─ [+ New Wiki] (secondary action)
```

**Desktop (1280px):**
```
┌─────────────────────────────────────────┐
│ Wikis                    [Search] [+ New]│
├────────────────┬────────────────────────┤
│ Wiki List      │ Recent Docs / Preview  │
│                │                        │
│ Project A ▶    │ API Design (Project A) │
│ • Architecture │ Last modified: 2h ago  │
│ • API Spec     │ 125 KB | 12 links      │
│ • Deployment   │ [Open] [Start Session] │
│                │                        │
│ Project B ▶    │ Setup Guide            │
│ • Quickstart   │ Last modified: 1d ago  │
│                │ 45 KB | 3 links        │
│ Shared ▶       │ [Open] [Start Session] │
│ • Architecture │                        │
│ • Decisions    │                        │
└────────────────┴────────────────────────┘
```

### Start Session from Wiki

When user taps `[Start Session]` on a wiki doc:

- Spawns new GENERAL session
- Pre-loads wiki folder as context (`session_context()` includes wiki files)
- Title suggestion: doc title or user can edit
- Session may eventually be tagged with wiki/topic for discovery

---

## 3. Project Dashboard

### Purpose
Per-project hub: sprint status, backlog summary, pipeline overview, and project-scoped actions. Entry point for project-focused sessions.

### Data Model
```sql
-- Current sprint
SELECT id, name, status, start_date, end_date, project_id
FROM sprints
WHERE project_id = ? AND status IN ('ACTIVE', 'PLANNED')
ORDER BY start_date DESC
LIMIT 1

-- Backlog summary
SELECT status, COUNT(*) as count, MAX(updated_at) as last_updated
FROM backlog_items
WHERE project_id = ?
GROUP BY status

-- Active pipelines (project-scoped)
SELECT id, name, status, created_at
FROM pipeline_executions
WHERE project_id = ?
ORDER BY created_at DESC
LIMIT 5

-- Team / collaborators (future: currently single-user)
SELECT DISTINCT user_id FROM agent_sessions WHERE project_id = ?
```

### Layout (Mobile-first)

**Mobile (375px):**
```
┌─ Project: [Name]
├─ [+ Start Session] (project-scoped)
├─ Current Sprint
│  ├─ Sprint Name (Status)
│  ├─ 5 tasks | 3 done | 2 in progress
│  └─ [View Sprint] [Add Task]
├─ Backlog Snapshot
│  ├─ Inbox: 2 items
│  ├─ Ready: 8 items
│  ├─ In Progress: 3 items
│  └─ [Triage Backlog]
├─ Active Pipelines
│  ├─ [Pipeline X] 🟢 [View] [Stop]
│  ├─ [Pipeline Y] 🟡 [Resume]
│  └─ [See all]
└─ Quick Actions
   ├─ [Start New Sprint]
   ├─ [Run Pipeline]
```

**Desktop (1280px):**
```
┌──────────────────────────────────────────────────────────┐
│ Project: [Name]                  [+ Start Session] [⚙️]   │
├──────────────┬──────────────────┬───────────────────────┤
│ Current      │ Backlog Status   │ Active Pipelines      │
│ Sprint       │                  │                       │
│              │ Inbox: 2 ⏳       │ • Pipeline X 🟢       │
│ Sprint Name  │ Ready: 8 ✓       │   Running             │
│ (ACTIVE)     │ In Progress: 3 🔄│   [View] [Stop]       │
│              │ Done: 12 ✅      │                       │
│ 5 tasks      │ Blocked: 1 ❌    │ • Pipeline Y 🟡       │
│ 3 done       │                  │   Paused              │
│ 2 in progress│ [Triage] [+New]  │   [View] [Resume]     │
│              │                  │                       │
│ [View Sprint]│                  │ [See all...]          │
│ [Add Task]   │                  │                       │
└──────────────┴──────────────────┴───────────────────────┘
```

### Start Project-Scoped Session

When user taps `[+ Start Session]` on Project Dashboard:

- Spawns GENERAL session with `project_id` set
- Context includes: current sprint, backlog summary, recent runs
- Title auto-prefixed with project name or user can edit
- Sessions stay discoverable in Project Dashboard and Home Dashboard

---

## 4. Navigation & Context Routing

### Sidebar Structure
```
Sidebar
├─ 🏠 Home          → Home Dashboard
├─ 📚 Wikis         → Wikis Dashboard
├─ [Project Name]   → Project Dashboard (if in project context)
│  ├─ 🎯 Sprints
│  ├─ 📋 Backlog
│  ├─ 🚀 Pipelines
│  └─ 💬 Sessions
├─ ⚙️ Settings
└─ ℹ️ About
```

### Cross-Dashboard Navigation

- **Home → Project**: Click project name in card → Project Dashboard
- **Home → Wiki**: Click wiki name → Wikis Dashboard → Browse that wiki
- **Project → Home**: Click Home in sidebar → back to Home Dashboard
- **Wikis → Session**: Click `[Start Session]` → new session with wiki context
- **Project → Session**: Click `[+ Start Session]` → new session with project context
- **Session → (any)**: Sessions remain accessible from Home Dashboard

### Session Context Routing

| Starting Point | Context Loaded | Notes |
|---|---|---|
| Home (blank) | None | Blank slate |
| Home (continue topic) | Topic rolling summary | Loaded from `session_topics` |
| Wikis → Start Session | Wiki folder(s) | Passed via `session_context()` |
| Project → Start Session | Sprint + backlog + recent runs | Project context attached |
| Backlog item | Discussion + item details | `backlog_item_id` in metadata |

---

## 5. Implementation Phases

### Phase 1: Home Dashboard (MVP)
- Recent sessions (5 items, GENERAL role)
- Recent backlog items (5 items, any project)
- Active pipelines (RUNNING/PAUSED)
- New Session button → basic modal
- No topic continuation (just blank or project-scoped)

**Files to create/modify:**
- `app/routes/dashboards.py` (new)
- `app/templates/dashboards/home.html` (new)
- `app/static/dashboards.css` (new)
- Update sidebar navigation

### Phase 2: Wikis Dashboard
- List available wikis (project + shared)
- Search within wikis (reuse existing wiki search)
- Recent docs by mtime
- Start session from wiki doc

**Files to create/modify:**
- `app/routes/dashboards.py` → add wikis route
- `app/templates/dashboards/wikis.html` (new)
- Leverage `app/wikis/` scanner and search

### Phase 3: Project Dashboard
- Sprint status card
- Backlog status summary
- Active pipelines
- Quick actions (start sprint, run pipeline, start session)

**Files to create/modify:**
- `app/routes/dashboards.py` → add project dashboard route
- `app/templates/dashboards/project.html` (new)
- Queries against `sprints`, `backlog_items`, `pipeline_executions`

### Phase 4: Topic Continuation
- Detect recent GENERAL topics
- Offer "Continue topic: X" in new session modal
- Pre-load rolling summary

**Files to create/modify:**
- `app/sessions/starter.py` → fetch recent topics
- `app/templates/dashboards/home.html` → update modal

### Phase 5: Cross-Dashboard Linking & Polish
- Deep linking (e.g., wiki link in home → wikis dashboard + open wiki)
- Stats cards (doc count, pipeline history)
- Responsive refinement for edge cases

---

## 6. Data Queries & Performance

### Home Dashboard Queries
```python
# Recent sessions (GENERAL, no project scope)
recent_sessions = AgentSession.query.filter_by(
    role='GENERAL', project_id=None
).order_by(AgentSession.updated_at.desc()).limit(5)

# Active pipelines (all projects)
active_pipelines = PipelineExecution.query.filter(
    PipelineExecution.status.in_(['RUNNING', 'PAUSED'])
).order_by(PipelineExecution.updated_at.desc()).limit(5)

# Recent backlog items (all projects)
recent_backlog = BacklogItem.query.order_by(
    BacklogItem.updated_at.desc()
).limit(5)
```

**Caching:** Queries are lightweight; cache sidebar wikis list (mtime-based, refresh on demand).

### Wikis Dashboard Queries
```python
# Available wikis (project + shared)
wikis = WikiSource.query.all()

# Docs in wiki (via scanner)
docs = wiki_scanner.scan(wiki.path)  # in-memory, mtime-indexed
```

### Project Dashboard Queries
```python
# Current sprint
current_sprint = Sprint.query.filter_by(
    project_id=project_id, status='ACTIVE'
).first()

# Backlog summary
backlog_summary = db.session.query(
    BacklogItem.status, func.count(BacklogItem.id)
).filter_by(project_id=project_id).group_by(
    BacklogItem.status
).all()

# Active pipelines
active_pipelines = PipelineExecution.query.filter_by(
    project_id=project_id
).filter(PipelineExecution.status.in_(['RUNNING', 'PAUSED'])
).limit(5)
```

---

## 7. Mobile & Desktop Constraints

### Mobile (375px & below)
- Full-width cards (no margin)
- Touch targets ≥ 44px (button height + padding)
- Buttons stack vertically or wrap in flex row
- No horizontal scroll
- Collapse details into modals or expand-on-tap
- Sidebar → drawer below 860px

### Desktop (1280px & above)
- Multi-column grid layouts
- Generous whitespace
- Side-by-side cards
- Hover states for secondary actions
- Status indicators (badges, colors) prominent

### Common (All viewports)
- Reuse `app.css` design tokens (colors, spacing, shadows)
- `.table-compact` for dense data
- `.row-actions` for button groups
- No new CSS dependencies (Bootstrap, etc.)

---

## 8. Existing Code to Leverage

| Component | Location | Use |
|-----------|----------|-----|
| Session context loader | `app/sessions/starter.py` | Pre-load wiki/project context |
| Wiki scanner | `app/knowledge/wiki_folders.py` | Docs indexing |
| Wiki search | `app/wikis/` | Search within wikis |
| Session topics | `app/sessions/topics.py` | Topic rolling summary |
| Backlog models | `app/backlog/models.py` | Backlog queries |
| Sprint queries | `app/sprints/persistence.py` | Sprint status |
| Pipeline execution | `app/pipelines/engine.py` | Pipeline status |
| Sidebar nav | `app/shell.py` | Navigation data |
| Mobile CSS | `app/static/app.css` | Design tokens, responsive |

---

## 9. Success Metrics

- Home Dashboard loads in <500ms (all queries cached or indexed)
- Mobile layout readable at 375px, desktop at 1280px
- New session spawned with context in <2s
- Users can resume recent work in 1–2 taps from Home

---

## 10. Open Questions & Future Work

1. **Wiki history**: Should we track which wikis a user has viewed for "Recently Accessed Wikis"? (Currently only mtime-sorted.)
2. **Shared/team view**: Single-user for now, but structure should support showing shared items later (sessions, pipelines, backlog items).
3. **Topic suggestions**: Should "New Session" suggest topics based on context (e.g., if in a wiki, suggest related topics)?
4. **Pipeline artifacts**: Should pipeline cards show status + latest artifact link?
5. **Backlog filters**: Should Backlog summary allow filtering by priority or tag?

