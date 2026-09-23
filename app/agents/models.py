from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class AgentSession:
    id: int
    project_id: int
    agent_type: str
    role: str
    external_session_id: str | None
    execution_provider: str
    execution_target: str
    status: str
    metadata: dict[str, Any]
    started_at: str
    last_activity_at: str


@dataclass
class AgentEvent:
    id: int
    session_id: int
    event_type: str
    data: str
    created_at: str


@dataclass
class AgentResult:
    id: int
    session_id: int
    status: str
    git_diff: str
    test_status: str | None
    test_exit_code: int | None
    test_output: str
    created_at: str


def create_agent_session(
    db: sqlite3.Connection,
    project_id: int,
    agent_type: str,
    role: str = "GENERAL",
    execution_provider: str = "host",
    execution_target: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    cur = db.execute(
        "INSERT INTO agent_sessions "
        "(project_id, agent_type, role, execution_provider, execution_target, status, metadata) "
        "VALUES (?, ?, ?, ?, ?, 'STARTING', ?)",
        (
            project_id,
            agent_type,
            role,
            execution_provider,
            execution_target,
            json.dumps(metadata or {}),
        ),
    )
    db.commit()
    return cur.lastrowid


def set_session_status(db: sqlite3.Connection, session_id: int, status: str) -> None:
    db.execute(
        "UPDATE agent_sessions SET status = ?, last_activity_at = datetime('now') WHERE id = ?",
        (status, session_id),
    )
    db.commit()


def set_session_metadata(
    db: sqlite3.Connection, session_id: int, metadata: dict[str, Any]
) -> None:
    db.execute(
        "UPDATE agent_sessions SET metadata = ?, last_activity_at = datetime('now') WHERE id = ?",
        (json.dumps(metadata), session_id),
    )
    db.commit()


def set_external_session_id(
    db: sqlite3.Connection, session_id: int, external_session_id: str
) -> None:
    db.execute(
        "UPDATE agent_sessions SET external_session_id = ? WHERE id = ?",
        (external_session_id, session_id),
    )
    db.commit()


def get_agent_session(db: sqlite3.Connection, session_id: int) -> AgentSession | None:
    row = db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    return _hydrate_session(row)


def list_agent_sessions_for_project(
    db: sqlite3.Connection, project_id: int
) -> list[AgentSession]:
    rows = db.execute(
        "SELECT * FROM agent_sessions WHERE project_id = ? ORDER BY id", (project_id,)
    ).fetchall()
    return [_hydrate_session(r) for r in rows]


def _hydrate_session(row: sqlite3.Row) -> AgentSession:
    return AgentSession(
        id=row["id"],
        project_id=row["project_id"],
        agent_type=row["agent_type"],
        role=row["role"],
        external_session_id=row["external_session_id"],
        execution_provider=row["execution_provider"],
        execution_target=row["execution_target"],
        status=row["status"],
        metadata=json.loads(row["metadata"]),
        started_at=row["started_at"],
        last_activity_at=row["last_activity_at"],
    )


def add_agent_event(
    db: sqlite3.Connection, session_id: int, event_type: str, data: str = ""
) -> int:
    cur = db.execute(
        "INSERT INTO agent_events (session_id, event_type, data) VALUES (?, ?, ?)",
        (session_id, event_type, data),
    )
    db.commit()
    return cur.lastrowid


def list_agent_events(
    db: sqlite3.Connection, session_id: int, after_id: int | None = None
) -> list[AgentEvent]:
    if after_id is None:
        rows = db.execute(
            "SELECT * FROM agent_events WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM agent_events WHERE session_id = ? AND id > ? ORDER BY id",
            (session_id, after_id),
        ).fetchall()
    return [_hydrate_event(r) for r in rows]


def _hydrate_event(row: sqlite3.Row) -> AgentEvent:
    return AgentEvent(
        id=row["id"],
        session_id=row["session_id"],
        event_type=row["event_type"],
        data=row["data"],
        created_at=row["created_at"],
    )


def record_result(
    db: sqlite3.Connection,
    session_id: int,
    status: str,
    git_diff: str = "",
    test_status: str | None = None,
    test_exit_code: int | None = None,
    test_output: str = "",
) -> int:
    cur = db.execute(
        "INSERT INTO agent_results "
        "(session_id, status, git_diff, test_status, test_exit_code, test_output) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, status, git_diff, test_status, test_exit_code, test_output),
    )
    db.commit()
    return cur.lastrowid


def get_result(db: sqlite3.Connection, session_id: int) -> AgentResult | None:
    row = db.execute(
        "SELECT * FROM agent_results WHERE session_id = ? ORDER BY id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return AgentResult(
        id=row["id"],
        session_id=row["session_id"],
        status=row["status"],
        git_diff=row["git_diff"],
        test_status=row["test_status"],
        test_exit_code=row["test_exit_code"],
        test_output=row["test_output"],
        created_at=row["created_at"],
    )
