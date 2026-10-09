from __future__ import annotations

import sqlite3

from app.backlog.models import (
    ATTACHMENT_KINDS,
    BACKLOG_PRIORITIES,
    BACKLOG_STATUSES,
    RESEARCH_LINK_STATUSES,
    TEST_PROPOSAL_STATUSES,
    TRANSITIONS,
    BacklogAttachment,
    BacklogItem,
    BacklogResearchLink,
    BacklogTestProposal,
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
    project_id: int | None = None,
    status: str | None = None,
    priority: str | None = None,
    sprint_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    order_by: str = "id",
) -> list[BacklogItem]:
    sql, params = "SELECT * FROM backlog_items", []
    clauses = []
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)
    if sprint_id is not None:
        clauses.append("sprint_id = ?")
        params.append(sprint_id)
    if order_by not in ("id", "created_at", "updated_at"):
        raise ValueError(f"Invalid order_by {order_by!r}")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" ORDER BY {order_by} DESC LIMIT ? OFFSET ?"
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


def record_note(db: sqlite3.Connection, item_id: int, notes: str, changed_by: str = "") -> None:
    """Log a `backlog_triage_history` row without changing status -- for
    actions that touch an item's content but not its lifecycle stage, e.g.
    accepting a research summary (§50)."""
    item = get_item(db, item_id)
    if item is None:
        raise LookupError(f"Backlog item {item_id} not found")
    _record(db, item_id, item.status, item.status, notes, changed_by)
    db.commit()


# -- research links (§50, task 44) ------------------------------------------


def add_research_link(
    db: sqlite3.Connection,
    item_id: int,
    research_session_id: int,
    artifact_id: int | None,
    outcome_state: str,
) -> int:
    cur = db.execute(
        "INSERT INTO backlog_research_links "
        "(backlog_item_id, research_session_id, artifact_id, outcome_state, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (item_id, research_session_id, artifact_id, outcome_state, now()),
    )
    db.commit()
    return cur.lastrowid


def list_research_links(db: sqlite3.Connection, item_id: int) -> list[BacklogResearchLink]:
    rows = db.execute(
        "SELECT * FROM backlog_research_links WHERE backlog_item_id = ? ORDER BY id DESC", (item_id,)
    ).fetchall()
    return [BacklogResearchLink(**dict(r)) for r in rows]


def get_research_link(db: sqlite3.Connection, link_id: int) -> BacklogResearchLink | None:
    row = db.execute("SELECT * FROM backlog_research_links WHERE id = ?", (link_id,)).fetchone()
    return BacklogResearchLink(**dict(row)) if row else None


def set_research_link_status(db: sqlite3.Connection, link_id: int, status: str) -> None:
    if status not in RESEARCH_LINK_STATUSES:
        raise ValueError(f"Unknown research link status {status!r}")
    db.execute(
        "UPDATE backlog_research_links SET status = ?, reviewed_at = ? WHERE id = ?",
        (status, now(), link_id),
    )
    db.commit()


# -- test proposals (§51, "Design this item") -------------------------------


def add_test_proposal(
    db: sqlite3.Connection,
    item_id: int,
    design_session_id: int | None,
    title: str,
    description: str = "",
    pytest_node_id: str = "",
) -> int:
    cur = db.execute(
        "INSERT INTO backlog_test_proposals "
        "(backlog_item_id, design_session_id, title, description, pytest_node_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, design_session_id, title.strip(), description.strip(), pytest_node_id.strip(), now()),
    )
    db.commit()
    return cur.lastrowid


def list_test_proposals(db: sqlite3.Connection, item_id: int) -> list[BacklogTestProposal]:
    rows = db.execute(
        "SELECT * FROM backlog_test_proposals WHERE backlog_item_id = ? ORDER BY id DESC", (item_id,)
    ).fetchall()
    return [BacklogTestProposal(**dict(r)) for r in rows]


def get_test_proposal(db: sqlite3.Connection, proposal_id: int) -> BacklogTestProposal | None:
    row = db.execute("SELECT * FROM backlog_test_proposals WHERE id = ?", (proposal_id,)).fetchone()
    return BacklogTestProposal(**dict(row)) if row else None


def set_test_proposal_status(db: sqlite3.Connection, proposal_id: int, status: str) -> None:
    if status not in TEST_PROPOSAL_STATUSES:
        raise ValueError(f"Unknown test proposal status {status!r}")
    db.execute(
        "UPDATE backlog_test_proposals SET status = ?, reviewed_at = ? WHERE id = ?",
        (status, now(), proposal_id),
    )
    db.commit()
