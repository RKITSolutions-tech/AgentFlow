# AgentFlow CloudCLI Gap Analysis

## 1. Purpose

AgentFlow Phase 1 is defined as a CloudCLI replacement
(`PHASED_DELIVERY_PLAN.md` §2). This document records where CloudCLI offers
capabilities that the AgentFlow design does not yet cover, so each gap can be
consciously accepted, deferred or scheduled rather than discovered after
cut-over.

`HIGH_LEVEL_DESIGN.md` §23 requires AgentFlow's implementation and styling to
be independently designed. CloudCLI is AGPL-3.0-or-later, so this document is
a feature inventory only: no CloudCLI code or markup is to be copied.

## 2. Method and Limits

```text
reference          CloudCLI v1.37.3, self-hosted, reviewed 2026-09-25
sources            installed server routes (API surface per module),
                   the running UI (project view, session view, composer,
                   a live AskUserQuestion), AgentFlow docs/, app/ routes
not reviewed       every UI screen; client-only behaviour is inferred from
                   routes and the screens above
AgentFlow status   judged from routes and templates in app/, not from
                   running every feature
```

## 3. Baseline: Covered by the Phase 1 Design

```text
Projects (create, edit, delete, repositories)
Codex sessions (start, resume, stop, stream, history)
Files (browse, view, search, basic editing)
Git (status, diff, log, stage, unstage, commit)
Persistent terminal
Model catalog
```

## 4. Gaps

Priority is a proposal for discussion, not a decision.

### G1. Interactive agent prompts (proposed: high)

CloudCLI renders structured clarifying questions and permission prompts as a
docked input panel. See `AGENT_ADAPTER.md` §12 for the observed behaviour.
The design mentions approvals in one line and had no clarifying-question
model. Nothing is built.

### G2. Notifications (proposed: high)

CloudCLI supports web push with per-channel preferences. The design has none.
A run or session blocked on a question or approval is otherwise invisible
unless the page is open.

### G3. Git beyond the basics (proposed: high)

```text
CloudCLI                                   AgentFlow design
branch list / checkout / create / delete   current branch only
push / pull / fetch / publish              absent
discard / delete untracked / revert        absent
remote status                              absent
AI-generated commit message                absent
```

Agents work on branches and Ralph is designed around worktrees, so branch
switching and pushing from the UI is likely to be needed early.

### G4. Session management (proposed: medium-high)

```text
rename, archive / restore, fork
cross-session search
recent and running session views
token usage per session
switch model / effort mid-session
```

The design has list, start, resume and stop only. CloudCLI already shows 62
sessions for AgentFlow alone, most untitled, so rename, archive and search
matter quickly after cut-over.

### G5. File operations (proposed: medium)

CloudCLI can create, rename, delete and upload files and folders. The design
says "basic editing" only (`PHASED_DELIVERY_PLAN.md` P1.4).

### G6. Composer features (proposed: medium)

```text
/ slash commands
@ file mentions
attachments and images
permission modes (cycled with Tab)
scheduled messages
voice input (low)
```

Attachments appear in the design only for backlog capture.

### G7. Project management (proposed: medium-low)

```text
star / pin, rename, archive / restore
clone from URL with progress
filesystem browse when adding a project
worktree create / merge / remove UI
```

Worktrees are designed only as a Ralph execution mechanism.

### G8. Extensibility (proposed: low for Phase 1)

MCP server management, skills management and plugins. `HIGH_LEVEL_DESIGN.md`
mentions "basic MCP visibility and configuration"; skills management and
plugins are not designed.

### G9. Embedded browser (proposed: unclear)

CloudCLI has an agent-controllable browser tab, exposed to agents as an MCP
server. AgentFlow's UI verification (`HIGH_LEVEL_DESIGN.md` §17) may need an
equivalent; it is not decided.

### G10. Task Master tab (deliberate divergence)

CloudCLI integrates Task Master, including PRD templates. AgentFlow's
`TASK_INTELLIGENCE.md` covers the same ground differently. No action beyond
confirming the divergence is intended.

### G11. Auth and credentials (deliberate divergence, revisit)

CloudCLI has login, API keys and a credential store. AgentFlow deliberately
has none (`HIGH_LEVEL_DESIGN.md` §20), relying on loopback binding and
Tailscale/LAN controls. Worth revisiting if the instance is reachable beyond a
single trusted user.

### G12. Polish (proposed: low)

Dark mode ("if straightforward" in the plan) and installable-app support.

## 5. Risks Worth Deciding Early

```text
G1 + G2   an autonomous run can block silently
G3        cannot switch branch or push from the UI
G4        untitled-session sprawl once real use begins
G11       single-trusted-user assumption if exposed remotely
```

## 6. Task Master Tasks

```text
G1 + G2   task 10  (high)
G3        task 11  (high)
G4        task 12  (medium)
G5        task 13  (medium)
G6        task 14  (medium)
G7        task 15  (low)
G8 - G12  task 16  (low, decision task)
```

## 7. Open Decisions

```text
Which gaps are accepted as out of scope for Phase 1?
Which belong in Phase 1 versus Phase 2?
Is a clarifying question a distinct event kind (leaning: yes, see
  AGENT_ADAPTER.md §12)?
Does G9 belong to UI verification or to the session workspace?
```
