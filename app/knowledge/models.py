"""Knowledge base entry models and persistence (task 48), extended with a
wiki layer (task 50): stable slugs, [[slug]] backlinks, read logging and a
review queue on the same `knowledge_entries` rows -- see app/db.py's SCHEMA
comment for why the wiki is an index over this table rather than a second
store."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from app.runs.models import now

# Alphanumeric + hyphen, no leading/trailing/doubled hyphen -- also what
# blocks path-traversal payloads like `../../../etc/passwd` from ever
# reaching `KnowledgeStore._safe_path`-style disk paths built from a slug.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[\[([a-z0-9]+(?:-[a-z0-9]+)*)\]\]")


class InvalidSlugError(ValueError):
    pass


def validate_slug(slug: str) -> str:
    if not SLUG_RE.match(slug):
        raise InvalidSlugError(
            f"Invalid slug {slug!r}: use lowercase letters, digits and single hyphens only"
        )
    return slug


def extract_links(content: str) -> list[str]:
    """Slugs referenced via `[[slug]]` in `content`, de-duplicated, in order."""
    seen: dict[str, None] = {}
    for m in LINK_RE.finditer(content or ""):
        seen.setdefault(m.group(1), None)
    return list(seen)


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
    slug: str | None = None
    aliases: list[str] = field(default_factory=list)
    topic: str = ""
    language: str = ""
    library: str = ""
    version: str = ""
    freshness_days: int = 30
    last_read_at: str | None = None
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
    slug: str | None = None,
    aliases: list[str] | None = None,
    topic: str = "",
    language: str = "",
    library: str = "",
    version: str = "",
    freshness_days: int = 30,
) -> int:
    """Create a new knowledge entry. Returns entry ID.

    `slug` is optional -- a plain note/web_cache entry from RESEARCH ingestion
    (task 50.5) never gets one -- but when given must pass `validate_slug` and
    be unique (`idx_knowledge_slug`, app/db.py), surfaced here as `ValueError`
    rather than a raw `sqlite3.IntegrityError`.
    """
    if kind not in ("web_cache", "note"):
        raise ValueError(f"Invalid kind: {kind}")
    if confidence not in ("unverified", "reviewed"):
        raise ValueError(f"Invalid confidence: {confidence}")
    if slug is not None:
        validate_slug(slug)
    try:
        cur = db.execute(
            "INSERT INTO knowledge_entries "
            "(kind, title, content, url, source_project_id, source_session_id, source_step_id, "
            "confidence, scope, pinned, use_count, created_at, updated_at, "
            "slug, aliases, topic, language, library, version, freshness_days) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, title.strip(), content, url, source_project_id, source_session_id, source_step_id,
             confidence, scope, now(), now(),
             slug, json.dumps(aliases or []), topic, language, library, version, freshness_days),
        )
    except sqlite3.IntegrityError as exc:
        if slug is not None:
            raise ValueError(f"slug {slug!r} is already in use") from exc
        raise
    entry_id = cur.lastrowid
    if tags:
        for tag in set(tags):
            db.execute("INSERT INTO knowledge_tags (entry_id, tag) VALUES (?, ?)", (entry_id, tag))
    sync_backlinks(db, entry_id, content)
    log_provenance(db, entry_id, "created", "system")
    db.commit()
    return entry_id


def _row_to_entry(db: sqlite3.Connection, row: sqlite3.Row) -> KnowledgeEntry:
    d = dict(row)
    d["aliases"] = json.loads(d["aliases"]) if d.get("aliases") else []
    d["tags"] = [t["tag"] for t in db.execute(
        "SELECT tag FROM knowledge_tags WHERE entry_id = ?", (row["id"],)
    ).fetchall()]
    return KnowledgeEntry(**d)


def get_entry(db: sqlite3.Connection, entry_id: int) -> KnowledgeEntry | None:
    """Fetch a knowledge entry by ID."""
    row = db.execute("SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
    return _row_to_entry(db, row) if row is not None else None


def get_entry_by_slug(db: sqlite3.Connection, slug: str) -> KnowledgeEntry | None:
    """Fetch a knowledge entry by its wiki slug (or alias)."""
    row = db.execute("SELECT * FROM knowledge_entries WHERE slug = ?", (slug,)).fetchone()
    if row is not None:
        return _row_to_entry(db, row)
    for row in db.execute("SELECT * FROM knowledge_entries WHERE aliases != '[]'"):
        if slug in json.loads(row["aliases"] or "[]"):
            return _row_to_entry(db, row)
    return None


def list_entries(
    db: sqlite3.Connection,
    kind: str | None = None,
    confidence: str | None = None,
    scope: str | None = None,
    tags: list[str] | None = None,
    language: str | None = None,
    library: str | None = None,
    version: str | None = None,
    topic: str | None = None,
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
    if language:
        query += " AND language = ?"
        params.append(language)
    if library:
        query += " AND library = ?"
        params.append(library)
    if version:
        query += " AND version = ?"
        params.append(version)
    if topic:
        query += " AND topic = ?"
        params.append(topic)
    if tags:
        placeholders = ",".join("?" * len(tags))
        query += f" AND id IN (SELECT entry_id FROM knowledge_tags WHERE tag IN ({placeholders}))"
        params.extend(tags)
    query += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(query, params).fetchall()
    return [_row_to_entry(db, row) for row in rows]


def update_entry(db: sqlite3.Connection, entry_id: int, **fields) -> None:
    """Update an entry. Valid fields: title, content, confidence, tags, slug,
    aliases, topic, language, library, version, freshness_days."""
    allowed = {
        "title", "content", "confidence", "tags", "slug", "aliases",
        "topic", "language", "library", "version", "freshness_days",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    tags = fields.pop("tags", None)
    if "slug" in fields and fields["slug"] is not None:
        validate_slug(fields["slug"])
    if "aliases" in fields:
        fields["aliases"] = json.dumps(fields["aliases"] or [])
    fields["updated_at"] = now()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    try:
        db.execute(f"UPDATE knowledge_entries SET {assignments} WHERE id = ?", [*fields.values(), entry_id])
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"slug {fields.get('slug')!r} is already in use") from exc
    if tags is not None:
        db.execute("DELETE FROM knowledge_tags WHERE entry_id = ?", (entry_id,))
        for tag in set(tags):
            db.execute("INSERT INTO knowledge_tags (entry_id, tag) VALUES (?, ?)", (entry_id, tag))
    if "content" in fields:
        sync_backlinks(db, entry_id, fields["content"])
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


# -- Wiki backlinks (task 50) -------------------------------------------------

def sync_backlinks(db: sqlite3.Connection, entry_id: int, content: str) -> None:
    """Replace `entry_id`'s outgoing `[[slug]]` links with what `content`
    references now. Targets don't have to exist yet (a link can point at a
    slug not written yet, like a wiki), so this stores target *slugs*, not
    target entry ids."""
    db.execute("DELETE FROM knowledge_backlinks WHERE source_entry_id = ?", (entry_id,))
    for slug in extract_links(content):
        db.execute(
            "INSERT OR IGNORE INTO knowledge_backlinks (source_entry_id, target_slug, created_at) "
            "VALUES (?, ?, ?)",
            (entry_id, slug, now()),
        )


@dataclass
class Backlink:
    source_entry_id: int
    source_title: str
    source_slug: str | None


def list_backlinks(db: sqlite3.Connection, slug: str) -> list[Backlink]:
    """Entries that link to `slug` via `[[slug]]`."""
    rows = db.execute(
        "SELECT e.id AS source_entry_id, e.title AS source_title, e.slug AS source_slug "
        "FROM knowledge_backlinks b JOIN knowledge_entries e ON e.id = b.source_entry_id "
        "WHERE b.target_slug = ? ORDER BY e.title",
        (slug,),
    ).fetchall()
    return [Backlink(**dict(r)) for r in rows]


def list_forward_links(db: sqlite3.Connection, entry_id: int) -> list[str]:
    """Slugs `entry_id` links to (whether or not each target exists yet)."""
    rows = db.execute(
        "SELECT target_slug FROM knowledge_backlinks WHERE source_entry_id = ? ORDER BY target_slug",
        (entry_id,),
    ).fetchall()
    return [r["target_slug"] for r in rows]


# -- Read logging (task 50) ---------------------------------------------------

def log_read(db: sqlite3.Connection, entry_id: int, context: str = "") -> None:
    """Record a read of `entry_id`: appends to `knowledge_read_log`, bumps
    `use_count` and `last_read_at` (WikiReadLog + use_count rollup)."""
    db.execute(
        "INSERT INTO knowledge_read_log (entry_id, accessed_at, context) VALUES (?, ?, ?)",
        (entry_id, now(), context),
    )
    db.execute(
        "UPDATE knowledge_entries SET use_count = use_count + 1, last_read_at = ? WHERE id = ?",
        (now(), entry_id),
    )
    db.commit()


def list_topics(db: sqlite3.Connection) -> dict:
    """Hierarchical topic tree: {language: {library: {version: [entries]}}}.
    Entries with an empty language/library/version group under `""` (an
    "unclassified" bucket at that level) rather than being dropped."""
    tree: dict = {}
    for entry in list_entries(db, confidence="reviewed", limit=10_000):
        if entry.slug is None:
            continue
        lang = tree.setdefault(entry.language, {})
        lib = lang.setdefault(entry.library, {})
        versions = lib.setdefault(entry.version, [])
        versions.append({"slug": entry.slug, "title": entry.title, "topic": entry.topic})
    return tree


# -- Review queue (task 50) ---------------------------------------------------

@dataclass
class ReviewItem:
    id: int
    entry_id: int | None
    slug: str | None
    change_type: str
    title: str | None
    content: str | None
    reason: str
    status: str
    created_at: str
    reviewed_at: str | None
    reviewed_by: str | None


def propose_change(
    db: sqlite3.Connection,
    change_type: str,
    slug: str | None = None,
    entry_id: int | None = None,
    title: str | None = None,
    content: str | None = None,
    reason: str = "",
) -> int:
    """Queue a proposed create/update/delete for human review."""
    if change_type not in ("create", "update", "delete"):
        raise ValueError(f"Invalid change_type: {change_type}")
    if slug is not None:
        validate_slug(slug)
    cur = db.execute(
        "INSERT INTO knowledge_review_queue "
        "(entry_id, slug, change_type, title, content, reason, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (entry_id, slug, change_type, title, content, reason, now()),
    )
    db.commit()
    return cur.lastrowid


def list_review_queue(db: sqlite3.Connection, status: str = "pending") -> list[ReviewItem]:
    rows = db.execute(
        "SELECT * FROM knowledge_review_queue WHERE status = ? ORDER BY created_at", (status,)
    ).fetchall()
    return [ReviewItem(**dict(r)) for r in rows]


def get_review_item(db: sqlite3.Connection, item_id: int) -> ReviewItem | None:
    row = db.execute("SELECT * FROM knowledge_review_queue WHERE id = ?", (item_id,)).fetchone()
    return ReviewItem(**dict(row)) if row is not None else None


def resolve_review_item(db: sqlite3.Connection, item_id: int, status: str, reviewer: str) -> None:
    if status not in ("approved", "rejected"):
        raise ValueError(f"Invalid status: {status}")
    db.execute(
        "UPDATE knowledge_review_queue SET status = ?, reviewed_at = ?, reviewed_by = ? WHERE id = ?",
        (status, now(), reviewer, item_id),
    )
    db.commit()
