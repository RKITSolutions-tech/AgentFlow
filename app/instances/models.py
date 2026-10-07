from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class RemoteInstance:
    id: int
    name: str
    base_url: str
    token: str
    created_at: str
    last_seen_at: str | None
    last_status: str


def _hydrate(row: sqlite3.Row) -> RemoteInstance:
    return RemoteInstance(
        id=row["id"],
        name=row["name"],
        base_url=row["base_url"],
        token=row["token"],
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
        last_status=row["last_status"],
    )


def list_instances(db: sqlite3.Connection) -> list[RemoteInstance]:
    rows = db.execute("SELECT * FROM remote_instances ORDER BY name").fetchall()
    return [_hydrate(row) for row in rows]


def get_instance(db: sqlite3.Connection, instance_id: int) -> RemoteInstance | None:
    row = db.execute("SELECT * FROM remote_instances WHERE id = ?", (instance_id,)).fetchone()
    return _hydrate(row) if row else None


def add_instance(db: sqlite3.Connection, name: str, base_url: str, token: str) -> int:
    cur = db.execute(
        "INSERT INTO remote_instances (name, base_url, token) VALUES (?, ?, ?)",
        (name, base_url.rstrip("/"), token),
    )
    db.commit()
    return cur.lastrowid


def delete_instance(db: sqlite3.Connection, instance_id: int) -> None:
    db.execute("DELETE FROM remote_instances WHERE id = ?", (instance_id,))
    db.commit()


def set_status(db: sqlite3.Connection, instance_id: int, status: str) -> None:
    db.execute(
        "UPDATE remote_instances SET last_status = ?, last_seen_at = datetime('now') WHERE id = ?",
        (status, instance_id),
    )
    db.commit()
