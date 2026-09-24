from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class TerminalSession:
    id: int
    repo_id: int
    tmux_session_name: str
    label: str
    working_directory: str
    pipe_path: str
    pipe_offset: int
    status: str
    created_at: str
    last_activity_at: str


@dataclass
class TerminalEvent:
    id: int
    terminal_session_id: int
    data: str
    created_at: str


def create_terminal_session(
    db: sqlite3.Connection,
    repo_id: int,
    tmux_session_name: str,
    working_directory: str,
    pipe_path: str,
    label: str = "",
) -> int:
    cur = db.execute(
        "INSERT INTO terminal_sessions "
        "(repo_id, tmux_session_name, label, working_directory, pipe_path, status) "
        "VALUES (?, ?, ?, ?, ?, 'RUNNING')",
        (repo_id, tmux_session_name, label, working_directory, pipe_path),
    )
    db.commit()
    return cur.lastrowid


def get_terminal_session(db: sqlite3.Connection, terminal_session_id: int) -> TerminalSession | None:
    row = db.execute(
        "SELECT * FROM terminal_sessions WHERE id = ?", (terminal_session_id,)
    ).fetchone()
    if row is None:
        return None
    return _hydrate_session(row)


def list_terminal_sessions(db: sqlite3.Connection, repo_id: int) -> list[TerminalSession]:
    rows = db.execute(
        "SELECT * FROM terminal_sessions WHERE repo_id = ? ORDER BY id", (repo_id,)
    ).fetchall()
    return [_hydrate_session(r) for r in rows]


def set_terminal_session_status(
    db: sqlite3.Connection, terminal_session_id: int, status: str
) -> None:
    db.execute(
        "UPDATE terminal_sessions SET status = ?, last_activity_at = datetime('now') "
        "WHERE id = ?",
        (status, terminal_session_id),
    )
    db.commit()


def touch_terminal_session(db: sqlite3.Connection, terminal_session_id: int) -> None:
    db.execute(
        "UPDATE terminal_sessions SET last_activity_at = datetime('now') WHERE id = ?",
        (terminal_session_id,),
    )
    db.commit()


def set_terminal_session_pipe_offset(
    db: sqlite3.Connection, terminal_session_id: int, pipe_offset: int
) -> None:
    db.execute(
        "UPDATE terminal_sessions SET pipe_offset = ? WHERE id = ?",
        (pipe_offset, terminal_session_id),
    )
    db.commit()


def _hydrate_session(row: sqlite3.Row) -> TerminalSession:
    return TerminalSession(
        id=row["id"],
        repo_id=row["repo_id"],
        tmux_session_name=row["tmux_session_name"],
        label=row["label"],
        working_directory=row["working_directory"],
        pipe_path=row["pipe_path"],
        pipe_offset=row["pipe_offset"],
        status=row["status"],
        created_at=row["created_at"],
        last_activity_at=row["last_activity_at"],
    )


def add_terminal_event(
    db: sqlite3.Connection, terminal_session_id: int, data: str
) -> int:
    cur = db.execute(
        "INSERT INTO terminal_events (terminal_session_id, data) VALUES (?, ?)",
        (terminal_session_id, data),
    )
    db.commit()
    return cur.lastrowid


def record_output_chunk(
    db: sqlite3.Connection, terminal_session_id: int, data: str, pipe_offset: int
) -> None:
    """Append a tailed output chunk and advance the tailer's read offset in
    one commit, so a crash can't leave the offset ahead of what was actually
    recorded (which would silently drop output) or behind it (which would
    re-emit already-recorded output as duplicate events on tailer restart)."""
    db.execute(
        "INSERT INTO terminal_events (terminal_session_id, data) VALUES (?, ?)",
        (terminal_session_id, data),
    )
    db.execute(
        "UPDATE terminal_sessions SET pipe_offset = ?, last_activity_at = datetime('now') "
        "WHERE id = ?",
        (pipe_offset, terminal_session_id),
    )
    db.commit()


def list_terminal_events(
    db: sqlite3.Connection, terminal_session_id: int, after_id: int | None = None
) -> list[TerminalEvent]:
    if after_id is None:
        rows = db.execute(
            "SELECT * FROM terminal_events WHERE terminal_session_id = ? ORDER BY id",
            (terminal_session_id,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM terminal_events WHERE terminal_session_id = ? AND id > ? "
            "ORDER BY id",
            (terminal_session_id, after_id),
        ).fetchall()
    return [_hydrate_event(r) for r in rows]


def _hydrate_event(row: sqlite3.Row) -> TerminalEvent:
    return TerminalEvent(
        id=row["id"],
        terminal_session_id=row["terminal_session_id"],
        data=row["data"],
        created_at=row["created_at"],
    )
