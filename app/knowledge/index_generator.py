"""Compact JSON topic index files (task 50.5): a lazy-loadable summary of the
wiki topic tree, one file per (language, library, version) group, so an
agent that reads files rather than querying the database (the same
motivation as app/prompts/skill_sync.py mirroring skills to disk) can list
what exists without loading the whole wiki. Written under the artifact root
(`app/runs/artifacts.artifact_root`), not committed to the repo -- unlike
`skill_sync`'s built-in skills, wiki entries are runtime, project-specific
data that grows unbounded.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

from app.runs.artifacts import _resolve_within

INDEX_SUBDIR = os.path.join("knowledge", "index")
_UNSAFE = re.compile(r"[^a-z0-9]+")


def _slug_part(value: str) -> str:
    cleaned = _UNSAFE.sub("_", (value or "").lower()).strip("_")
    return cleaned or "unclassified"


def index_filename(language: str, library: str, version: str) -> str:
    return f"topic_tree_{_slug_part(language)}_{_slug_part(library)}_{_slug_part(version)}.json"


def generate(db: sqlite3.Connection, artifact_root: str) -> list[str]:
    """Write one compact index file per (language, library, version) group
    of reviewed, slugged entries. Returns the written file paths."""
    output_dir = _resolve_within(artifact_root, INDEX_SUBDIR)
    os.makedirs(output_dir, exist_ok=True)
    rows = db.execute(
        "SELECT language, library, version, slug, title, topic, updated_at "
        "FROM knowledge_entries WHERE confidence = 'reviewed' AND slug IS NOT NULL "
        "ORDER BY language, library, version, title"
    ).fetchall()

    groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        key = (row["language"], row["library"], row["version"])
        groups.setdefault(key, []).append({
            "slug": row["slug"], "title": row["title"], "topic": row["topic"], "updated_at": row["updated_at"],
        })

    written = []
    for (language, library, version), entries in groups.items():
        path = os.path.join(output_dir, index_filename(language, library, version))
        payload = {
            "language": language,
            "library": library,
            "version": version,
            "entry_count": len(entries),
            "last_updated": max((e["updated_at"] for e in entries), default=None),
            "entries": entries,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        written.append(path)
    return written
