from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from app.security import validate_repository_path


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "project"


@dataclass
class Repository:
    id: int
    project_id: int
    name: str
    path: str
    is_primary: bool


@dataclass
class Project:
    id: int
    name: str
    slug: str
    description: str
    status: str
    repositories: list[Repository]


def _unique_slug(db: sqlite3.Connection, base_slug: str) -> str:
    slug = base_slug
    suffix = 2
    while db.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def create_project(db: sqlite3.Connection, name: str, description: str = "") -> int:
    slug = _unique_slug(db, slugify(name))
    cur = db.execute(
        "INSERT INTO projects (name, slug, description) VALUES (?, ?, ?)",
        (name, slug, description),
    )
    db.commit()
    return cur.lastrowid


def update_project(
    db: sqlite3.Connection, project_id: int, name: str, description: str
) -> None:
    db.execute(
        "UPDATE projects SET name = ?, description = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (name, description, project_id),
    )
    db.commit()


def add_repository(
    db: sqlite3.Connection,
    project_id: int,
    name: str,
    path: str,
    allowed_roots: tuple[str, ...],
    is_primary: bool = False,
) -> int:
    resolved_path = validate_repository_path(path, allowed_roots)

    if is_primary:
        db.execute(
            "UPDATE repositories SET is_primary = 0 WHERE project_id = ?",
            (project_id,),
        )
    else:
        has_primary = db.execute(
            "SELECT 1 FROM repositories WHERE project_id = ? AND is_primary = 1",
            (project_id,),
        ).fetchone()
        if not has_primary:
            is_primary = True

    cur = db.execute(
        "INSERT INTO repositories (project_id, name, path, is_primary) "
        "VALUES (?, ?, ?, ?)",
        (project_id, name, resolved_path, int(is_primary)),
    )
    db.commit()
    return cur.lastrowid


def list_projects(db: sqlite3.Connection) -> list[Project]:
    rows = db.execute("SELECT * FROM projects ORDER BY name").fetchall()
    return [_hydrate_project(db, row) for row in rows]


def get_project(db: sqlite3.Connection, project_id: int) -> Project | None:
    row = db.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        return None
    return _hydrate_project(db, row)


def get_repository(db: sqlite3.Connection, project_id: int, repo_id: int) -> Repository | None:
    row = db.execute(
        "SELECT * FROM repositories WHERE id = ? AND project_id = ?",
        (repo_id, project_id),
    ).fetchone()
    if row is None:
        return None
    return Repository(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        path=row["path"],
        is_primary=bool(row["is_primary"]),
    )


def _hydrate_project(db: sqlite3.Connection, row: sqlite3.Row) -> Project:
    repo_rows = db.execute(
        "SELECT * FROM repositories WHERE project_id = ? ORDER BY is_primary DESC, name",
        (row["id"],),
    ).fetchall()
    repositories = [
        Repository(
            id=r["id"],
            project_id=r["project_id"],
            name=r["name"],
            path=r["path"],
            is_primary=bool(r["is_primary"]),
        )
        for r in repo_rows
    ]
    return Project(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        status=row["status"],
        repositories=repositories,
    )
