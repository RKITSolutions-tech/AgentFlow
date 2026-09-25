# AgentFlow CloudCLI UX Reference

## 1. Purpose

This document records the look, feel and behaviour of CloudCLI's operational
UI so AgentFlow's Phase 1 workspace can reach the same usability. It
complements `CLOUDCLI_GAP_ANALYSIS.md` (what features are missing) by
describing how the existing ones behave.

## 2. Ground Rules

```text
AgentFlow styling and implementation are independently designed
  (HIGH_LEVEL_DESIGN.md section 23).
CloudCLI is AGPL-3.0-or-later.
This document describes patterns in our own words. Do not copy CloudCLI
  CSS, markup, icons, colours, copy text or component code.
Adopt behaviour and information architecture; choose our own palette,
  tokens, icons and wording.
```

## 3. Method and Confidence

```text
reference     CloudCLI v1.37.3, desktop viewport 1440x900, dark theme
observed      project sidebar, chat (answered and pending question,
              slash menu), Files, Source Control, Shell, Tasks, Browser,
              Settings (Agents > Permissions), session row menu
inferred      mobile behaviour, from the stylesheet breakpoints only
                (640, 768, 1024, 1280, 1400 px; coarse-pointer rules;
                standalone display mode; reduced motion)
not observed  mobile rendering, light theme, Conversations panel,
              long-running streaming states, diff views
```

## 3a. AgentFlow Today

```text
shell        top bar + collapsible primary nav; full-page navigation
project nav  link row: Files / Search / Status / History / Commit / Terminal
sessions     table lists; chat page with a single input and stop/delete
styling      plain CSS, no custom properties, one mobile breakpoint (640px)
```

## 4. Shell and Navigation

```text
layout        persistent left sidebar + main pane
sidebar       brand, refresh/new/collapse actions, Projects | Conversations
              switch, search box with a keyboard hint (Ctrl+K), project
              list, footer links (settings, help)
project row   star marker, name, session count, path; expands inline to
              list its sessions
session row   provider icon, title, relative age, status spinner or dot,
              overflow menu (rename, fork, archive or delete)
primary CTA   full-width "New Session" button at the top of the list
main header   current page title + project name, and a tab strip:
              Chat | Shell | Files | Source Control | Browser | Tasks
palette       Ctrl+K searches sessions, files and commits
side toggle   collapsible right-hand handle for a secondary panel
```

Takeaways for AgentFlow: move from page-per-feature navigation to a
persistent sidebar plus per-project tab strip, so switching between Chat,
Files, Git and Terminal never loses context.

## 5. Conversation View

```text
message flow      user bubble right-aligned with timestamp and a text/markdown
                  toggle; agent output left-aligned under the provider name
tool activity     collapsible one-line rows ("tool / argument", "Output"),
                  errors flagged inline with an Error badge
agent questions   inline item with a Running badge while pending; docked panel
                  replaces the input (see AGENT_ADAPTER.md section 12)
composer          multi-line input; bottom bar with attach, token counter,
                  queued-messages count, model chip, permission-mode toggle,
                  send button; hint line for keys
keys              Enter send (configurable Ctrl+Enter), Shift+Enter newline,
                  Tab cycles modes, / opens commands, @ mentions files
slash menu        popover above the composer, grouped with a count, each row:
                  icon, /name, small "builtin" tag, one-line description
new session       provider and model picker card, then a "next task" card
                  with a Start Task action tied to the task board
scroll            jumps to the latest content, with a control to return
markdown          rich rendering including diagrams (client bundles a
                  diagram renderer)
display toggles   "show raw parameters" and "show thinking" in quick settings
```

## 6. Files

```text
toolbar      upload, new file, new folder, refresh, expand/collapse,
             list / compact / detailed view toggle
search       inline filter box above the tree
tree table   name, size, modified (relative), permissions (rwx string)
             folders first; expandable rows; file-type icons and tints
tooltips     tab and toolbar buttons show hover labels
```

## 7. Source Control

```text
header       branch chip with ahead/behind count and a branch dropdown;
             Fetch and Push (with pending count) as primary actions;
             discard-all and refresh icons
sub-tabs     Changes (count) | Commits | Branches | Worktrees
commit box   multi-line message, hint "Ctrl+Enter to commit", selected-files
             count, Commit button
sections     Staged (n) and Changes (n), each with a bulk action
             ("Stage All"); per-file checkbox, expand chevron for inline
             diff, discard action on the row
help         collapsible "File Status Guide" legend
```

## 8. Shell

```text
header       session name with connection dot; Bypass (permissions),
             Disconnect and Restart buttons
body         full-height terminal
note         CloudCLI's Shell tab launches the agent CLI, not a general
             shell. AgentFlow's terminal (P1.6) is a general tmux-backed
             terminal, which is a deliberate difference.
```

## 9. Tasks

```text
views        kanban | list | grid toggle
controls     search, sort chips (ID, Status, Priority), filters, PRD menu,
             Add Task
columns      To Do | In Progress | Done, each with an icon, colour band and
             count; empty column shows a friendly placeholder
card         ID chip, title, dependency line, status, optional progress bar
```

## 10. Browser (agent activity monitor)

```text
purpose      watch browser sessions opened by agents
layout       large live-screenshot pane with a cursor marker; right list of
             sessions with status pills (ready, stopped), age and last action;
             detail block (status, last action, profile) and Stop / Delete
value        makes autonomous UI verification observable to the human
```

Relevant to `HIGH_LEVEL_DESIGN.md` section 17 (UI verification).

## 11. Settings

```text
container    centred modal, left category list, right pane
categories   Agents, Appearance, Git, API & Tokens, Voice, Tasks, Browser,
             Plugins, Notifications, About
agents       one tab per provider with a status dot; sub-tabs Account,
             Permissions, MCP Servers, Skills
permissions  danger-styled "skip permission prompts" switch; allowed-tools
             list with pattern entries (for example a tool name with an
             argument pattern), add field, quick-add chips, removable rows
```

## 12. Cross-Cutting Behaviour

```text
status        spinner for running, dot for attention, relative timestamps
destructive   confirmation dialog offering archive (safe) before delete
empty states  short message plus the next action
density       compact rows, clear hierarchy, dark-first
mobile        breakpoint at 768px; larger targets on coarse pointers;
              installable-app display mode supported
```

## 13. Proposed Adoption for AgentFlow

Ordered by value for the Phase 1 replacement goal. All are our own
implementation; nothing here requires copying CloudCLI code.

```text
A1  Shell: persistent sidebar (projects, sessions) + per-project tab strip
A2  Design tokens: semantic CSS custom properties (surface, text, accent,
    status colours, spacing, radius), dark-first, own palette
A3  Session rows: title, age, status indicator, overflow menu with archive
A4  Composer: model and mode chips, token counter, hint line, slash menu
A5  Pending question panel and inline question item (task 10)
A6  Files toolbar and table columns (size, modified, permissions)
A7  Source Control page: branch chip, sub-tabs, commit box, staged/changes
A8  Command palette (Ctrl+K) over sessions, files and commits
A9  Agent activity monitor for UI verification (decide with G9)
```

## 14. Where AgentFlow Should Differ

```text
- terminal is a general persistent terminal, not an agent CLI launcher
- Runs, Backlog and Sprints are first-class navigation, not a Tasks tab
- sessions carry role, execution provider and Run linkage
- application login remains out of scope unless the deployment changes
- Codex first; question and approval UI must degrade gracefully where an
  adapter has no structured prompts
```

## 15. Open Questions

```text
Sidebar plus tabs, or keep page navigation with a richer project header?
Which of A1-A9 belong in Phase 1 versus Phase 2?
Do we want a light theme at launch, or dark only?
Should the Tasks board be a Task Master view now and a Backlog view later?
```
