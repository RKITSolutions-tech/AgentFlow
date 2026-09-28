"""Wiki maintenance report: stale/unused/duplicate entries and missing
cross-links (task 50.5).

No embedding infra exists anywhere in this codebase (adapters shell out to
CLIs, not provider APIs -- see app/agents/research_agent.py's note on cost
estimation for the same constraint), so "potential duplicates (cosine
similarity > 0.85 on content embeddings)" is approximated with
`difflib.SequenceMatcher` over raw content instead -- documented here rather
than silently swapped, since the task's own threshold (0.85) assumes cosine
similarity's scale, which `SequenceMatcher.ratio()` only loosely resembles.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import dataclass, field

from app.knowledge import models

DUPLICATE_RATIO_THRESHOLD = 0.85


@dataclass
class MaintenanceReport:
    stale: list[models.KnowledgeEntry] = field(default_factory=list)
    unused: list[models.KnowledgeEntry] = field(default_factory=list)
    duplicates: list[tuple[models.KnowledgeEntry, models.KnowledgeEntry, float]] = field(default_factory=list)
    missing_backlinks: list[tuple[models.KnowledgeEntry, str]] = field(default_factory=list)


def stale_entries(db: sqlite3.Connection) -> list[models.KnowledgeEntry]:
    """Reviewed entries not read within their own `freshness_days` (never
    read at all counts as stale from creation)."""
    out = []
    for entry in models.list_entries(db, confidence="reviewed", limit=10_000):
        reference = entry.last_read_at or entry.created_at
        row = db.execute(
            "SELECT julianday('now') - julianday(?) AS age", (reference,)
        ).fetchone()
        if row["age"] is not None and row["age"] > entry.freshness_days:
            out.append(entry)
    return out


def unused_entries(db: sqlite3.Connection) -> list[models.KnowledgeEntry]:
    return [e for e in models.list_entries(db, confidence="reviewed", limit=10_000) if e.use_count == 0]


def potential_duplicates(
    db: sqlite3.Connection, threshold: float = DUPLICATE_RATIO_THRESHOLD,
) -> list[tuple[models.KnowledgeEntry, models.KnowledgeEntry, float]]:
    entries = models.list_entries(db, confidence="reviewed", limit=1000)
    pairs = []
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            ratio = difflib.SequenceMatcher(None, a.content, b.content).ratio()
            if ratio > threshold:
                pairs.append((a, b, ratio))
    return pairs


def missing_backlinks(db: sqlite3.Connection) -> list[tuple[models.KnowledgeEntry, str]]:
    """Entries whose content mentions another entry's title in plain text
    without a `[[slug]]` link to it -- a suggestion, not an error."""
    entries = [e for e in models.list_entries(db, confidence="reviewed", limit=1000) if e.slug]
    suggestions = []
    for entry in entries:
        linked = set(models.list_forward_links(db, entry.id))
        for other in entries:
            if other.id == entry.id or other.slug in linked:
                continue
            if re.search(rf"\b{re.escape(other.title)}\b", entry.content, re.IGNORECASE):
                suggestions.append((entry, other.slug))
    return suggestions


def generate_report(db: sqlite3.Connection) -> MaintenanceReport:
    return MaintenanceReport(
        stale=stale_entries(db),
        unused=unused_entries(db),
        duplicates=potential_duplicates(db),
        missing_backlinks=missing_backlinks(db),
    )
