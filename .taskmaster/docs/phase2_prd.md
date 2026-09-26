# Phase 2: Development Orchestration Platform

## Overview

Transform AgentFlow from a CloudCLI replacement into a full development orchestration system. Phase 2 enables teams to capture backlog, plan sprints, generate tasks, execute pipelines autonomously with Ralph, and monitor progress visually.

## Phase 2 Goal

Capture backlog → Plan Sprint → Generate Tasks → Review acceptance → Release → Pipeline → Ralph → Verification → Auto commit → Visual monitoring

A reviewed Sprint can autonomously execute released Tasks through a visible, inspectable pipeline with evidence and controlled Ralph retries.

---

## P2.1 Backlog Management

Capture work items with rich content and triage workflow.

### Requirements
- Quick text capture for backlog items
- Image and file upload support
- Inbox with incoming items
- Triage workflow (review, categorize, assign priority)
- Select items for Sprint
- Enrich items with details, context, links

---

## P2.2 Sprint Planning

Plan sprints with goals, dependencies, and readiness checks.

### Requirements
- Sprint definition with goal statement
- Documentation references and context links
- Planning agent (AI-powered task breakdown)
- Proposed task graph generation
- Dependency tracking and visualization
- Sprint readiness review
- Approval workflow

---

## P2.3 Acceptance Criteria

First-class acceptance criteria and evidence tracking.

### Requirements
- Define acceptance criteria per task
- Acceptance templates for common patterns
- Reviewed and approved states
- Evidence links to artifacts/logs
- Traceability from task to verification

---

## P2.4 Pipeline Engine

Reusable, deterministic pipeline execution framework.

### Requirements
- Pipeline definition format (YAML/JSON)
- Reusable pipeline definitions
- SubPipeline composition
- Deterministic step execution
- Rigging steps (setup/verification/teardown)
- Manual approval steps
- Design-spec-driven screen mockups with human approval
- Compensation (retry/recovery strategies)

---

## P2.5 Ralph Orchestration

Autonomous agent execution with feedback loops.

### Requirements
- Multiple iteration support
- Failure feedback and error propagation
- No-progress detection and steering
- Pause and resume capability
- Automatic commit on success
- Iteration history and state tracking

---

## P2.6 Visual Monitoring

Real-time pipeline visualization and inspection.

### Requirements
- Left-to-right desktop graph visualization
- Top-to-bottom mobile graph visualization
- Right-hand inspector panel
- Prompts/replies inspection
- Test results inspection
- File changes inspection
- Artifacts display
- Raw data export
- Iteration grouping and timeline
- Compensation path visualization

---

## P2.7 Historical Replay

Inspect and replay executed pipelines.

### Requirements
- Run replay capability
- Event timeline view
- Step-by-step execution inspection
- Artifact replay and comparison
- State reconstruction at any point

---

## P2.8 Artifact Library

Centralized storage and filtering of pipeline artifacts.

### Requirements
- Artifact collection (screenshots, traces, logs, diffs)
- Filtering by type, date, source
- Originating step linkage
- Search across artifacts
- Artifact comparison tools

---

## P2.9 Prompt Library

Reusable prompt management and composition.

### Requirements
- Reusable prompt fragments
- Standard Ralph instruction blocks
- Selectable and composable instructions
- Versioning and history
- Context-file resolution

---

## P2.10 External Rigging

Integration with external systems and services.

### Requirements
- HTTP request/response integration
- SSH command execution
- Docker container orchestration
- Docker Compose stack management
- External browser application control
- Health checks and monitoring
- Service lifecycle management

---

## Phase 2 Deferred from Phase 1 Review

Carry-forward items from Phase 1 review meeting:
- Context-file resolution (alongside P2.9)
- Document review event types (alongside P2.4)
- Deferred Run/terminal improvements

---

## Success Criteria

Phase 2 is complete when:
- A backlog can be captured and triaged
- A sprint can be planned with dependencies and readiness
- Tasks can be generated and enhanced with acceptance criteria
- A pipeline can be visually designed and executed
- Ralph can autonomously iterate with failure feedback and steering
- Progress is visually monitored and results are persisted
- Historical replay allows inspection and verification of results
