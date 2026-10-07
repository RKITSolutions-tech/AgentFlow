from __future__ import annotations

import os
import sqlite3

import click
from flask import Flask, current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    starred INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project_id, path)
);

CREATE TABLE IF NOT EXISTS execution_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    working_directory TEXT NOT NULL,
    environment_profile TEXT NOT NULL DEFAULT '{}',
    mounts TEXT,
    network TEXT,
    status TEXT NOT NULL DEFAULT 'CREATED',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS processes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id INTEGER NOT NULL REFERENCES execution_contexts(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_process_id INTEGER,
    command_summary TEXT NOT NULL,
    working_directory TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'CREATED',
    started_at TEXT,
    completed_at TEXT,
    exit_code INTEGER
);

CREATE TABLE IF NOT EXISTS process_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id INTEGER NOT NULL REFERENCES execution_contexts(id) ON DELETE CASCADE,
    process_id INTEGER REFERENCES processes(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    stream TEXT,
    data TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_type TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'GENERAL',
    external_session_id TEXT,
    execution_provider TEXT NOT NULL DEFAULT 'host',
    execution_target TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'STARTING',
    metadata TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_activity_at TEXT NOT NULL DEFAULT (datetime('now')),
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    reviewed_git_sha TEXT
);

CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    redacted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agent_events_session ON agent_events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_project_role ON agent_sessions(project_id, role, last_activity_at DESC);

CREATE TABLE IF NOT EXISTS agent_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    event_id INTEGER REFERENCES agent_events(id) ON DELETE SET NULL,
    external_id TEXT,
    header TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL,
    multi_select INTEGER NOT NULL DEFAULT 0,
    options TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'PENDING',
    answer TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    channel TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    read_at TEXT,
    delivered_at TEXT
);

CREATE TABLE IF NOT EXISTS notification_preferences (
    kind TEXT NOT NULL,
    channel TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (kind, channel)
);

CREATE TABLE IF NOT EXISTS scheduled_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    send_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'SENT', 'FAILED', 'CANCELLED')),
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS clone_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    destination TEXT NOT NULL,
    project_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    context_id INTEGER,
    process_id INTEGER,
    status TEXT NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING', 'DONE', 'FAILED', 'CANCELLED')),
    message TEXT NOT NULL DEFAULT '',
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    git_diff TEXT NOT NULL DEFAULT '',
    test_status TEXT,
    test_exit_code INTEGER,
    test_output TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS terminal_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    tmux_session_name TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL DEFAULT '',
    working_directory TEXT NOT NULL,
    pipe_path TEXT NOT NULL,
    pipe_offset INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_activity_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS terminal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_session_id INTEGER NOT NULL REFERENCES terminal_sessions(id) ON DELETE CASCADE,
    data TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS model_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    base_url TEXT NOT NULL DEFAULT '',
    api_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider, model_id)
);

-- Federation registry (remote AgentFlow instances this one can drive sessions
-- on, e.g. a second instance on a different host). `token` is the bearer
-- credential *this* instance presents to that remote's federation API
-- (app/federation/routes.py) -- it must match that remote's own
-- AGENTFLOW_FEDERATION_TOKEN.
CREATE TABLE IF NOT EXISTS remote_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    token TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT,
    last_status TEXT NOT NULL DEFAULT 'UNKNOWN'
);

-- Folder-backed wikis (docs/WIKI_INTEGRATION_AND_PRESENTATION.md §14): a folder of
-- markdown files either on this host ('local') or on a registered remote
-- instance ('remote', read through that instance's federation API so its own
-- ALLOWED_PROJECT_ROOTS check applies). Separate from knowledge_entries, which
-- is the database-backed research wiki. `project_id` NULL = shared by every
-- project; agent sessions only see their own project's wikis plus shared ones.
CREATE TABLE IF NOT EXISTS wiki_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT NOT NULL CHECK(location IN ('local', 'remote')),
    path TEXT NOT NULL,
    instance_id INTEGER REFERENCES remote_instances(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK((location = 'remote') = (instance_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    repo_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    chain_id TEXT,
    doc_type TEXT NOT NULL CHECK(doc_type IN ('DESIGN', 'IMPLEMENTATION', 'TESTING', 'PLAN', 'STANDARD')),
    path TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'IN_REVIEW', 'APPROVED', 'SUPERSEDED')),
    current_git_sha TEXT,
    supersedes_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS document_refs (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    referenced_document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    relationship TEXT NOT NULL CHECK(relationship IN ('DERIVES_FROM', 'REFERENCES', 'SUPERSEDES')),
    PRIMARY KEY (document_id, referenced_document_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'CREATED',
    status_reason TEXT NOT NULL DEFAULT '',
    restart_of INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS run_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    command_redacted INTEGER NOT NULL DEFAULT 0,
    timeout_seconds REAL,
    collect TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'PENDING',
    process_id INTEGER REFERENCES processes(id) ON DELETE SET NULL,
    exit_code INTEGER,
    prompt TEXT,
    reply TEXT,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(run_id, seq)
);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id INTEGER REFERENCES run_steps(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id INTEGER REFERENCES run_steps(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    redacted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backlog_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'INBOX' CHECK(status IN ('INBOX', 'TRIAGED', 'SELECTED', 'PLANNING', 'PLANNED', 'READY', 'RELEASED', 'ARCHIVED', 'REJECTED')),
    priority TEXT CHECK(priority IS NULL OR priority IN ('LOW', 'MEDIUM', 'HIGH')),
    sprint_id INTEGER,
    created_by TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    source_reference TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backlog_items_project_status ON backlog_items(project_id, status);
CREATE INDEX IF NOT EXISTS idx_backlog_items_priority ON backlog_items(priority);
CREATE INDEX IF NOT EXISTS idx_backlog_items_sprint ON backlog_items(sprint_id);

CREATE TABLE IF NOT EXISTS backlog_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backlog_item_id INTEGER NOT NULL REFERENCES backlog_items(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('IMAGE', 'FILE', 'LINK')),
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backlog_attachments_item ON backlog_attachments(backlog_item_id);

CREATE TABLE IF NOT EXISTS backlog_triage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backlog_item_id INTEGER NOT NULL REFERENCES backlog_items(id) ON DELETE CASCADE,
    old_status TEXT,
    new_status TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    changed_by TEXT NOT NULL DEFAULT '',
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backlog_history_item ON backlog_triage_history(backlog_item_id);

CREATE TABLE IF NOT EXISTS sprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'PLANNING', 'REVIEW', 'READY', 'EXECUTING', 'VERIFYING', 'COMPLETE', 'CANCELLED')),
    planning_profile TEXT NOT NULL DEFAULT 'STANDARD_FEATURE',
    start_date TEXT,
    target_date TEXT,
    planning_session_id INTEGER REFERENCES agent_sessions(id) ON DELETE SET NULL,
    approved_at TEXT,
    approved_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sprints_project ON sprints(project_id, status);

CREATE TABLE IF NOT EXISTS sprint_document_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sprint_id INTEGER NOT NULL REFERENCES sprints(id) ON DELETE CASCADE,
    reference TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS planned_work_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sprint_id INTEGER NOT NULL REFERENCES sprints(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    acceptance TEXT NOT NULL DEFAULT '[]',
    estimate TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'SUGGESTED' CHECK(status IN ('SUGGESTED', 'REVIEWED', 'APPROVED', 'REJECTED')),
    task_state TEXT NOT NULL DEFAULT 'DRAFT',
    released_at TEXT,
    verification_pipeline TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_planned_work_sprint ON planned_work_items(sprint_id, seq);

CREATE TABLE IF NOT EXISTS planned_work_dependencies (
    work_item_id INTEGER NOT NULL REFERENCES planned_work_items(id) ON DELETE CASCADE,
    depends_on_id INTEGER NOT NULL REFERENCES planned_work_items(id) ON DELETE CASCADE,
    PRIMARY KEY (work_item_id, depends_on_id)
);

CREATE TABLE IF NOT EXISTS planned_work_backlog_links (
    work_item_id INTEGER NOT NULL REFERENCES planned_work_items(id) ON DELETE CASCADE,
    backlog_item_id INTEGER NOT NULL REFERENCES backlog_items(id) ON DELETE CASCADE,
    PRIMARY KEY (work_item_id, backlog_item_id)
);

CREATE TABLE IF NOT EXISTS sprint_readiness_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sprint_id INTEGER NOT NULL REFERENCES sprints(id) ON DELETE CASCADE,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PASS', 'FAIL', 'WARN')),
    details TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_locks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    owner_type TEXT NOT NULL CHECK(owner_type IN ('ralph_run', 'pipeline_run', 'manual_run')),
    owner_id INTEGER NOT NULL,
    repository_id INTEGER,  -- NULL = the whole project; else only that repository
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    released_at TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'STALE', 'RELEASED'))
);
-- Runs that chose to wait for a busy project, first come first served (RUN_AND_RALPH §22).
CREATE TABLE IF NOT EXISTS project_lock_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    repository_id INTEGER,
    owner_type TEXT NOT NULL CHECK(owner_type IN ('ralph_run', 'pipeline_run', 'manual_run')),
    owner_id INTEGER NOT NULL,
    enqueued_at TEXT NOT NULL,
    polled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'WAITING' CHECK(status IN ('WAITING', 'GRANTED', 'CANCELLED', 'EXPIRED'))
);
CREATE INDEX IF NOT EXISTS idx_project_lock_queue_waiting ON project_lock_queue(project_id, status);

-- Prompt library (docs/PHASE2_PLANNING.md §2, PHASED_DELIVERY_PLAN P2.9). Global to
-- the installation, not per project.
CREATE TABLE IF NOT EXISTS prompt_fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'instruction' CHECK(category IN ('instruction', 'context', 'example')),
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prompt_fragment_versions (
    fragment_id INTEGER NOT NULL REFERENCES prompt_fragments(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    changelog TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (fragment_id, version)
);
CREATE TABLE IF NOT EXISTS prompt_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    fragments TEXT NOT NULL DEFAULT '[]',
    variables TEXT NOT NULL DEFAULT '{}',
    base_template_id INTEGER REFERENCES prompt_templates(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ralph_instruction_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    applies_to TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL CHECK(source_type IN ('pipeline_step', 'ralph_iteration')),
    source_id INTEGER NOT NULL,
    template_id INTEGER REFERENCES prompt_templates(id) ON DELETE SET NULL,
    template_name TEXT NOT NULL DEFAULT '',
    effective_prompt TEXT NOT NULL,
    context_files TEXT NOT NULL DEFAULT '[]',
    variables_used TEXT NOT NULL DEFAULT '{}',
    blocks_included TEXT NOT NULL DEFAULT '[]',
    redacted INTEGER NOT NULL DEFAULT 0,
    assembled_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execution_prompts_source ON execution_prompts(source_type, source_id);
-- Per-project overrides of a library template body or a Ralph block (content and/or
-- on/off). No override row = the global text applies.
CREATE TABLE IF NOT EXISTS project_prompt_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('template', 'block')),
    target_id INTEGER NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    enabled INTEGER,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, kind, target_id)
);

CREATE TABLE IF NOT EXISTS pipelines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'CUSTOM',
    enabled INTEGER NOT NULL DEFAULT 1,
    current_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pipelines_project_name ON pipelines(project_id, name);

CREATE TABLE IF NOT EXISTS pipeline_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(pipeline_id, version)
);

CREATE TABLE IF NOT EXISTS pipeline_elements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL REFERENCES pipeline_versions(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    name TEXT NOT NULL,
    element_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled TEXT NOT NULL DEFAULT 'ENABLED' CHECK(enabled IN ('ENABLED', 'DISABLED', 'SKIPPED')),
    phase TEXT NOT NULL DEFAULT 'MAIN' CHECK(phase IN ('SETUP', 'MAIN', 'TEARDOWN')),
    configuration TEXT NOT NULL DEFAULT '{}',
    compensation_policy TEXT NOT NULL DEFAULT '{}',
    depends_on TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(version_id, name)
);

CREATE TABLE IF NOT EXISTS pipeline_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    pipeline_version INTEGER NOT NULL,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    repository_id INTEGER REFERENCES repositories(id) ON DELETE SET NULL,
    sprint_id INTEGER REFERENCES sprints(id) ON DELETE SET NULL,
    parent_execution_id INTEGER REFERENCES pipeline_executions(id) ON DELETE SET NULL,
    run_id INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'RUNNING', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED')),
    reason TEXT NOT NULL DEFAULT '',
    resolved_configuration TEXT NOT NULL DEFAULT '{}',
    variables TEXT NOT NULL DEFAULT '{}',
    cursor INTEGER NOT NULL DEFAULT 0,
    waiting_step_id INTEGER,
    loops TEXT NOT NULL DEFAULT '{}',
    resources TEXT NOT NULL DEFAULT '[]',
    context_id INTEGER,
    warnings INTEGER NOT NULL DEFAULT 0,
    needs_attention INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pipeline_exec_project ON pipeline_executions(project_id, status);

CREATE TABLE IF NOT EXISTS step_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL REFERENCES pipeline_executions(id) ON DELETE CASCADE,
    element_name TEXT NOT NULL,
    element_type TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'MAIN',
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'RUNNING', 'WAITING', 'PASSED', 'FAILED', 'SKIPPED', 'DISABLED', 'CANCELLED', 'TIMED_OUT')),
    input_reference TEXT NOT NULL DEFAULT '',
    result_summary TEXT NOT NULL DEFAULT '',
    error_summary TEXT NOT NULL DEFAULT '',
    raw_data_reference TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    process_id INTEGER,
    session_id INTEGER,
    redacted INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    execution_prompt_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_step_exec_execution ON step_executions(execution_id, id);

CREATE TABLE IF NOT EXISTS pipeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL REFERENCES pipeline_executions(id) ON DELETE CASCADE,
    step_execution_id INTEGER REFERENCES step_executions(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_execution ON pipeline_events(execution_id, id);

CREATE TABLE IF NOT EXISTS ralph_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    work_item_id INTEGER REFERENCES planned_work_items(id) ON DELETE SET NULL,
    sprint_id INTEGER REFERENCES sprints(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    task_text TEXT NOT NULL DEFAULT '',
    acceptance TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'CREATED' CHECK(status IN ('CREATED', 'RUNNING', 'VERIFYING', 'WAITING_FOR_HUMAN', 'PAUSED', 'BLOCKED', 'COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT')),
    reason TEXT NOT NULL DEFAULT '',
    verification_pipeline TEXT NOT NULL,
    max_iterations INTEGER NOT NULL DEFAULT 8,
    max_runtime_seconds REAL,
    identical_failure_limit INTEGER NOT NULL DEFAULT 2,
    no_change_limit INTEGER NOT NULL DEFAULT 2,
    auto_commit INTEGER NOT NULL DEFAULT 1,
    agent_session_id INTEGER,
    current_iteration INTEGER NOT NULL DEFAULT 0,
    commit_sha TEXT NOT NULL DEFAULT '',
    pause_requested INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    needs_attention INTEGER NOT NULL DEFAULT 0,
    awaiting_acceptance INTEGER NOT NULL DEFAULT 0,
    script TEXT,
    elapsed_seconds REAL NOT NULL DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ralph_runs_project ON ralph_runs(project_id, status);

CREATE TABLE IF NOT EXISTS ralph_iterations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES ralph_runs(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'CREATED' CHECK(status IN ('CREATED', 'BUILDING_CONTEXT', 'RUNNING_AGENT', 'COLLECTING_CHANGES', 'VERIFYING', 'EVALUATING', 'PASSED', 'FAILED', 'NO_PROGRESS', 'BLOCKED', 'CANCELLED')),
    prompt TEXT NOT NULL DEFAULT '',
    reply TEXT NOT NULL DEFAULT '',
    verification_execution_id INTEGER REFERENCES pipeline_executions(id) ON DELETE SET NULL,
    failure_signature TEXT NOT NULL DEFAULT '',
    change_signature TEXT NOT NULL DEFAULT '',
    changed_files TEXT NOT NULL DEFAULT '[]',
    analysis TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL DEFAULT '',
    redacted INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(run_id, number)
);

CREATE TABLE IF NOT EXISTS ralph_steering (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES ralph_runs(id) ON DELETE CASCADE,
    iteration_number INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL,
    consumed_iteration INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('screenshot', 'trace', 'log', 'diff', 'report', 'video', 'file', 'research_report')),
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size INTEGER NOT NULL DEFAULT 0,
    execution_id INTEGER REFERENCES pipeline_executions(id) ON DELETE SET NULL,
    step_execution_id INTEGER REFERENCES step_executions(id) ON DELETE SET NULL,
    step_name TEXT NOT NULL DEFAULT '',
    ralph_run_id INTEGER REFERENCES ralph_runs(id) ON DELETE SET NULL,
    iteration_number INTEGER,
    redacted INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifact_library_project ON artifact_library(project_id, kind, id);
CREATE INDEX IF NOT EXISTS idx_artifact_library_step ON artifact_library(step_execution_id);

CREATE TABLE IF NOT EXISTS artifact_tags (
    artifact_id INTEGER NOT NULL REFERENCES artifact_library(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (artifact_id, tag)
);

CREATE TABLE IF NOT EXISTS artifact_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_a_id INTEGER NOT NULL REFERENCES artifact_library(id) ON DELETE CASCADE,
    artifact_b_id INTEGER NOT NULL REFERENCES artifact_library(id) ON DELETE CASCADE,
    comparison_type TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS acceptance_criteria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    work_item_id INTEGER REFERENCES planned_work_items(id) ON DELETE CASCADE,
    ralph_run_id INTEGER REFERENCES ralph_runs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'APPROVED', 'VERIFIED', 'FAILED', 'WAIVED')),
    required INTEGER NOT NULL DEFAULT 1,
    origin TEXT NOT NULL DEFAULT 'MANUAL' CHECK(origin IN ('MANUAL', 'PLANNING', 'TEMPLATE', 'AGENT')),
    iteration INTEGER,
    created_by TEXT NOT NULL DEFAULT '',
    approved_by TEXT,
    approved_at TEXT,
    verified_by TEXT,
    verified_at TEXT,
    waived_reason TEXT NOT NULL DEFAULT '',
    template_key TEXT NOT NULL DEFAULT '',
    hints TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(work_item_id IS NOT NULL OR ralph_run_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_acceptance_work_item ON acceptance_criteria(work_item_id);
CREATE INDEX IF NOT EXISTS idx_acceptance_run ON acceptance_criteria(ralph_run_id);
-- idx_acceptance_sprint lives in _migrate(): sprint_id only exists on
-- acceptance_criteria after that function's rebuild step has run (same
-- reason idx_knowledge_slug/idx_knowledge_topic live there, not here).

CREATE TABLE IF NOT EXISTS acceptance_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    criterion_id INTEGER NOT NULL REFERENCES acceptance_criteria(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL CHECK(evidence_type IN ('ARTIFACT', 'TEST_RESULT', 'MANUAL')),
    reference_id INTEGER,
    note TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'LINKED' CHECK(state IN ('SUGGESTED', 'LINKED', 'DISMISSED')),
    recorded_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acceptance_evidence_criterion ON acceptance_evidence(criterion_id);

CREATE TABLE IF NOT EXISTS acceptance_criteria_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    criterion_id INTEGER NOT NULL REFERENCES acceptance_criteria(id) ON DELETE CASCADE,
    iteration_number INTEGER,
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL CHECK(new_status IN ('DRAFT', 'APPROVED', 'VERIFIED', 'FAILED', 'WAIVED')),
    evidence_id INTEGER REFERENCES acceptance_evidence(id) ON DELETE SET NULL,
    changed_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acceptance_history_criterion ON acceptance_criteria_history(criterion_id, iteration_number);

-- Research agent role (docs/AGENT_ADAPTER.md §23, Phase A: repository-scoped).
-- One row per `ResearchAgent.research()` call; `agent_session_id` links to the
-- underlying agent_sessions row that actually ran the prompt (prompt/reply text
-- lives there, same as every other role). No exclusive project_locks row is ever
-- created for a research session (§23 "Locking": advisory, no lock).
CREATE TABLE IF NOT EXISTS research_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_session_id INTEGER REFERENCES agent_sessions(id) ON DELETE SET NULL,
    question TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING', 'COMPLETED', 'FAILED')),
    findings_summary TEXT NOT NULL DEFAULT '',
    source_references TEXT NOT NULL DEFAULT '[]',
    report_json TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    time_limit_seconds REAL NOT NULL DEFAULT 300,
    cost_limit_usd REAL NOT NULL DEFAULT 10,
    cost_usd REAL NOT NULL DEFAULT 0,
    duration_seconds REAL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_sessions_project ON research_sessions(project_id, id);

-- Backlog research action (docs/SPRINT_PLANNING_AND_BACKLOG.md §50, task 44):
-- links a `research_sessions` row (and the Artifact Library entry indexing its
-- report) back to the Backlog item that triggered it. A lighter-weight link
-- table rather than a column on `backlog_items`: an item's status/lifecycle is
-- untouched by research (it can be triggered from any status), and an item may
-- accumulate more than one research run over time, so a link row per run (like
-- `backlog_triage_history`'s append-only rows) fits better than a single
-- mutable field would.
CREATE TABLE IF NOT EXISTS backlog_research_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backlog_item_id INTEGER NOT NULL REFERENCES backlog_items(id) ON DELETE CASCADE,
    research_session_id INTEGER NOT NULL REFERENCES research_sessions(id) ON DELETE CASCADE,
    artifact_id INTEGER REFERENCES artifact_library(id) ON DELETE SET NULL,
    outcome_state TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'ACCEPTED', 'DISMISSED')),
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backlog_research_links_item ON backlog_research_links(backlog_item_id, id);

-- Backlog "Design this item" action (docs/SPRINT_PLANNING_AND_BACKLOG.md §51):
-- draft test proposals, mirroring backlog_research_links' shape. A backlog
-- item has no planned_work_items row yet (that only exists after Sprint
-- planning), so proposals cannot be acceptance_criteria rows directly; an
-- ACCEPTED proposal is promoted into a real acceptance_criteria row later,
-- at sprint-approval time, by the same path sync_from_work_items() already
-- uses for planned_work_items.acceptance (app/acceptance/service.py).
CREATE TABLE IF NOT EXISTS backlog_test_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backlog_item_id INTEGER NOT NULL REFERENCES backlog_items(id) ON DELETE CASCADE,
    design_session_id INTEGER REFERENCES agent_sessions(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    pytest_node_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'ACCEPTED', 'DISMISSED')),
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backlog_test_proposals_item ON backlog_test_proposals(backlog_item_id, id);

CREATE TABLE IF NOT EXISTS sprint_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sprint_id INTEGER NOT NULL REFERENCES sprints(id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK(decision IN ('APPROVED', 'REVOKED')),
    approved_by TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Audit trail for research on failure (task 47, docs/RUN_AND_RALPH.md §22):
-- logs each research trigger event and steering application for compliance.
CREATE TABLE IF NOT EXISTS ralph_research_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES ralph_runs(id) ON DELETE CASCADE,
    iteration_number INTEGER NOT NULL,
    research_session_id INTEGER REFERENCES research_sessions(id) ON DELETE SET NULL,
    artifact_id INTEGER REFERENCES artifact_library(id) ON DELETE SET NULL,
    triggered_reason TEXT NOT NULL CHECK(triggered_reason IN ('no_progress', 'repeated_failures')),
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    steering_applied INTEGER NOT NULL DEFAULT 0,
    steering_accepted_at TEXT,
    accepted_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, iteration_number)
);
CREATE INDEX IF NOT EXISTS idx_ralph_research_run ON ralph_research_history(run_id, iteration_number);

-- Knowledge base (task 48): shared research insights with scoping, search, and metadata
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('web_cache', 'note')),
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    url TEXT,
    source_project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    source_session_id INTEGER REFERENCES research_sessions(id) ON DELETE SET NULL,
    source_step_id INTEGER REFERENCES step_executions(id) ON DELETE SET NULL,
    confidence TEXT NOT NULL DEFAULT 'unverified' CHECK(confidence IN ('unverified', 'reviewed', 'deleted')),
    scope TEXT NOT NULL DEFAULT 'global' CHECK(scope IN ('global') OR scope LIKE '%:%'),
    pinned INTEGER NOT NULL DEFAULT 0,
    use_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_kind ON knowledge_entries(kind);
CREATE INDEX IF NOT EXISTS idx_knowledge_confidence ON knowledge_entries(confidence);
CREATE INDEX IF NOT EXISTS idx_knowledge_scope ON knowledge_entries(scope);
CREATE INDEX IF NOT EXISTS idx_knowledge_pinned ON knowledge_entries(pinned DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_tags (
    entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (entry_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_tags ON knowledge_tags(tag);

CREATE TABLE IF NOT EXISTS knowledge_provenance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    user TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_provenance ON knowledge_provenance_history(entry_id, timestamp);

-- Wiki layer on top of the knowledge base (task 50): stable slugs, a topic
-- tree, [[slug]] backlinks and FTS5 search over the same knowledge_entries
-- rows -- the wiki is a presentation/indexing layer on existing entries, not
-- a parallel store, so KnowledgeLookup/ResearchAgent (task 48) keep working
-- unchanged. `slug`/`topic` live on knowledge_entries via `_ADDED_COLUMNS`
-- below (the table itself predates them); their indexes are created in
-- `_migrate()`, after that ALTER TABLE has actually run.
CREATE TABLE IF NOT EXISTS knowledge_backlinks (
    source_entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
    target_slug TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_entry_id, target_slug)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_backlinks_target ON knowledge_backlinks(target_slug);

CREATE TABLE IF NOT EXISTS knowledge_read_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
    accessed_at TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_knowledge_read_log_entry ON knowledge_read_log(entry_id, accessed_at);

-- Review queue for `propose()` (agent-submitted create/update/delete
-- suggestions) and for the maintenance report's stale/duplicate findings;
-- `entry_id` is NULL for a proposed *new* entry (nothing to point at yet).
CREATE TABLE IF NOT EXISTS knowledge_review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER REFERENCES knowledge_entries(id) ON DELETE CASCADE,
    slug TEXT,
    change_type TEXT NOT NULL CHECK(change_type IN ('create', 'update', 'delete')),
    title TEXT,
    content TEXT,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_review_status ON knowledge_review_queue(status, created_at);

-- FTS5 full-text index over title/content, kept in sync with knowledge_entries
-- by the triggers below (external content table: no duplicated storage,
-- `rowid` mirrors knowledge_entries.id). Tags stay a separate AND-filter
-- (knowledge_tags) rather than a FTS column, since they live in their own
-- join table and searches rarely need free-text ranking over tags.
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_search USING fts5(
    title, content,
    content='knowledge_entries', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS knowledge_entries_ai AFTER INSERT ON knowledge_entries BEGIN
    INSERT INTO wiki_search(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_entries_ad AFTER DELETE ON knowledge_entries BEGIN
    INSERT INTO wiki_search(wiki_search, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_entries_au AFTER UPDATE ON knowledge_entries BEGIN
    INSERT INTO wiki_search(wiki_search, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
    INSERT INTO wiki_search(rowid, title, content) VALUES (new.id, new.title, new.content);
END;

-- Archived chat summaries (docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md, task 62): chat
-- itself is a read-side view over agent_sessions/agent_events (role = 'GENERAL'),
-- not a duplicated message store, so this is the only new table the feature needs --
-- one row per summarized project/day window, produced by the nightly job (task 67).
CREATE TABLE IF NOT EXISTS session_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    summary TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    session_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_session_summaries_project ON session_summaries(project_id, period_end DESC);
"""

# Starter catalog, seeded once (see `_seed_model_catalog`) so the model
# picker isn't empty on a fresh install. Admins add/remove/enable entries
# from the Settings page afterwards; there is no API to enumerate a
# provider's valid model ids, so this list is maintained by hand.
_DEFAULT_MODEL_CATALOG = [
    ("openai", "gpt-5.5"),
    ("openai", "gpt-5.6-sol"),
    ("openai", "gpt-5.6-luna"),
    ("anthropic", "claude-opus-5"),
    ("anthropic", "claude-sonnet-5"),
    ("anthropic", "claude-fable-5"),
    ("anthropic", "claude-haiku-4-5-20251001"),
]


def _seed_model_catalog(db: sqlite3.Connection) -> None:
    if db.execute("SELECT 1 FROM model_catalog LIMIT 1").fetchone():
        return
    db.executemany(
        "INSERT OR IGNORE INTO model_catalog (provider, model_id) VALUES (?, ?)",
        _DEFAULT_MODEL_CATALOG,
    )
    db.commit()


# Columns added after a table first shipped. `CREATE TABLE IF NOT EXISTS` leaves
# an existing table untouched, so databases created earlier get them here.
_ADDED_COLUMNS = (
    ("projects", "starred", "INTEGER NOT NULL DEFAULT 0"),
    ("projects", "context_files", "TEXT NOT NULL DEFAULT ''"),
    ("projects", "lock_scope", "TEXT NOT NULL DEFAULT 'project'"),  # 'project' | 'repository'
    ("project_locks", "repository_id", "INTEGER"),
    ("ralph_runs", "awaiting_acceptance", "INTEGER NOT NULL DEFAULT 0"),
    ("step_executions", "execution_prompt_id", "INTEGER"),
    ("ralph_iterations", "execution_prompt_id", "INTEGER"),
    ("sprints", "auto_run", "INTEGER NOT NULL DEFAULT 0"),
    ("planned_work_items", "task_state", "TEXT NOT NULL DEFAULT 'DRAFT'"),
    ("planned_work_items", "released_at", "TEXT"),
    ("planned_work_items", "verification_pipeline", "TEXT NOT NULL DEFAULT ''"),
    ("model_catalog", "base_url", "TEXT NOT NULL DEFAULT ''"),
    ("model_catalog", "api_key", "TEXT NOT NULL DEFAULT ''"),
    # Skills (docs/AGENT_ADAPTER.md §23.2/23.3): a skill is a prompt_fragments row
    # with skill_status set. Kept as columns on the fragment, not a new table, so
    # skill content reuses fragment versioning/audit trail rather than duplicating it.
    ("prompt_fragments", "skill_status", "TEXT NOT NULL DEFAULT ''"),  # '' | draft | active | deprecated
    ("prompt_fragments", "skill_when_to_use", "TEXT NOT NULL DEFAULT ''"),
    ("prompt_fragments", "skill_constraints", "TEXT NOT NULL DEFAULT '[]'"),
    ("prompt_fragments", "skill_depends_on", "TEXT NOT NULL DEFAULT '[]'"),  # JSON list of fragment ids
    ("prompt_fragments", "skill_roles", "TEXT NOT NULL DEFAULT '[]'"),  # empty = every role
    ("prompt_fragments", "skill_adapter_types", "TEXT NOT NULL DEFAULT '[]'"),  # empty = every adapter
    ("prompt_fragments", "skill_priority", "INTEGER NOT NULL DEFAULT 0"),
    ("prompt_fragments", "skill_author", "TEXT NOT NULL DEFAULT ''"),
    # Review cadence (docs/AGENT_ADAPTER.md §23.4): a person can record "reviewed,
    # still correct" without changing content -- prompt_fragment_versions only
    # captures *changes*, not a no-op review, so that case needs its own columns
    # rather than a synthetic version bump.
    ("prompt_fragments", "skill_last_reviewed_at", "TEXT NOT NULL DEFAULT ''"),
    ("prompt_fragments", "skill_reviewed_by", "TEXT NOT NULL DEFAULT ''"),
    ("prompt_fragment_versions", "changelog", "TEXT NOT NULL DEFAULT ''"),
    # Compliance/audit trail for role-based skill injection (task 53,
    # docs/AGENT_ADAPTER.md §23.2/§23.3 "revisited"): which skills `skill_context()`
    # selected for the prompt this row records, alongside the existing
    # `blocks_included`.
    ("execution_prompts", "skills_included", "TEXT NOT NULL DEFAULT '[]'"),
    # Opt-in research-on-failure trigger (task 47, docs/RUN_AND_RALPH.md §22):
    # run-level configuration, alongside the existing `identical_failure_limit`/
    # `no_change_limit` no-progress settings this sits next to but does not
    # replace. `research_script` is a FakeAgent script (mirrors the existing
    # `script` column) so tests can drive the ad-hoc ResearchAgent session
    # deterministically; unused by real adapters.
    ("ralph_runs", "research_on_failure", "INTEGER NOT NULL DEFAULT 0"),
    ("ralph_runs", "failure_threshold", "INTEGER NOT NULL DEFAULT 2"),
    ("ralph_runs", "research_script", "TEXT"),
    # Per-iteration link to the research session/Artifact it triggered, and
    # whether a person has acted on it yet (advisory-only gate, §23 "A person
    # accepts findings before they change a plan or acceptance criteria").
    ("ralph_iterations", "research_session_id", "INTEGER"),
    ("ralph_iterations", "research_artifact_id", "INTEGER"),
    ("ralph_iterations", "research_report_presented", "INTEGER NOT NULL DEFAULT 0"),
    # Wiki layer on knowledge_entries (task 50): stable slug/aliases for
    # /wiki/<slug> routing and [[slug]] links, plus the progressive-disclosure
    # dimensions (language/library/version/topic) the index generator groups
    # by, and `freshness_days`/`last_read_at` for the staleness report.
    ("knowledge_entries", "slug", "TEXT"),
    ("knowledge_entries", "aliases", "TEXT NOT NULL DEFAULT '[]'"),
    ("knowledge_entries", "topic", "TEXT NOT NULL DEFAULT ''"),
    ("knowledge_entries", "language", "TEXT NOT NULL DEFAULT ''"),
    ("knowledge_entries", "library", "TEXT NOT NULL DEFAULT ''"),
    ("knowledge_entries", "version", "TEXT NOT NULL DEFAULT ''"),
    ("knowledge_entries", "freshness_days", "INTEGER NOT NULL DEFAULT 30"),
    ("knowledge_entries", "last_read_at", "TEXT"),
    # Sprint QA checklist (docs/SPRINT_PLANNING_AND_BACKLOG.md §51): a criterion
    # not owned by one work item, and the exact pytest test that proves it
    # (rather than the fuzzy keyword matching suggest_evidence() otherwise
    # relies on). The CHECK constraint allowing sprint_id-only rows is fixed
    # up separately, by the table-rebuild step below -- ALTER TABLE ADD COLUMN
    # cannot change an existing CHECK.
    ("acceptance_criteria", "sprint_id", "INTEGER REFERENCES sprints(id) ON DELETE CASCADE"),
    ("acceptance_criteria", "pytest_node_id", "TEXT NOT NULL DEFAULT ''"),
    # Chat redaction chokepoint (docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md §7, task 61):
    # `add_agent_event` redacts `data` before storing it and sets this flag, so both
    # the interactive session transcript and the derived chat view are covered by one
    # change instead of redacting the new chat surface only.
    ("agent_events", "redacted", "INTEGER NOT NULL DEFAULT 0"),
    # Folder wiki scoping (docs/WIKI_INTEGRATION_AND_PRESENTATION.md §14), added
    # after wiki_sources first shipped without it.
    ("wiki_sources", "project_id", "INTEGER REFERENCES projects(id) ON DELETE CASCADE"),
)


def _migrate(db: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if existing and column not in existing:  # no columns = table not created yet
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    # At most one ACTIVE lock per scope (the whole project, or one repository): the
    # database arbitrates races between equal scopes; `lock.acquire` handles
    # project-vs-repository overlap inside a write transaction.
    if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_locks'").fetchone():
        db.execute("DROP INDEX IF EXISTS idx_project_locks_active")
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_locks_scope "
            "ON project_locks(project_id, IFNULL(repository_id, 0)) WHERE status = 'ACTIVE'"
        )
    # slug/topic only exist on knowledge_entries after the ALTER TABLE loop
    # above has run, so their indexes can't live in SCHEMA (executescript
    # runs before _migrate, against the pre-task-50 column set). Guarded like
    # project_locks above: _migrate() also runs standalone, against a bare
    # pre-existing database that may not have this table at all yet
    # (tests/test_project_management.py's test_migration_adds_starred_to_old_database).
    if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_entries'").fetchone():
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_slug ON knowledge_entries(slug)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_topic ON knowledge_entries(topic)")
    db.commit()
    _migrate_acceptance_sprint_check(db)


def _migrate_acceptance_sprint_check(db: sqlite3.Connection) -> None:
    """Widen acceptance_criteria's ownership CHECK to also allow a sprint_id-only
    row (§51 Sprint QA checklist). The _ADDED_COLUMNS loop above already added
    the sprint_id/pytest_node_id columns to an existing database, but SQLite
    cannot ALTER a CHECK constraint in place -- the table must be rebuilt, the
    standard recreate-and-swap SQLite itself documents for this case (unlike
    every other entry in _ADDED_COLUMNS, which only ever add a column). Guarded
    on the stored CREATE TABLE text so this runs at most once, whether against
    a database that predates sprint_id entirely or one from between this
    column's introduction and this rebuild landing."""
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'acceptance_criteria'"
    ).fetchone()
    if row is None or "sprint_id IS NOT NULL" in row["sql"]:
        return
    db.commit()  # PRAGMA foreign_keys is a no-op inside a pending transaction
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute(
            "CREATE TABLE acceptance_criteria_new ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,"
            "work_item_id INTEGER REFERENCES planned_work_items(id) ON DELETE CASCADE,"
            "ralph_run_id INTEGER REFERENCES ralph_runs(id) ON DELETE CASCADE,"
            "title TEXT NOT NULL,"
            "description TEXT NOT NULL DEFAULT '',"
            "status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'APPROVED', 'VERIFIED', 'FAILED', 'WAIVED')),"
            "required INTEGER NOT NULL DEFAULT 1,"
            "origin TEXT NOT NULL DEFAULT 'MANUAL' CHECK(origin IN ('MANUAL', 'PLANNING', 'TEMPLATE', 'AGENT')),"
            "iteration INTEGER,"
            "created_by TEXT NOT NULL DEFAULT '',"
            "approved_by TEXT,"
            "approved_at TEXT,"
            "verified_by TEXT,"
            "verified_at TEXT,"
            "waived_reason TEXT NOT NULL DEFAULT '',"
            "template_key TEXT NOT NULL DEFAULT '',"
            "hints TEXT NOT NULL DEFAULT '{}',"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            "sprint_id INTEGER REFERENCES sprints(id) ON DELETE CASCADE,"
            "pytest_node_id TEXT NOT NULL DEFAULT '',"
            "CHECK(work_item_id IS NOT NULL OR ralph_run_id IS NOT NULL OR sprint_id IS NOT NULL)"
            ")"
        )
        db.execute(
            "INSERT INTO acceptance_criteria_new (id, project_id, work_item_id, ralph_run_id, title, "
            "description, status, required, origin, iteration, created_by, approved_by, approved_at, "
            "verified_by, verified_at, waived_reason, template_key, hints, created_at, updated_at, "
            "sprint_id, pytest_node_id) "
            "SELECT id, project_id, work_item_id, ralph_run_id, title, description, status, required, "
            "origin, iteration, created_by, approved_by, approved_at, verified_by, verified_at, "
            "waived_reason, template_key, hints, created_at, updated_at, sprint_id, pytest_node_id "
            "FROM acceptance_criteria"
        )
        db.execute("DROP TABLE acceptance_criteria")
        db.execute("ALTER TABLE acceptance_criteria_new RENAME TO acceptance_criteria")
        db.execute("CREATE INDEX IF NOT EXISTS idx_acceptance_work_item ON acceptance_criteria(work_item_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_acceptance_run ON acceptance_criteria(ralph_run_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_acceptance_sprint ON acceptance_criteria(sprint_id)")
        db.commit()
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db_path = current_app.config["DATABASE_PATH"]
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        # `timeout=30` and WAL mode match every other connection factory in the
        # app (app/execution/host.py, app/pipelines/manager.py, app/runs/executor.py,
        # app/projects/lock.py, app/workspace/terminal.py) -- this was the one
        # outlier, opening with sqlite3's default 5s busy timeout and never
        # requesting WAL, which let a request holding this connection (or the
        # very first connection to ever touch a fresh database file, racing the
        # one-time, exclusive-lock-requiring switch to WAL from another thread)
        # produce spurious "database is locked" errors under the concurrent
        # thread activity Runs/Ralph/pipeline locking already assumes
        # (docs/RUN_AND_RALPH.md §22 Heartbeat, tests/projects/test_lock_scopes.py).
        g.db = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        if db_path != ":memory:":
            g.db.execute("PRAGMA journal_mode = WAL")
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app: Flask) -> None:
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        _migrate(db)
        _seed_model_catalog(db)

    app.cli.add_command(init_db_command)


@click.command("init-db")
def init_db_command() -> None:
    """Clear existing data and recreate tables."""
    db = get_db()
    db.executescript(SCHEMA)
    _migrate(db)
    _seed_model_catalog(db)
    click.echo("Initialized the database.")
