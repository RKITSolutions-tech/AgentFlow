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
    last_activity_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '',
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
        db.commit()
        _seed_model_catalog(db)

    app.cli.add_command(init_db_command)


@click.command("init-db")
def init_db_command() -> None:
    """Clear existing data and recreate tables."""
    db = get_db()
    db.executescript(SCHEMA)
    db.commit()
    _seed_model_catalog(db)
    click.echo("Initialized the database.")
