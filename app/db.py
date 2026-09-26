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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider, model_id)
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
    completed_at TEXT
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
    kind TEXT NOT NULL CHECK(kind IN ('screenshot', 'trace', 'log', 'diff', 'report', 'video', 'file')),
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

CREATE TABLE IF NOT EXISTS sprint_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sprint_id INTEGER NOT NULL REFERENCES sprints(id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK(decision IN ('APPROVED', 'REVOKED')),
    approved_by TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
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
)


def _migrate(db: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    db.commit()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db_path = current_app.config["DATABASE_PATH"]
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        g.db = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
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
