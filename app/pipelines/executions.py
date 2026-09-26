"""Persistence for pipeline executions (docs/PIPELINE_ENGINE.md §9, §18)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from app.runs.models import now

TERMINAL_EXECUTION = ("COMPLETED", "FAILED", "CANCELLED")
DONE_STEP = ("PASSED", "SKIPPED", "DISABLED")


@dataclass
class Execution:
    id: int
    pipeline_id: int
    pipeline_version: int
    project_id: int
    repository_id: int | None
    sprint_id: int | None
    parent_execution_id: int | None
    run_id: int | None
    status: str
    reason: str
    resolved_configuration: dict
    variables: dict
    cursor: int
    waiting_step_id: int | None
    loops: dict
    resources: list
    context_id: int | None
    warnings: int
    needs_attention: bool
    cancel_requested: bool
    started_at: str | None
    completed_at: str | None
    created_at: str

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_EXECUTION


@dataclass
class StepExecution:
    id: int
    execution_id: int
    element_name: str
    element_type: str
    phase: str
    attempt: int
    status: str
    input_reference: str
    result_summary: str
    error_summary: str
    raw_data_reference: str
    exit_code: int | None
    process_id: int | None
    session_id: int | None
    redacted: bool
    started_at: str | None
    completed_at: str | None


@dataclass
class ExecutionEvent:
    id: int
    execution_id: int
    step_execution_id: int | None
    event_type: str
    data: str
    created_at: str = field(default="")


def _execution(row: sqlite3.Row) -> Execution:
    d = dict(row)
    for key in ("resolved_configuration", "variables", "loops", "resources"):
        d[key] = json.loads(d[key])
    d["needs_attention"] = bool(d["needs_attention"])
    d["cancel_requested"] = bool(d["cancel_requested"])
    return Execution(**d)


def create_execution(
    db: sqlite3.Connection,
    pipeline_id: int,
    pipeline_version: int,
    project_id: int,
    elements: list[dict],
    repository_id: int | None = None,
    sprint_id: int | None = None,
    parent_execution_id: int | None = None,
    run_id: int | None = None,
    variables: dict | None = None,
) -> int:
    """`elements` is the composed, ordered element list; it is frozen into
    `resolved_configuration` so later pipeline edits never change this run (§17)."""
    cur = db.execute(
        "INSERT INTO pipeline_executions (pipeline_id, pipeline_version, project_id, repository_id, "
        "sprint_id, parent_execution_id, run_id, resolved_configuration, variables, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pipeline_id, pipeline_version, project_id, repository_id, sprint_id,
            parent_execution_id, run_id, json.dumps({"elements": elements}),
            json.dumps(variables or {}), now(),
        ),
    )
    db.commit()
    return cur.lastrowid


def get_execution(db: sqlite3.Connection, execution_id: int) -> Execution | None:
    row = db.execute("SELECT * FROM pipeline_executions WHERE id = ?", (execution_id,)).fetchone()
    return _execution(row) if row else None


def list_executions(
    db: sqlite3.Connection, project_id: int, limit: int = 50, offset: int = 0
) -> list[Execution]:
    rows = db.execute(
        "SELECT * FROM pipeline_executions WHERE project_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (project_id, limit, offset),
    ).fetchall()
    return [_execution(r) for r in rows]


def update_execution(db: sqlite3.Connection, execution_id: int, **fields) -> None:
    encoded = {
        k: (json.dumps(v) if k in ("loops", "resources", "variables") else v)
        for k, v in fields.items()
    }
    assignments = ", ".join(f"{k} = ?" for k in encoded)
    db.execute(
        f"UPDATE pipeline_executions SET {assignments} WHERE id = ?", [*encoded.values(), execution_id]
    )
    db.commit()


def add_event(
    db: sqlite3.Connection,
    execution_id: int,
    event_type: str,
    data: str = "",
    step_execution_id: int | None = None,
) -> int:
    cur = db.execute(
        "INSERT INTO pipeline_events (execution_id, step_execution_id, event_type, data, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (execution_id, step_execution_id, event_type, data, now()),
    )
    db.commit()
    return cur.lastrowid


def list_events(db: sqlite3.Connection, execution_id: int, after_id: int = 0) -> list[ExecutionEvent]:
    rows = db.execute(
        "SELECT * FROM pipeline_events WHERE execution_id = ? AND id > ? ORDER BY id",
        (execution_id, after_id),
    ).fetchall()
    return [ExecutionEvent(**dict(r)) for r in rows]


def add_step(
    db: sqlite3.Connection, execution_id: int, element: dict, attempt: int, status: str = "RUNNING"
) -> int:
    stamp = now() if status == "RUNNING" else None
    cur = db.execute(
        "INSERT INTO step_executions (execution_id, element_name, element_type, phase, attempt, "
        "status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (execution_id, element["name"], element["type"], element.get("phase", "MAIN"), attempt, status, stamp),
    )
    db.commit()
    return cur.lastrowid


def get_step(db: sqlite3.Connection, step_id: int) -> StepExecution | None:
    row = db.execute("SELECT * FROM step_executions WHERE id = ?", (step_id,)).fetchone()
    return _step(row) if row else None


def _step(row: sqlite3.Row) -> StepExecution:
    d = dict(row)
    d["redacted"] = bool(d["redacted"])
    return StepExecution(**d)


def list_steps(db: sqlite3.Connection, execution_id: int) -> list[StepExecution]:
    rows = db.execute(
        "SELECT * FROM step_executions WHERE execution_id = ? ORDER BY id", (execution_id,)
    ).fetchall()
    return [_step(r) for r in rows]


def update_step(db: sqlite3.Connection, step_id: int, **fields) -> None:
    assignments = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE step_executions SET {assignments} WHERE id = ?", [*fields.values(), step_id])
    db.commit()


def finish_step(db: sqlite3.Connection, step_id: int, status: str, **fields) -> None:
    update_step(db, step_id, status=status, completed_at=now(), **fields)


def latest_step(db: sqlite3.Connection, execution_id: int, name: str) -> StepExecution | None:
    row = db.execute(
        "SELECT * FROM step_executions WHERE execution_id = ? AND element_name = ? ORDER BY id DESC LIMIT 1",
        (execution_id, name),
    ).fetchone()
    return _step(row) if row else None
