"""Persistence for Ralph runs, iterations and steering (docs/RUN_AND_RALPH.md)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from app.runs.models import now

RUN_ACTIVE = ("CREATED", "RUNNING", "VERIFYING")
RUN_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT")
RUN_STATUSES = (
    "CREATED", "RUNNING", "VERIFYING", "WAITING_FOR_HUMAN", "PAUSED", "BLOCKED",
    "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT",
)
DEFAULT_MAX_ITERATIONS = 8
DEFAULT_IDENTICAL_FAILURE_LIMIT = 2
DEFAULT_NO_CHANGE_LIMIT = 2


@dataclass
class RalphRun:
    id: int
    project_id: int
    repository_id: int
    work_item_id: int | None
    sprint_id: int | None
    title: str
    task_text: str
    acceptance: list[str]
    status: str
    reason: str
    verification_pipeline: str
    max_iterations: int
    max_runtime_seconds: float | None
    identical_failure_limit: int
    no_change_limit: int
    auto_commit: bool
    agent_session_id: int | None
    current_iteration: int
    commit_sha: str
    pause_requested: bool
    cancel_requested: bool
    needs_attention: bool
    awaiting_acceptance: bool
    script: list | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    elapsed_seconds: float = 0.0


@dataclass
class Iteration:
    id: int
    run_id: int
    number: int
    status: str
    prompt: str
    reply: str
    verification_execution_id: int | None
    failure_signature: str
    change_signature: str
    changed_files: list[str]
    analysis: str
    next_action: str
    commit_sha: str
    redacted: bool
    started_at: str | None
    completed_at: str | None


@dataclass
class Steering:
    id: int
    run_id: int
    iteration_number: int
    message: str
    consumed_iteration: int | None
    created_at: str


def _run(row: sqlite3.Row) -> RalphRun:
    d = dict(row)
    d["acceptance"] = json.loads(d["acceptance"])
    d["script"] = json.loads(d["script"]) if d["script"] else None
    for key in ("auto_commit", "pause_requested", "cancel_requested", "needs_attention", "awaiting_acceptance"):
        d[key] = bool(d[key])
    return RalphRun(**d)


def create_run(
    db: sqlite3.Connection,
    project_id: int,
    repository_id: int,
    title: str,
    task_text: str,
    verification_pipeline: str,
    acceptance: list[str] | None = None,
    work_item_id: int | None = None,
    sprint_id: int | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_runtime_seconds: float | None = None,
    identical_failure_limit: int = DEFAULT_IDENTICAL_FAILURE_LIMIT,
    no_change_limit: int = DEFAULT_NO_CHANGE_LIMIT,
    auto_commit: bool = True,
    script: list | None = None,
) -> int:
    if not verification_pipeline:
        raise ValueError("A Ralph run needs a verification pipeline: the agent saying it is done is not enough")
    if max_iterations < 1 or identical_failure_limit < 1 or no_change_limit < 1:
        raise ValueError("Iteration and no-progress limits must be at least 1")
    cur = db.execute(
        "INSERT INTO ralph_runs (project_id, repository_id, work_item_id, sprint_id, title, task_text, "
        "acceptance, verification_pipeline, max_iterations, max_runtime_seconds, identical_failure_limit, "
        "no_change_limit, auto_commit, script, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            project_id, repository_id, work_item_id, sprint_id, title.strip() or f"Task {work_item_id}",
            task_text, json.dumps(acceptance or []), verification_pipeline, max_iterations,
            max_runtime_seconds, identical_failure_limit, no_change_limit, int(auto_commit),
            json.dumps(script) if script is not None else None, now(),
        ),
    )
    db.commit()
    return cur.lastrowid


def get_run(db: sqlite3.Connection, run_id: int) -> RalphRun | None:
    row = db.execute("SELECT * FROM ralph_runs WHERE id = ?", (run_id,)).fetchone()
    return _run(row) if row else None


def list_runs(db: sqlite3.Connection, project_id: int, limit: int = 50) -> list[RalphRun]:
    rows = db.execute(
        "SELECT * FROM ralph_runs WHERE project_id = ? ORDER BY id DESC LIMIT ?", (project_id, limit)
    ).fetchall()
    return [_run(r) for r in rows]


def update_run(db: sqlite3.Connection, run_id: int, **fields) -> None:
    assignments = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE ralph_runs SET {assignments} WHERE id = ?", [*fields.values(), run_id])
    db.commit()


def add_iteration(db: sqlite3.Connection, run_id: int, number: int, prompt: str, redacted: bool) -> int:
    cur = db.execute(
        "INSERT INTO ralph_iterations (run_id, number, status, prompt, redacted, started_at) "
        "VALUES (?, ?, 'RUNNING_AGENT', ?, ?, ?)",
        (run_id, number, prompt, int(redacted), now()),
    )
    db.commit()
    return cur.lastrowid


def _iteration(row: sqlite3.Row) -> Iteration:
    d = dict(row)
    d["changed_files"] = json.loads(d["changed_files"])
    d["redacted"] = bool(d["redacted"])
    return Iteration(**d)


def get_iteration(db: sqlite3.Connection, iteration_id: int) -> Iteration | None:
    row = db.execute("SELECT * FROM ralph_iterations WHERE id = ?", (iteration_id,)).fetchone()
    return _iteration(row) if row else None


def list_iterations(db: sqlite3.Connection, run_id: int) -> list[Iteration]:
    rows = db.execute("SELECT * FROM ralph_iterations WHERE run_id = ? ORDER BY number", (run_id,)).fetchall()
    return [_iteration(r) for r in rows]


def update_iteration(db: sqlite3.Connection, iteration_id: int, **fields) -> None:
    if "changed_files" in fields:
        fields["changed_files"] = json.dumps(fields["changed_files"])
    if "redacted" in fields:
        fields["redacted"] = int(fields["redacted"])
    assignments = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE ralph_iterations SET {assignments} WHERE id = ?", [*fields.values(), iteration_id])
    db.commit()


def add_steering(db: sqlite3.Connection, run_id: int, message: str, iteration_number: int) -> int:
    message = message.strip()
    if not message:
        raise ValueError("Steering cannot be empty")
    cur = db.execute(
        "INSERT INTO ralph_steering (run_id, iteration_number, message, created_at) VALUES (?, ?, ?, ?)",
        (run_id, iteration_number, message, now()),
    )
    db.commit()
    return cur.lastrowid


def unconsumed_steering(db: sqlite3.Connection, run_id: int) -> list[Steering]:
    rows = db.execute(
        "SELECT * FROM ralph_steering WHERE run_id = ? AND consumed_iteration IS NULL ORDER BY id", (run_id,)
    ).fetchall()
    return [Steering(**dict(r)) for r in rows]


def list_steering(db: sqlite3.Connection, run_id: int) -> list[Steering]:
    rows = db.execute("SELECT * FROM ralph_steering WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return [Steering(**dict(r)) for r in rows]


def consume_steering(db: sqlite3.Connection, steering_ids: list[int], iteration_number: int) -> None:
    db.executemany(
        "UPDATE ralph_steering SET consumed_iteration = ? WHERE id = ?",
        [(iteration_number, i) for i in steering_ids],
    )
    db.commit()
