from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass
class ExecutionContext:
    id: int
    provider: str
    target: str
    working_directory: str
    environment_profile: dict[str, str]
    mounts: str | None
    network: str | None
    status: str


@dataclass
class Process:
    id: int
    context_id: int
    provider: str
    external_process_id: int | None
    command_summary: str
    working_directory: str
    status: str
    started_at: str | None
    completed_at: str | None
    exit_code: int | None


@dataclass
class ProcessEvent:
    id: int
    context_id: int
    process_id: int | None
    event_type: str
    stream: str | None
    data: str
    created_at: str


def create_execution_context(
    db: sqlite3.Connection,
    provider: str,
    working_directory: str,
    environment_profile: dict[str, str],
    target: str = "",
    mounts: str | None = None,
    network: str | None = None,
) -> int:
    cur = db.execute(
        "INSERT INTO execution_contexts "
        "(provider, target, working_directory, environment_profile, mounts, network, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'CREATED')",
        (
            provider,
            target,
            working_directory,
            json.dumps(environment_profile),
            mounts,
            network,
        ),
    )
    db.commit()
    return cur.lastrowid


def set_context_status(db: sqlite3.Connection, context_id: int, status: str) -> None:
    db.execute(
        "UPDATE execution_contexts SET status = ? WHERE id = ?", (status, context_id)
    )
    db.commit()


def get_execution_context(
    db: sqlite3.Connection, context_id: int
) -> ExecutionContext | None:
    row = db.execute(
        "SELECT * FROM execution_contexts WHERE id = ?", (context_id,)
    ).fetchone()
    if row is None:
        return None
    return _hydrate_context(row)


def _hydrate_context(row: sqlite3.Row) -> ExecutionContext:
    return ExecutionContext(
        id=row["id"],
        provider=row["provider"],
        target=row["target"],
        working_directory=row["working_directory"],
        environment_profile=json.loads(row["environment_profile"]),
        mounts=row["mounts"],
        network=row["network"],
        status=row["status"],
    )


def create_process(
    db: sqlite3.Connection,
    context_id: int,
    provider: str,
    command_summary: str,
    working_directory: str,
) -> int:
    cur = db.execute(
        "INSERT INTO processes "
        "(context_id, provider, command_summary, working_directory, status) "
        "VALUES (?, ?, ?, ?, 'STARTING')",
        (context_id, provider, command_summary, working_directory),
    )
    db.commit()
    return cur.lastrowid


def mark_process_started(
    db: sqlite3.Connection, process_id: int, external_process_id: int
) -> None:
    db.execute(
        "UPDATE processes SET external_process_id = ?, status = 'RUNNING', "
        "started_at = datetime('now') WHERE id = ?",
        (external_process_id, process_id),
    )
    db.commit()


def mark_process_terminal(
    db: sqlite3.Connection, process_id: int, status: str, exit_code: int | None
) -> None:
    db.execute(
        "UPDATE processes SET status = ?, exit_code = ?, completed_at = datetime('now') "
        "WHERE id = ?",
        (status, exit_code, process_id),
    )
    db.commit()


def get_process(db: sqlite3.Connection, process_id: int) -> Process | None:
    row = db.execute("SELECT * FROM processes WHERE id = ?", (process_id,)).fetchone()
    if row is None:
        return None
    return _hydrate_process(row)


def list_processes_for_context(db: sqlite3.Connection, context_id: int) -> list[Process]:
    rows = db.execute(
        "SELECT * FROM processes WHERE context_id = ? ORDER BY id", (context_id,)
    ).fetchall()
    return [_hydrate_process(r) for r in rows]


def _hydrate_process(row: sqlite3.Row) -> Process:
    return Process(
        id=row["id"],
        context_id=row["context_id"],
        provider=row["provider"],
        external_process_id=row["external_process_id"],
        command_summary=row["command_summary"],
        working_directory=row["working_directory"],
        status=row["status"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        exit_code=row["exit_code"],
    )


def add_event(
    db: sqlite3.Connection,
    context_id: int,
    event_type: str,
    process_id: int | None = None,
    data: str = "",
    stream: str | None = None,
) -> int:
    cur = db.execute(
        "INSERT INTO process_events (context_id, process_id, event_type, stream, data) "
        "VALUES (?, ?, ?, ?, ?)",
        (context_id, process_id, event_type, stream, data),
    )
    db.commit()
    return cur.lastrowid


def list_events(
    db: sqlite3.Connection, process_id: int, after_id: int | None = None
) -> list[ProcessEvent]:
    if after_id is None:
        rows = db.execute(
            "SELECT * FROM process_events WHERE process_id = ? ORDER BY id",
            (process_id,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM process_events WHERE process_id = ? AND id > ? ORDER BY id",
            (process_id, after_id),
        ).fetchall()
    return [_hydrate_event(r) for r in rows]


def _hydrate_event(row: sqlite3.Row) -> ProcessEvent:
    return ProcessEvent(
        id=row["id"],
        context_id=row["context_id"],
        process_id=row["process_id"],
        event_type=row["event_type"],
        stream=row["stream"],
        data=row["data"],
        created_at=row["created_at"],
    )
