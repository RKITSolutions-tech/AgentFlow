"""Wiki MCP tools: let an agent use the project's wiki folders
(app/knowledge/wiki_sources.py) as a knowledge store during a discussion --
look things up, and record decisions/findings as markdown pages.

Like `backlog.py`, these add the project-ownership check the persistence layer
doesn't do itself: a session only reaches wikis registered for its own project
or shared with every project. Paths are re-checked against ALLOWED_PROJECT_ROOTS
(local) or by the remote instance (remote), and writes are redacted.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.knowledge import wiki_sources
from app.knowledge.wiki_sources import WikiSource


def _owned_source(db: sqlite3.Connection, project_id: int, wiki_id: int) -> WikiSource:
    source = wiki_sources.get_source(db, wiki_id)
    if source is None or source.project_id not in (None, project_id):
        raise LookupError(f"Wiki {wiki_id} not found for project {project_id}")
    return source


def list_wikis(db: sqlite3.Connection, project_id: int, allowed_roots: tuple[str, ...]) -> list[dict[str, Any]]:
    result = []
    for source in wiki_sources.list_for_project(db, project_id):
        entry = {"id": source.id, "name": source.name, "location": source.where,
                 "shared": source.project_id is None}
        try:
            entry["pages"] = wiki_sources.list_pages(db, allowed_roots, source)
        except ValueError as exc:  # WikiFolderError: folder gone / remote unreachable
            entry["error"] = str(exc)
        result.append(entry)
    return result


def search(db: sqlite3.Connection, project_id: int, allowed_roots: tuple[str, ...], query: str,
           wiki_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Search one wiki, or every wiki the project can use. An unreachable
    wiki is reported in the results rather than failing the whole search."""
    sources = ([_owned_source(db, project_id, wiki_id)] if wiki_id is not None
               else wiki_sources.list_for_project(db, project_id))
    hits = []
    for source in sources:
        try:
            for hit in wiki_sources.search(db, allowed_roots, source, query, limit=limit):
                hits.append({"wiki_id": source.id, "wiki": source.name, **hit})
        except ValueError as exc:
            hits.append({"wiki_id": source.id, "wiki": source.name, "error": str(exc)})
    hits.sort(key=lambda h: -h.get("score", 0))
    return hits[:limit]


def read(db: sqlite3.Connection, project_id: int, allowed_roots: tuple[str, ...],
         wiki_id: int, page: str) -> dict[str, Any]:
    source = _owned_source(db, project_id, wiki_id)
    return {"wiki_id": wiki_id, "page": page, "content": wiki_sources.read_page(db, allowed_roots, source, page)}


def write(db: sqlite3.Connection, project_id: int, allowed_roots: tuple[str, ...], wiki_id: int,
          page: str, content: str, redact_patterns: tuple[str, ...] = ()) -> dict[str, Any]:
    source = _owned_source(db, project_id, wiki_id)
    written = wiki_sources.write_page(db, allowed_roots, source, page, content, redact_patterns)
    return {"wiki_id": wiki_id, "page": written, "status": "saved"}
