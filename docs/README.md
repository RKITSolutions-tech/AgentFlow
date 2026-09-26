# AgentFlow Design Pack

This archive contains the AgentFlow design documents produced to date.

## Documents

1. `HIGH_LEVEL_DESIGN.md`
2. `DOMAIN_AND_ORCHESTRATION_DESIGN.md`
3. `SPRINT_PLANNING_AND_BACKLOG.md`
4. `PIPELINE_ENGINE.md`
5. `PIPELINE_VISUALISATION.md`
6. `RUN_AND_RALPH.md`
7. `EXECUTION_PROVIDER.md`
8. `AGENT_ADAPTER.md`
9. `PHASED_DELIVERY_PLAN.md`
10. `TASK_INTELLIGENCE.md`
11. `DOCUMENT_LIFECYCLE.md`
12. `PHASE2_PLANNING.md`

## Delivery Priority

### Phase 1

Replace the useful CloudCLI-style capabilities first so AgentFlow becomes usable early:

```text
Projects
Sessions
Codex
Files
Git
Persistent Terminal
Basic Runs
Mobile/LAN/Tailscale access
```

### Phase 2

Add the richer AgentFlow capabilities:

```text
Backlog
Sprint Planning
Acceptance
Pipeline Engine
Design Mockup Review
Ralph
Visual Pipeline Monitoring
Replay
Artifacts
Prompt Library
Rigging / External Systems
```

### Phase 3

Advanced authoring, concurrency, additional agents and optimisation.

## Canonical Design Decisions

- AgentFlow runs directly on the Linux host. Docker is an optional execution target for project workloads.
- Focused design documents own their interfaces and state definitions; broader documents reference them.
- SQLite stores application records and artifact metadata. Artifact content, including transcripts and large logs, is stored on the filesystem.
- Phase 1 includes basic Runs and execution history. Ralph iteration, retry policy, steering and auto-commit are Phase 2 capabilities.
- The application binds to loopback by default. Remote access requires an explicitly selected Tailscale address and network access controls.

## Core Delivery Flow

```text
Backlog
→ Sprint Planning
→ Design / Enrichment
→ Task Graph
→ Acceptance Review
→ Release
→ Pipeline
→ Ralph
→ Verification
→ Auto Commit
→ Sprint Progress
```
