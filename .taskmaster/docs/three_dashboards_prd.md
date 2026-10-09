# Three Dashboards Implementation PRD

## Overview

Implement three interconnected dashboards for AgentFlow:
1. **Home Dashboard** — Quick-access hub for recent sessions, pipelines, and backlog items
2. **Wikis Dashboard** — Browse, search, and manage knowledge bases
3. **Project Dashboard** — Per-project sprint status, backlog, and pipelines

All dashboards are mobile-first (375px+), touch-friendly, and serve as entry points for starting contextual sessions.

## Goals

1. Provide a quick-access hub for resuming recent work across all activity types
2. Make it easy to start new sessions with contextual awareness (wiki topic, project scope)
3. Offer clear mobile and desktop experiences without adding new UI dependencies
4. Leverage existing code (session context, wiki scanner, sprint/backlog queries)

## Acceptance Criteria

- Home Dashboard displays recent sessions (5), active pipelines (5), recent backlog items (5)
- New Session button spawns modal with optional context/topic selection
- Wikis Dashboard shows available wikis with search and recent docs
- Project Dashboard shows sprint status, backlog summary, active pipelines
- All layouts responsive: 375px (mobile) and 1280px (desktop) without horizontal scroll
- Touch targets ≥ 44px on mobile
- Sessions spawned from dashboards include appropriate context (wiki folder, project, topic)
- No new CSS dependencies; reuse app.css design tokens

## Phase 1: Home Dashboard (MVP)

**Deliverables:**
- Home Dashboard route and template
- Recent sessions query (GENERAL role, no project scope)
- Recent backlog items query (all projects)
- Active pipelines query (all projects)
- New Session modal with blank/topic options
- Sidebar navigation updated
- Mobile (375px) and desktop (1280px) layouts tested

**Implementation Tasks:**
1. Create `app/routes/dashboards.py` with Home route
2. Create `app/templates/dashboards/home.html` with responsive layout
3. Create `app/static/dashboards.css` with mobile/desktop breakpoints
4. Update `app/shell.py` to add Home Dashboard nav item
5. Implement New Session modal (optional subject, context picker)
6. Test responsive layout at 375px and 1280px
7. Verify touch targets ≥ 44px on mobile

## Phase 2: Wikis Dashboard

**Deliverables:**
- Wikis Dashboard route and template
- List available wikis (project + shared, sorted by name)
- Search within wikis (reuse existing wiki search)
- Recent docs by mtime
- Start Session action for wiki docs
- Sidebar nav updated
- Mobile and desktop layouts

**Implementation Tasks:**
1. Add Wikis route to `app/routes/dashboards.py`
2. Create `app/templates/dashboards/wikis.html` with wiki list and search
3. Integrate wiki scanner from `app/knowledge/wiki_folders.py`
4. Implement wiki doc search (leverage `app/wikis/` search)
5. Add "Start Session" action (spawns session with wiki context)
6. Test responsive layout and search functionality
7. Update sidebar nav with Wikis link

## Phase 3: Project Dashboard

**Deliverables:**
- Project Dashboard route and template (per-project view)
- Current sprint card (status, task count, dates)
- Backlog summary (status breakdown: Inbox, Ready, In Progress, Done, Blocked)
- Active pipelines list (RUNNING/PAUSED)
- Quick actions (Start New Sprint, Run Pipeline, Start Session)
- Sidebar nav updated for project context
- Mobile and desktop layouts

**Implementation Tasks:**
1. Add Project Dashboard route to `app/routes/dashboards.py`
2. Create `app/templates/dashboards/project.html` with sprint/backlog/pipeline cards
3. Implement current sprint query from `app/sprints/persistence.py`
4. Implement backlog summary query (status breakdown)
5. Implement active pipelines query
6. Add "Start Session" action (project-scoped context)
7. Add quick action buttons (Start Sprint, Run Pipeline, etc.)
8. Test responsive layout
9. Update project context sidebar to include Project Dashboard link

## Phase 4: Topic Continuation

**Deliverables:**
- Recent GENERAL topics fetched on Home Dashboard load
- New Session modal offers "Continue topic: X" option
- Topic rolling summary pre-loaded into session context

**Implementation Tasks:**
1. Update `app/sessions/starter.py` to fetch recent topics
2. Modify Home Dashboard template to show topic options in New Session modal
3. Route topic selection to session creation with rolling summary loaded
4. Test topic pre-load in session context
5. Verify topic selection flow on mobile and desktop

## Phase 4: Cross-Dashboard Linking & Polish

**Deliverables:**
- Deep linking (wiki card → Wikis Dashboard, sprint card → Project Dashboard)
- Responsive refinement for edge cases (overflow, truncation)
- Polish animations/transitions if needed
- Performance tuning (query caching, load times)

**Implementation Tasks:**
1. Add deep links from Home Dashboard cards to destination dashboards
2. Implement query-parameter-based navigation (e.g., ?tab=sprint)
3. Test all cross-dashboard links on mobile and desktop
4. Performance audit (query times, page load, caching strategy)
5. Polish layout edge cases (long titles, many items)
6. Final responsive testing (all viewport sizes)

## Timeline

- Phase 1 (Home Dashboard): 1–2 days
- Phase 2 (Wikis Dashboard): 1 day
- Phase 3 (Project Dashboard): 1–2 days
- Phase 4 (Topic Continuation): 0.5 days
- Phase 5 (Cross-Dashboard Linking & Polish): 1 day

## Dependencies & Notes

- Existing code to reuse:
  - `app/sessions/starter.py` (context loaders)
  - `app/knowledge/wiki_folders.py` (wiki scanner)
  - `app/wikis/` (wiki search)
  - `app/sessions/topics.py` (topic rolling summary)
  - `app/static/app.css` (design tokens, responsive helpers)
  - `app/shell.py` (sidebar nav)
  
- No new external dependencies required
- Mobile-first approach: design for 375px first, then expand to desktop
- All layouts tested with Playwright before marking done
