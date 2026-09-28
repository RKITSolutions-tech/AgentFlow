# Ralph Research on Failure Design Document

**Status:** In Progress (Task 47)  
**Date:** 2026-09-28  
**Author:** Claude Haiku 4.5

## Overview

Task 47 adds opt-in research analysis to Ralph runs when no progress is made or repeated failures occur. The research system runs an optional RESEARCH pipeline step that analyzes the failure, generates findings, stores them as artifacts, and presents them to the user for optional steering input.

## Key Assumptions

1. **Database Schema**: The `planned_work_items` table will be extended with a `research_config` JSON column (default `{"enabled": false, "threshold": 2}`). A new `ralph_research_history` table tracks all research triggers and steering applications for audit.

2. **Research Trigger**: A consecutive failure counter in `run_state` (tracked per iteration) triggers research when:
   - `research_on_failure = true` AND
   - `consecutive_failures >= failure_threshold` OR
   - iteration produces no code changes (regardless of threshold)

3. **No Automatic Application**: Research findings are displayed but never modify behavior automatically. Users must explicitly click "Use findings as steering" to apply them. This ensures human control.

4. **Research Step Integration**: A RESEARCH step is spawned via the PipelineEngine with context including `current_run_state`, `failure_analysis`, `repository_code`, and `recent_logs`. The step produces a JSON report with sections for root causes, patterns, and suggestions.

5. **UI Responsiveness**: 
   - Mobile (375px): Report and buttons stack vertically, scrollable
   - Desktop (1280px): Report and buttons inline or side-by-side, 44px+ touch targets
   - Shared AJAX handlers for button clicks

6. **Default Behavior**: 
   - Research is opt-in (disabled by default)
   - Failure threshold defaults to 2 consecutive failures
   - Research cannot interfere with active Ralph iteration

## Subtask Breakdown

### 47.1 - Extend Ralph Run Model with Research Configuration
- Add `research_config: JSON` column to `planned_work_items` table
- Create `ralph_research_history` audit table
- Extend `RalphRun` model with research tracking fields:
  - `enabled_research: boolean`
  - `failure_threshold: int`
  - `consecutive_failures: int` (in run_state)
  - `research_report_id: string` (link to artifact)
  - `research_report_presented: boolean`

### 47.2 - Implement Failure Counter and Detection
- Track `consecutive_failures` in run_state
- Increment on failed/no-progress iterations
- Reset on successful iterations
- Detect "no progress" when iteration produces no code changes

### 47.3 - Implement Research Trigger and RESEARCH Step
- Spawn RESEARCH step when threshold reached
- Pass context to PipelineEngine
- Store result as Artifact with tags `['ralph_research', 'iteration_N', 'failure_analysis']`
- Update run_state with research_report_id and research_report_presented=false

### 47.4 - Create Research Prompt Template and History Table
- Add RESEARCH prompt to `prompts` table with role='RESEARCH'
- Format prompt for failure analysis (JSON output expected)
- Create audit trail in `ralph_research_history` table

### 47.5 - Build Ralph Research UI
- Display research report in Ralph execution view
- Render report with expandable sections
- Add "Use findings as steering" and "Dismiss" buttons
- Implement AJAX handlers for button clicks
- Ensure mobile/desktop responsive layout

## Implementation Notes

- All code changes follow CLAUDE.md guidelines
- Security: Research findings stored as artifacts follow same path-traversal checks
- Testing: Both unit and integration tests required (see task test strategy)
- No-op guardrail: Research never affects iteration without explicit user action

## Related Tasks

- Task 43: RESEARCH pipeline steps (dependency ✓)
- Task 45: RESEARCH agent capability (dependency ✓)
- Task 35: Replay follow-ups (dependency ✓)
- Task 31: Real-browser verification (dependency ✓)
- Task 28: Project execution locks (dependency ✓)

