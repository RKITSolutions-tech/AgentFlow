# Dashboard Responsive Design Testing Guide

This document outlines the responsive design testing for the three AgentFlow dashboards: Home, Wikis, and Project. All dashboards follow a mobile-first responsive approach with breakpoints at 375px, 768px, 1024px, and 1280px.

## Testing Environment Setup

### Prerequisites
- Python 3.12+
- All dependencies from `requirements.txt` installed
- Playwright browsers installed: `playwright install`

### Running the App
```bash
cd /home/devadmin/git/AgentFlow
python3 -m app --host 127.0.0.1 --port 5000
```

The app will be available at `http://127.0.0.1:5000`

## Dashboard URLs

- **Home Dashboard**: `/dashboards/home`
- **Wikis Dashboard**: `/dashboards/wikis`
- **Project Dashboard**: `/dashboards/project/<project_id>`

---

## 1. Home Dashboard - Responsive Testing

### Purpose
Quick-access hub for resuming recent work. Shows recent sessions, active pipelines, and recent backlog items.

### Viewport Sizes to Test

#### Mobile (375px width)
- [ ] Single-column layout: all sections stack vertically
- [ ] Cards are full-width with no horizontal scrolling
- [ ] Touch targets (buttons, links) are at least 44px tall
- [ ] Text is readable without zooming
- [ ] Padding/margins are appropriate for mobile (not cramped)
- [ ] Session/pipeline/backlog cards show title and one-liner description
- [ ] Status badges are visible and not truncated

**Expected Behavior:**
```
┌─ Recent Sessions
│  ├─ [Session Title] [Resume]
│  ├─ [Session Title] [Resume]
│  └─ [See all]
├─ Active Pipelines
│  ├─ [Pipeline] 🟢 [View]
│  └─ [Pipeline] 🟡 [View]
└─ Recent Backlog
   ├─ [Item] [Open]
   └─ [Item] [Open]
```

#### Tablet (768px width)
- [ ] 2-column grid layout for cards
- [ ] Sections flow in 2-column grid
- [ ] Same touch targets maintained
- [ ] Content still readable without horizontal scroll

#### Desktop (1024px+ width)
- [ ] 3-column grid layout
- [ ] All sections visible at once
- [ ] Hover states work on links/buttons
- [ ] Spacing feels generous and balanced
- [ ] Max-width maintained (should be ~1280px)

### Content Variations to Test
- [ ] Empty state (no sessions, pipelines, backlog items)
- [ ] With data: 5 items in each section (the limit)
- [ ] Mixed data: some sections full, others empty
- [ ] Long titles: truncation with ellipsis working

---

## 2. Wikis Dashboard - Responsive Testing

### Purpose
Browse, search, and manage knowledge bases. Wikis organized by project vs. shared.

### Viewport Sizes to Test

#### Mobile (375px width)
- [ ] Search box is visible and full-width
- [ ] Sidebar navigation is **hidden**
- [ ] Wiki list displays as cards in single column
- [ ] Each card shows: wiki name, type (Project/Shared), Browse/Search buttons
- [ ] Buttons stack vertically within each card
- [ ] No horizontal scrolling
- [ ] Search functionality works (filters wiki list)

**Expected Behavior:**
```
┌─ Wikis Dashboard
├─ [Search wikis...] (full-width input)
├─ [Project A] [Browse] [Search]
├─ [Project B] [Browse] [Search]
├─ [Shared]    [Browse] [Search]
└─ [+ Add Wiki]
```

#### Tablet (768px width)
- [ ] Sidebar appears on the left (if space allows)
- [ ] Sidebar is narrower or collapses
- [ ] Wiki list remains in single column or 2-column
- [ ] No horizontal scrolling

#### Desktop (1024px+ width)
- [ ] **Sidebar layout active**: left sidebar (250px) + preview panel (right)
- [ ] Sidebar shows: search box, project wikis list, shared wikis list
- [ ] Clicking sidebar items highlights them
- [ ] Right panel shows wiki cards for preview
- [ ] Smooth scrolling between sidebar and preview
- [ ] "Add Wiki" button visible in header

**Expected Behavior:**
```
┌──────────────────────────────────────┐
│ ┌─ Sidebar  │ ┌─ Preview Panel      │
│ │ [Search]  │ │ Available Wikis     │
│ │ Project:  │ │ [Project A]         │
│ │ • Wiki A  │ │ [Browse] [Search]   │
│ │ • Wiki B  │ │                     │
│ │ Shared:   │ │ [Project B]         │
│ │ • Wiki C  │ │ [Browse] [Search]   │
│ └───────────┘ │                     │
│               │ [Shared]            │
│               │ [Browse] [Search]   │
│               └─────────────────────┘
└──────────────────────────────────────┘
```

### Content Variations to Test
- [ ] Empty state (no wikis)
- [ ] Only project wikis (no shared)
- [ ] Only shared wikis (no project)
- [ ] Mixed project and shared wikis
- [ ] Long wiki names (truncation/wrapping)
- [ ] Search filtering (type search term, verify results filter)

---

## 3. Project Dashboard - Responsive Testing

### Purpose
Per-project hub: sprint status, backlog summary, pipeline overview.

### Viewport Sizes to Test

#### Mobile (375px width)
- [ ] Single-column layout: sprint → backlog → pipelines
- [ ] Project title is visible and readable
- [ ] "Start Session" and "Settings" buttons stack or are on separate rows
- [ ] Sprint section shows: name, status badge, task counts
- [ ] Backlog shows: status counts in a grid (Inbox, Ready, etc.)
- [ ] Pipeline cards show: name, status badge, actions
- [ ] No horizontal scrolling
- [ ] All text is readable without zoom
- [ ] Touch targets are 44px+ minimum

**Expected Behavior:**
```
┌─ Project: Name
├─ [Start Session] [Settings]
├─ Current Sprint
│  ├─ Sprint Name (Status)
│  ├─ 5 tasks | 3 done | 2 in progress
│  └─ [View Sprint] [Add Task]
├─ Backlog Status
│  ├─ Inbox: 2
│  ├─ Ready: 8
│  ├─ In Progress: 3
│  └─ [View Backlog] [+ New Item]
└─ Active Pipelines
   ├─ [Pipeline X] 🟢 [View] [Stop]
   ├─ [Pipeline Y] 🟡 [View]
   └─ [View All...]
```

#### Tablet (768px width)
- [ ] 2-column grid: sprint + backlog | pipelines
- [ ] Sprint and backlog side-by-side
- [ ] Pipelines span full width below
- [ ] Same readability and touch targets
- [ ] Proper spacing between columns

#### Desktop (1024px+ width)
- [ ] **3-column grid active**: sprint | backlog | pipelines
- [ ] All three sections visible at once
- [ ] Balanced column widths
- [ ] Hover states work on buttons/links
- [ ] Status badges properly styled
- [ ] Clear visual hierarchy

**Expected Behavior:**
```
┌──────────────────────────────────────────────────┐
│ Project: Name       [Start Session] [Settings]   │
├─ Sprint ──┬─ Backlog ──┬─ Pipelines           │
│ • Sprint  │ • Inbox: 2 │ • Pipeline X 🟢       │
│   Name    │ • Ready: 8 │   [View] [Stop]       │
│   (ACTIVE)│ • In Prog: │                       │
│ 5 tasks   │   3        │ • Pipeline Y 🟡       │
│ 3 done    │ • Done: 12 │   [View] [Resume]     │
│ [View]    │ [View]     │ [View All...]         │
└───────────┴────────────┴──────────────────────┘
```

### Content Variations to Test
- [ ] No active sprint (shows "No Active Sprint" + "Start Sprint" button)
- [ ] With active sprint: shows status and task breakdown
- [ ] Empty backlog (shows "No backlog items" message)
- [ ] With backlog items: shows status breakdown
- [ ] No active pipelines (shows "No Active Pipelines" + "Run Pipeline" button)
- [ ] With active pipelines: shows status badges and actions
- [ ] Project settings accessible from header button

---

## CSS Responsive Classes & Utilities

### Media Query Breakpoints (in base.html)
```css
/* Mobile-first: single column */
.dashboard-grid { grid-template-columns: 1fr; }

/* Tablet: 2 columns at 768px */
@media (min-width: 768px) {
  .dashboard-grid { grid-template-columns: repeat(2, 1fr); }
}

/* Desktop: 3 columns at 1024px */
@media (min-width: 1024px) {
  .dashboard-grid { grid-template-columns: repeat(3, 1fr); }
}

/* Wikis sidebar at 1024px */
@media (min-width: 1024px) {
  .dashboard-sidebar-layout {
    grid-template-columns: 250px 1fr;
  }
}

/* Project dashboard grid at various sizes */
@media (min-width: 768px) {
  .project-dashboard-grid {
    grid-template-columns: 1fr 1fr; /* 2 columns at tablet */
  }
}

@media (min-width: 1024px) {
  .project-dashboard-grid {
    grid-template-columns: repeat(3, 1fr); /* 3 columns at desktop */
  }
}
```

### CSS Classes Used
- `.dashboard-container` - max-width wrapper, responsive padding
- `.dashboard-grid` - responsive grid for home dashboard
- `.dashboard-cards` - grid for card collections
- `.dashboard-card` - individual card with flex layout
- `.dashboard-sidebar-layout` - 2-column sidebar layout (Wikis desktop)
- `.dashboard-sidebar` - left sidebar navigation
- `.dashboard-preview` - right preview panel
- `.project-dashboard-grid` - project dashboard 3-column layout
- `.status-badge` - status indicator (running, paused, blocked, done)
- `.status-summary` - grid of status counts
- `.status-item` - individual status count
- `.card-actions` - action buttons within card
- `.card-action-btn` - individual action button

---

## Automated Testing

### Run Dashboard Tests
```bash
cd /home/devadmin/git/AgentFlow
python3 -m pytest tests/test_dashboards.py -v
```

### Tests Included
1. `test_home_dashboard_empty_state` - Empty state for all sections
2. `test_home_dashboard_shows_recent_items` - With data display
3. `test_home_dashboard_excludes_non_general_sessions_and_inactive_pipelines` - Filtering
4. `test_home_dashboard_limits_recent_sessions_to_five` - Limit enforcement
5. `test_wikis_dashboard_loads` - Route loads successfully
6. `test_wikis_dashboard_empty_state` - Empty wiki list
7. `test_project_dashboard_loads` - Route loads successfully
8. `test_project_dashboard_shows_sections` - All sections present
9. `test_project_dashboard_no_sprint_empty_state` - No active sprint
10. `test_project_dashboard_with_backlog_items` - Backlog status summary
11. `test_project_dashboard_with_active_pipeline` - Active pipeline display

---

## Manual Testing Checklist

### Before Shipping
- [ ] Home dashboard responsive at all sizes
- [ ] Wikis dashboard responsive at all sizes (sidebar at 1024px+)
- [ ] Project dashboard responsive at all sizes (3-col at 1024px+)
- [ ] All links are clickable and navigate correctly
- [ ] No console errors in browser devtools
- [ ] No horizontal scrolling at any viewport
- [ ] Touch targets are 44px+ on mobile
- [ ] Text is readable without zooming on mobile
- [ ] Colors have sufficient contrast (WCAG AA)
- [ ] Keyboard navigation works (Tab through elements)
- [ ] Forms can be filled without issues
- [ ] Status badges display correctly with colors
- [ ] Empty states have helpful messages
- [ ] All buttons have hover states
- [ ] Search filtering works (Wikis dashboard)
- [ ] No layout shift when content loads

### Browser Testing
- [ ] Chrome/Chromium at 375px, 768px, 1024px, 1280px
- [ ] Firefox at same sizes
- [ ] Mobile Safari (if available)
- [ ] Mobile Chrome (if available)

### Performance
- [ ] Home dashboard loads in <500ms (with data cached)
- [ ] Wikis dashboard loads in <500ms
- [ ] Project dashboard loads in <500ms
- [ ] No layout reflows during load
- [ ] Smooth scrolling (no jank)

---

## Known Limitations & Future Work

1. **Wikis Search**: Currently client-side filtering only. Could be enhanced to support backend search.
2. **Wiki Preview**: Desktop shows full card list. Could add preview panel showing wiki contents.
3. **Project Stats**: Dashboard shows basic status counts. Could add charts/graphs for trends.
4. **Responsive Images**: No images currently, but if added, ensure lazy-loading and srcset.
5. **Pagination**: Home dashboard limits to 5 items. Could add "See All" links with pagination.
6. **Caching**: Wiki list and project stats could be cached to improve performance.

---

## Design Decisions

### Why Mobile-First?
Mobile is the constrained environment. Building mobile first ensures the design works everywhere, then enhancing for larger screens.

### Why These Breakpoints?
- **375px**: Smallest modern smartphone (iPhone SE, etc.)
- **768px**: Tablet portrait or small laptop
- **1024px**: Tablet landscape or small desktop
- **1280px**: Standard desktop width (design doc maximum)

### Why Grid Layout?
CSS Grid provides responsive layout without media query hacks. Cards naturally reflow based on container width.

### Why Sidebar at 1024px?
Sidebar navigation provides better discoverability on wider screens. Below 1024px, it would take too much space and should be collapsed or hidden.

---

## Troubleshooting

### Content Not Showing
- Check browser console for JavaScript errors
- Verify database has test data (sessions, pipelines, backlog items)
- Check Flask logs for template rendering errors

### Layout Issues
- Clear browser cache (Ctrl+Shift+Del)
- Check CSS is loading (DevTools > Network tab)
- Verify viewport meta tag in base template: `<meta name="viewport" content="width=device-width, initial-scale=1">`

### Touch Targets Too Small
- Minimum size is 44px (defined as `--tap` CSS variable)
- If buttons appear smaller, check CSS isn't overriding padding
- Use DevTools to measure element size

### Sidebar Not Appearing (Wikis)
- Check viewport is >= 1024px
- Verify `.dashboard-sidebar-layout` is using correct media query
- Check `@media (min-width: 1024px)` styles are applied

---

## Related Documentation

- `docs/THREE_DASHBOARDS_DESIGN.md` - Design specifications and data models
- `app/dashboards/routes.py` - Dashboard route implementations
- `app/templates/dashboards/` - Template files
- `app/static/app.css` - Design tokens and responsive utilities
