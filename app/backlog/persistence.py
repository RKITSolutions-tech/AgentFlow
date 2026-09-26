from __future__ import annotations

import sqlite3

from app.backlog.models import (
    ATTACHMENT_KINDS,
    BACKLOG_PRIORITIES,
    BACKLOG_STATUSES,
    TRANSITIONS,
    BacklogAttachment,
    BacklogItem,
    InvalidTransitionError,
    TriageEntry,
)
from app.runs.models import now

_UPDATABLE = ("title", "text", "priority", "sprint_id", "source_type", "source_reference")


def _check_priority(priority: str | None) -> str | None:
    if priority is None or priority == "":
        return None
    priority = priority.upper()
    if priority not in BACKLOG_PRIORITIES:
        raise ValueError(f"Unknown priority {priority!r}")
    return priority


def create_item(
    db: sqlite3.Connection,
    project_id: int,
    text: str = "",
    title: str = "",
    priority: str | None = None,
    created_by: str = "",
    source_type: str = "",
    source_reference: str = "",
) -> int:
    """Insert an INBOX item. Text is optional here because an attachment alone
    is valid capture (design §5); callers enforce "text or attachment"."""
    stamp = now()
    cur = db.execute(
        "INSERT INTO backlog_items (project_id, title, text, priority, created_by, "
        "source_type, source_reference, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id, title.strip(), text.strip(), _check_priority(priority),
            created_by, source_type, source_reference, stamp, stamp,
        ),
    )
    item_id = cur.lastrowid
    _record(db, item_id, None, "INBOX", "created", created_by)
    db.commit()
    return item_id


def get_item(db: sqlite3.Connection, item_id: int) -> BacklogItem | None:
    row = db.execute("SELECT * FROM backlog_items WHERE id = ?", (item_id,)).fetchone()
    return BacklogItem(**dict(row)) if row else None


def list_items(
    db: sqlite3.Connection,
    project_id: int,
    status: str | None = None,
    priority: str | None = None,
    sprint_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[BacklogItem]:
    sql, params = "SELECT * FROM backlog_items WHERE project_id = ?", [project_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    if priority:
        sql += " AND priority = ?"
        params.append(priority)
    if sprint_id is not None:
        sql += " AND sprint_id = ?"
        params.append(sprint_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [BacklogItem(**dict(r)) for r in db.execute(sql, params).fetchall()]


def update_item(db: sqlite3.Connection, item_id: int, **fields) -> None:
    """Edit content fields. Status changes go through `transition`."""
    unknown = set(fields) - set(_UPDATABLE)
    if unknown:
        raise ValueError(f"Cannot update {sorted(unknown)}")
    if not fields:
        return
    if "priority" in fields:
        fields["priority"] = _check_priority(fields["priority"])
    assignments = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE backlog_items SET {assignments}, updated_at = ? WHERE id = ?",
        [*fields.values(), now(), item_id],
    )
    db.commit()


def transition(
    db: sqlite3.Connection,
    item_id: int,
    new_status: str,
    notes: str = "",
    changed_by: str = "",
) -> None:
    if new_status not in BACKLOG_STATUSES:
        raise ValueError(f"Unknown status {new_status!r}")
    item = get_item(db, item_id)
    if item is None:
        raise LookupError(f"Backlog item {item_id} not found")
    if new_status not in TRANSITIONS[item.status]:
        raise InvalidTransitionError(f"{item.status} -> {new_status} is not allowed")
    db.execute(
        "UPDATE backlog_items SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, now(), item_id),
    )
    _record(db, item_id, item.status, new_status, notes, changed_by)
    db.commit()


def delete_item(db: sqlite3.Connection, item_id: int) -> None:
    db.execute("DELETE FROM backlog_items WHERE id = ?", (item_id,))
    db.commit()


def _record(db, item_id, old_status, new_status, notes, changed_by) -> None:
    db.execute(
        "INSERT INTO backlog_triage_history (backlog_item_id, old_status, new_status, "
        "notes, changed_by, changed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, old_status, new_status, notes, changed_by, now()),
    )


def list_history(db: sqlite3.Connection, item_id: int) -> list[TriageEntry]:
    rows = db.execute(
        "SELECT * FROM backlog_triage_history WHERE backlog_item_id = ? ORDER BY id", (item_id,)
    ).fetchall()
    return [TriageEntry(**dict(r)) for r in rows]


def add_attachment(
    db: sqlite3.Connection, item_id: int, kind: str, name: str, path: str, size: int = 0
) -> int:
    if kind not in ATTACHMENT_KINDS:
        raise ValueError(f"Unknown attachment kind {kind!r}")
    cur = db.execute(
        "INSERT INTO backlog_attachments (backlog_item_id, kind, name, path, size, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, kind, name, path, size, now()),
    )
    db.commit()
    return cur.lastrowid


def list_attachments(db: sqlite3.Connection, item_id: int) -> list[BacklogAttachment]:
    rows = db.execute(
        "SELECT * FROM backlog_attachments WHERE backlog_item_id = ? ORDER BY id", (item_id,)
    ).fetchall()
    return [BacklogAttachment(**dict(r)) for r in rows]


def get_attachment(
    db: sqlite3.Connection, item_id: int, attachment_id: int
) -> BacklogAttachment | None:
    row = db.execute(
        "SELECT * FROM backlog_attachments WHERE id = ? AND backlog_item_id = ?",
        (attachment_id, item_id),
    ).fetchone()
    return BacklogAttachment(**dict(row)) if row else None
