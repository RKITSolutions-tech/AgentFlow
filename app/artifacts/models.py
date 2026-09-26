"""Global Artifact Library (docs/PIPELINE_VISUALISATION.md §14-§15).

Every artifact keeps the StepExecution it came from (`step_execution_id`) and,
for Ralph, the run and iteration. File content lives under the artifact root;
this table is the searchable index.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from app.runs.models import now

KINDS = ("screenshot", "trace", "log", "diff", "report", "video", "file")


@dataclass
class Artifact:
    id: int
    project_id: int
    kind: str
    name: str
    path: str
    mime_type: str
    size: int
    execution_id: int | None
    step_execution_id: int | None
    step_name: str
    ralph_run_id: int | None
    iteration_number: int | None
    redacted: bool
    metadata: dict
    created_at: str
    tags: list[str] = field(default_factory=list)


@dataclass
class Comparison:
    id: int
    artifact_a_id: int
    artifact_b_id: int
    comparison_type: str
    result: dict
    created_at: str


def _artifact(db: sqlite3.Connection, row: sqlite3.Row) -> Artifact:
    d = dict(row)
    d["metadata"] = json.loads(d["metadata"])
    d["redacted"] = bool(d["redacted"])
    a = Artifact(**d)
    a.tags = [r[0] for r in db.execute("SELECT tag FROM artifact_tags WHERE artifact_id = ? ORDER BY tag", (a.id,))]
    return a


def add_artifact(
    db: sqlite3.Connection,
    project_id: int,
    kind: str,
    name: str,
    path: str,
    mime_type: str = "application/octet-stream",
    size: int = 0,
    execution_id: int | None = None,
    step_execution_id: int | None = None,
    step_name: str = "",
    ralph_run_id: int | None = None,
    iteration_number: int | None = None,
    redacted: bool = False,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> int:
    if kind not in KINDS:
        raise ValueError(f"Unknown artifact kind {kind!r}")
    cur = db.execute(
        "INSERT INTO artifact_library (project_id, kind, name, path, mime_type, size, execution_id, "
        "step_execution_id, step_name, ralph_run_id, iteration_number, redacted, metadata, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            project_id, kind, name, path, mime_type, size, execution_id, step_execution_id, step_name,
            ralph_run_id, iteration_number, int(redacted), json.dumps(metadata or {}), now(),
        ),
    )
    artifact_id = cur.lastrowid
    for tag in tags or []:
        _add_tag(db, artifact_id, tag)
    db.commit()
    return artifact_id


def _add_tag(db: sqlite3.Connection, artifact_id: int, tag: str) -> None:
    tag = tag.strip().lower()[:40]
    if tag:
        db.execute("INSERT OR IGNORE INTO artifact_tags (artifact_id, tag) VALUES (?, ?)", (artifact_id, tag))


def add_tags(db: sqlite3.Connection, artifact_id: int, tags: list[str]) -> None:
    for tag in tags:
        _add_tag(db, artifact_id, tag)
    db.commit()


def remove_tag(db: sqlite3.Connection, artifact_id: int, tag: str) -> None:
    db.execute("DELETE FROM artifact_tags WHERE artifact_id = ? AND tag = ?", (artifact_id, tag.strip().lower()))
    db.commit()


def get_artifact(db: sqlite3.Connection, artifact_id: int) -> Artifact | None:
    row = db.execute("SELECT * FROM artifact_library WHERE id = ?", (artifact_id,)).fetchone()
    return _artifact(db, row) if row else None


def _where(project_id, kinds, since, until, step_name, execution_id, ralph_run_id, iteration, tag, q):
    sql, params = " WHERE a.project_id = ?", [project_id]
    if kinds:
        sql += f" AND a.kind IN ({','.join('?' * len(kinds))})"
        params += list(kinds)
    if since:
        sql += " AND a.created_at >= ?"
        params.append(since)
    if until:
        sql += " AND a.created_at < ?"
        params.append(until + "￿")
    if step_name:
        sql += " AND a.step_name = ?"
        params.append(step_name)
    if execution_id:
        sql += " AND a.execution_id = ?"
        params.append(execution_id)
    if ralph_run_id:
        sql += " AND a.ralph_run_id = ?"
        params.append(ralph_run_id)
    if iteration:
        sql += " AND a.iteration_number = ?"
        params.append(iteration)
    if tag:
        sql += " AND EXISTS (SELECT 1 FROM artifact_tags t WHERE t.artifact_id = a.id AND t.tag = ?)"
        params.append(tag.strip().lower())
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql += (
            " AND (a.name LIKE ? ESCAPE '\\' OR a.step_name LIKE ? ESCAPE '\\' OR a.metadata LIKE ? ESCAPE '\\'"
            " OR EXISTS (SELECT 1 FROM artifact_tags t WHERE t.artifact_id = a.id AND t.tag LIKE ? ESCAPE '\\'))"
        )
        params += [like, like, like, like]
    return sql, params


def search(
    db: sqlite3.Connection,
    project_id: int,
    kinds: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    step_name: str | None = None,
    execution_id: int | None = None,
    ralph_run_id: int | None = None,
    iteration: int | None = None,
    tag: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Artifact], int]:
    where, params = _where(project_id, kinds, since, until, step_name, execution_id, ralph_run_id, iteration, tag, q)
    total = db.execute("SELECT COUNT(*) FROM artifact_library a" + where, params).fetchone()[0]
    rows = db.execute(
        "SELECT a.* FROM artifact_library a" + where + " ORDER BY a.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [_artifact(db, r) for r in rows], total


def step_names(db: sqlite3.Connection, project_id: int) -> list[str]:
    return [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT step_name FROM artifact_library WHERE project_id = ? AND step_name != '' ORDER BY step_name",
            (project_id,),
        )
    ]


def save_comparison(db: sqlite3.Connection, a: int, b: int, comparison_type: str, result: dict) -> int:
    cur = db.execute(
        "INSERT INTO artifact_comparisons (artifact_a_id, artifact_b_id, comparison_type, result, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (a, b, comparison_type, json.dumps(result), now()),
    )
    db.commit()
    return cur.lastrowid


def get_comparison(db: sqlite3.Connection, comparison_id: int) -> Comparison | None:
    row = db.execute("SELECT * FROM artifact_comparisons WHERE id = ?", (comparison_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["result"] = json.loads(d["result"])
    return Comparison(**d)
