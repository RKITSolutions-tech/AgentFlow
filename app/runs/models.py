from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

RUN_ACTIVE_STATUSES = ("CREATED", "RUNNING", "PAUSED")
RUN_TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT")
# BLOCKED needs a human: the process it was watching disappeared.
RUN_RESTARTABLE_STATUSES = (*RUN_TERMINAL_STATUSES, "BLOCKED")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def duration_seconds(started_at: str | None, completed_at: str | None) -> float | None:
    if not started_at:
        return None
    end = datetime.fromisoformat(completed_at) if completed_at else datetime.now(timezone.utc)
    return max((end - datetime.fromisoformat(started_at)).total_seconds(), 0.0)


@dataclass
class Step:
    id: int
    run_id: int
    seq: int
    name: str
    command: str
    command_redacted: bool
    timeout_seconds: float | None
    collect: list[str]
    status: str
    process_id: int | None
    exit_code: int | None
    prompt: str | None
    reply: str | None
    started_at: str | None
    completed_at: str | None

    @property
    def duration(self) -> float | None:
        return duration_seconds(self.started_at, self.completed_at)


@dataclass
class Run:
    id: int
    project_id: int
    repository_id: int
    title: str
    status: str
    status_reason: str
    restart_of: int | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    steps: list[Step] = field(default_factory=list)

    @property
    def duration(self) -> float | None:
        return duration_seconds(self.started_at, self.completed_at)

    @property
    def active(self) -> bool:
        return self.status in RUN_ACTIVE_STATUSES


@dataclass
class RunEvent:
    id: int
    run_id: int
    step_id: int | None
    event_type: str
    data: str
    created_at: str


@dataclass
class Artifact:
    id: int
    run_id: int
    step_id: int | None
    kind: str
    name: str
    path: str
    size: int
    redacted: bool
    created_at: str


def create_run(
    db: sqlite3.Connection,
    project_id: int,
    repository_id: int,
    title: str,
    steps: list[dict],
    restart_of: int | None = None,
) -> int:
    """Insert a Run with its ordered Steps. Each step dict needs `command`;
    `name`, `timeout_seconds`, `collect`, `command_redacted` and `prompt` are optional."""
    cur = db.execute(
        "INSERT INTO runs (project_id, repository_id, title, restart_of, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (project_id, repository_id, title, restart_of, now()),
    )
    run_id = cur.lastrowid
    for seq, step in enumerate(steps, start=1):
        db.execute(
            "INSERT INTO run_steps (run_id, seq, name, command, command_redacted, "
            "timeout_seconds, collect, prompt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                seq,
                step.get("name") or step["command"][:60],
                step["command"],
                1 if step.get("command_redacted") else 0,
                step.get("timeout_seconds"),
                json.dumps(step.get("collect") or []),
                step.get("prompt"),
            ),
        )
    add_event(db, run_id, "RunCreated", data=title, commit=False)
    db.commit()
    return run_id


def get_run(db: sqlite3.Connection, run_id: int, with_steps: bool = True) -> Run | None:
    row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    run = _hydrate_run(row)
    if with_steps:
        run.steps = list_steps(db, run_id)
    return run


def list_runs(
    db: sqlite3.Connection,
    project_id: int,
    status: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> list[Run]:
    sql = "SELECT * FROM runs WHERE project_id = ?"
    params: list = [project_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    runs = [_hydrate_run(r) for r in db.execute(sql, params).fetchall()]
    for run in runs:
        run.steps = list_steps(db, run.id)
    return runs


def count_runs(db: sqlite3.Connection, project_id: int, status: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM runs WHERE project_id = ?"
    params: list = [project_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    return db.execute(sql, params).fetchone()[0]


def list_runs_by_status(db: sqlite3.Connection, statuses: tuple[str, ...]) -> list[Run]:
    marks = ",".join("?" * len(statuses))
    rows = db.execute(
        f"SELECT * FROM runs WHERE status IN ({marks}) ORDER BY id", statuses
    ).fetchall()
    runs = [_hydrate_run(r) for r in rows]
    for run in runs:
        run.steps = list_steps(db, run.id)
    return runs


def set_run_status(
    db: sqlite3.Connection, run_id: int, status: str, reason: str = ""
) -> None:
    fields, params = ["status = ?", "status_reason = ?"], [status, reason]
    if status == "RUNNING":
        fields.append("started_at = COALESCE(started_at, ?)")
        params.append(now())
    if status in RUN_TERMINAL_STATUSES or status == "BLOCKED":
        fields.append("completed_at = ?")
        params.append(now())
    params.append(run_id)
    db.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", params)
    db.commit()


def list_steps(db: sqlite3.Connection, run_id: int) -> list[Step]:
    rows = db.execute(
        "SELECT * FROM run_steps WHERE run_id = ? ORDER BY seq", (run_id,)
    ).fetchall()
    return [_hydrate_step(r) for r in rows]


def get_step(db: sqlite3.Connection, step_id: int) -> Step | None:
    row = db.execute("SELECT * FROM run_steps WHERE id = ?", (step_id,)).fetchone()
    return _hydrate_step(row) if row else None


def mark_step_started(db: sqlite3.Connection, step_id: int) -> None:
    db.execute(
        "UPDATE run_steps SET status = 'RUNNING', started_at = ? WHERE id = ?",
        (now(), step_id),
    )
    db.commit()


def set_step_process(db: sqlite3.Connection, step_id: int, process_id: int) -> None:
    db.execute("UPDATE run_steps SET process_id = ? WHERE id = ?", (process_id, step_id))
    db.commit()


def finish_step(
    db: sqlite3.Connection,
    step_id: int,
    status: str,
    exit_code: int | None = None,
    reply: str | None = None,
) -> None:
    db.execute(
        "UPDATE run_steps SET status = ?, exit_code = ?, reply = COALESCE(?, reply), "
        "completed_at = ? WHERE id = ?",
        (status, exit_code, reply, now(), step_id),
    )
    db.commit()


def add_event(
    db: sqlite3.Connection,
    run_id: int,
    event_type: str,
    step_id: int | None = None,
    data: str = "",
    commit: bool = True,
) -> int:
    cur = db.execute(
        "INSERT INTO run_events (run_id, step_id, event_type, data, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_id, step_id, event_type, data, now()),
    )
    if commit:
        db.commit()
    return cur.lastrowid


def list_events(db: sqlite3.Connection, run_id: int) -> list[RunEvent]:
    rows = db.execute(
        "SELECT * FROM run_events WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    return [RunEvent(**{k: r[k] for k in r.keys()}) for r in rows]


def add_artifact(
    db: sqlite3.Connection,
    run_id: int,
    step_id: int | None,
    kind: str,
    name: str,
    path: str,
    size: int,
    redacted: bool = False,
) -> int:
    cur = db.execute(
        "INSERT INTO run_artifacts (run_id, step_id, kind, name, path, size, redacted, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, step_id, kind, name, path, size, 1 if redacted else 0, now()),
    )
    db.commit()
    return cur.lastrowid


def list_artifacts(
    db: sqlite3.Connection,
    run_id: int,
    step_id: int | None = None,
    kind: str | None = None,
) -> list[Artifact]:
    sql, params = "SELECT * FROM run_artifacts WHERE run_id = ?", [run_id]
    if step_id is not None:
        sql += " AND step_id = ?"
        params.append(step_id)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    rows = db.execute(sql + " ORDER BY id", params).fetchall()
    return [_hydrate_artifact(r) for r in rows]


def get_artifact(db: sqlite3.Connection, run_id: int, artifact_id: int) -> Artifact | None:
    row = db.execute(
        "SELECT * FROM run_artifacts WHERE id = ? AND run_id = ?", (artifact_id, run_id)
    ).fetchone()
    return _hydrate_artifact(row) if row else None


def _hydrate_run(row: sqlite3.Row) -> Run:
    return Run(**{k: row[k] for k in row.keys()})


def _hydrate_step(row: sqlite3.Row) -> Step:
    data = {k: row[k] for k in row.keys()}
    data["command_redacted"] = bool(data["command_redacted"])
    data["collect"] = json.loads(data["collect"])
    return Step(**data)


def _hydrate_artifact(row: sqlite3.Row) -> Artifact:
    data = {k: row[k] for k in row.keys()}
    data["redacted"] = bool(data["redacted"])
    return Artifact(**data)
