"""Knowledge base entry models and persistence (task 48)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from app.runs.models import now


@dataclass
class KnowledgeEntry:
    id: int
    kind: str
    title: str
    content: str
    url: str | None
    source_project_id: int | None
    source_session_id: int | None
    source_step_id: int | None
    confidence: str
    scope: str
    pinned: bool
    use_count: int
    created_at: str
    updated_at: str
    tags: list[str] | None = None


@dataclass
class KnowledgeProvenanceHistory:
    id: int
    entry_id: int
    action: str
    user: str
    timestamp: str


def create_entry(
    db: sqlite3.Connection,
    kind: str,
    title: str,
    content: str,
    scope: str = "global",
    url: str | None = None,
    source_project_id: int | None = None,
    source_session_id: int | None = None,
    source_step_id: int | None = None,
    confidence: str = "unverified",
    tags: list[str] | None = None,
) -> int:
    """Create a new knowledge entry. Returns entry ID."""
    if kind not in ("web_cache", "note"):
        raise ValueError(f"Invalid kind: {kind}")
    if confidence not in ("unverified", "reviewed"):
        raise ValueError(f"Invalid confidence: {confidence}")
    cur = db.execute(
        "INSERT INTO knowledge_entries "
        "(kind, title, content, url, source_project_id, source_session_id, source_step_id, "
        "confidence, scope, pinned, use_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
        (kind, title.strip(), content, url, source_project_id, source_session_id, source_step_id,
         confidence, scope, now(), now()),
    )
    entry_id = cur.lastrowid
    if tags:
        for tag in set(tags):
            db.execute("INSERT INTO knowledge_tags (entry_id, tag) VALUES (?, ?)", (entry_id, tag))
    log_provenance(db, entry_id, "created", "system")
    db.commit()
    return entry_id


def get_entry(db: sqlite3.Connection, entry_id: int) -> KnowledgeEntry | None:
    """Fetch a knowledge entry by ID."""
    row = db.execute("SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["tags"] = [t["tag"] for t in db.execute(
        "SELECT tag FROM knowledge_tags WHERE entry_id = ?", (entry_id,)
    ).fetchall()]
    return KnowledgeEntry(**d)


def list_entries(
    db: sqlite3.Connection,
    kind: str | None = None,
    confidence: str | None = None,
    scope: str | None = None,
    tags: list[str] | None = None,
    limit: int = 100,
) -> list[KnowledgeEntry]:
    """List knowledge entries with filters."""
    query = "SELECT * FROM knowledge_entries WHERE 1=1"
    params = []
    if kind:
        query += " AND kind = ?"
        params.append(kind)
    if confidence:
        query += " AND confidence = ?"
        params.append(confidence)
    if scope:
        query += " AND scope = ?"
        params.append(scope)
    query += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(query, params).fetchall()
    entries = []
    for row in rows:
        d = dict(row)
        d["tags"] = [t["tag"] for t in db.execute(
            "SELECT tag FROM knowledge_tags WHERE entry_id = ?", (row["id"],)
        ).fetchall()]
        entries.append(KnowledgeEntry(**d))
    return entries


def update_entry(db: sqlite3.Connection, entry_id: int, **fields) -> None:
    """Update an entry. Valid fields: title, content, confidence, tags."""
    allowed = {"title", "content", "confidence", "tags"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    tags = fields.pop("tags", None)
    fields["updated_at"] = now()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE knowledge_entries SET {assignments} WHERE id = ?", [*fields.values(), entry_id])
    if tags is not None:
        db.execute("DELETE FROM knowledge_tags WHERE entry_id = ?", (entry_id,))
        for tag in set(tags):
            db.execute("INSERT INTO knowledge_tags (entry_id, tag) VALUES (?, ?)", (entry_id, tag))
    db.commit()


def increment_use_count(db: sqlite3.Connection, entry_id: int) -> None:
    """Increment use_count for an entry."""
    db.execute("UPDATE knowledge_entries SET use_count = use_count + 1 WHERE id = ?", (entry_id,))
    db.commit()


def pin_entry(db: sqlite3.Connection, entry_id: int, pinned: bool) -> None:
    """Pin or unpin an entry."""
    db.execute("UPDATE knowledge_entries SET pinned = ? WHERE id = ?", (int(pinned), entry_id))
    db.commit()


def promote_entry(db: sqlite3.Connection, entry_id: int, user: str) -> None:
    """Promote an entry from unverified to reviewed."""
    entry = get_entry(db, entry_id)
    if entry is None:
        raise LookupError(f"Entry {entry_id} not found")
    if entry.confidence == "reviewed":
        return
    update_entry(db, entry_id, confidence="reviewed")
    log_provenance(db, entry_id, "promoted", user)


def merge_entries(db: sqlite3.Connection, source_id: int, target_id: int, user: str) -> None:
    """Merge source entry into target (higher confidence wins, tags combined, use_counts summed)."""
    source = get_entry(db, source_id)
    target = get_entry(db, target_id)
    if source is None or target is None:
        raise LookupError("Entry not found")
    if source.confidence == "reviewed" and target.confidence == "unverified":
        source_id, target_id = target_id, source_id
        source, target = target, source
    combined_tags = sorted(set((source.tags or []) + (target.tags or [])))
    new_use_count = source.use_count + target.use_count
    update_entry(db, target_id, tags=combined_tags)
    db.execute(
        "UPDATE knowledge_entries SET use_count = ? WHERE id = ?",
        (new_use_count, target_id),
    )
    db.execute("DELETE FROM knowledge_entries WHERE id = ?", (source_id,))
    log_provenance(db, target_id, f"merged_from_{source_id}", user)
    db.commit()


def delete_entry(db: sqlite3.Connection, entry_id: int) -> None:
    """Soft delete an entry (mark as deleted via confidence)."""
    entry = get_entry(db, entry_id)
    if entry is None:
        raise LookupError(f"Entry {entry_id} not found")
    update_entry(db, entry_id, confidence="deleted")


def log_provenance(db: sqlite3.Connection, entry_id: int, action: str, user: str) -> None:
    """Log a provenance action."""
    db.execute(
        "INSERT INTO knowledge_provenance_history (entry_id, action, user, timestamp) "
        "VALUES (?, ?, ?, ?)",
        (entry_id, action, user, now()),
    )
    db.commit()


def list_provenance(db: sqlite3.Connection, entry_id: int) -> list[KnowledgeProvenanceHistory]:
    """List all provenance actions for an entry."""
    rows = db.execute(
        "SELECT * FROM knowledge_provenance_history WHERE entry_id = ? ORDER BY timestamp",
        (entry_id,),
    ).fetchall()
    return [KnowledgeProvenanceHistory(**dict(r)) for r in rows]
