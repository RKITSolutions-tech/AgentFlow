"""Registered folder-backed wikis (`wiki_sources`) and the dispatch between a
`local` folder (app/knowledge/wiki_folders.py, called directly) and a
`remote` one (the same functions, run by the remote instance behind its
federation API -- app/instances/client.py).

These wikis are a knowledge store for agent discussions: a session reaches
them through the `wiki_*` MCP tools (app/mcp/tools/wiki.py) and, if asked,
gets `session_context()` in its starting prompt. A source belongs to one
project, or to every project when `project_id` is NULL.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.instances import client
from app.instances import models as instance_models
from app.instances.client import RemoteInstanceError
from app.knowledge import wiki_folders
from app.knowledge.wiki_folders import WikiFolderError

CONTEXT_PAGE_LIMIT = 50
CONTEXT_INDEX_CHARS = 4000


@dataclass
class WikiSource:
    id: int
    name: str
    location: str
    path: str
    instance_id: int | None
    instance_name: str | None
    project_id: int | None
    project_name: str | None
    created_at: str

    @property
    def where(self) -> str:
        if self.location == "local":
            return f"folder {self.path} on this machine"
        return f"remote instance {self.instance_name}"


_SELECT = (
    "SELECT w.*, i.name AS instance_name, p.name AS project_name FROM wiki_sources w "
    "LEFT JOIN remote_instances i ON i.id = w.instance_id "
    "LEFT JOIN projects p ON p.id = w.project_id"
)


def _hydrate(row: sqlite3.Row) -> WikiSource:
    return WikiSource(
        id=row["id"], name=row["name"], location=row["location"], path=row["path"],
        instance_id=row["instance_id"], instance_name=row["instance_name"],
        project_id=row["project_id"], project_name=row["project_name"], created_at=row["created_at"],
    )


def list_sources(db: sqlite3.Connection) -> list[WikiSource]:
    return [_hydrate(r) for r in db.execute(f"{_SELECT} ORDER BY w.name").fetchall()]


def list_for_project(db: sqlite3.Connection, project_id: int) -> list[WikiSource]:
    """The wikis a session in `project_id` may use: its own plus shared ones."""
    rows = db.execute(
        f"{_SELECT} WHERE w.project_id = ? OR w.project_id IS NULL ORDER BY w.name", (project_id,),
    ).fetchall()
    return [_hydrate(r) for r in rows]


def get_source(db: sqlite3.Connection, source_id: int) -> WikiSource | None:
    row = db.execute(f"{_SELECT} WHERE w.id = ?", (source_id,)).fetchone()
    return _hydrate(row) if row else None


def delete_source(db: sqlite3.Connection, source_id: int) -> None:
    """Unregister the wiki. The folder and its pages are left untouched."""
    db.execute("DELETE FROM wiki_sources WHERE id = ?", (source_id,))
    db.commit()


def _instance(db: sqlite3.Connection, instance_id: int | None):
    instance = instance_models.get_instance(db, instance_id) if instance_id else None
    if instance is None:
        raise WikiFolderError("Choose a registered remote instance.")
    return instance


def add_source(
    db: sqlite3.Connection,
    allowed_roots: tuple[str, ...],
    name: str,
    location: str,
    path: str,
    instance_id: int | None = None,
    create: bool = False,
    project_id: int | None = None,
) -> int:
    """Set the folder up (validate, optionally create) where it lives, then
    register it under the path that machine resolved. Nothing is stored if
    setup fails. Raises WikiFolderError with a user-facing message."""
    name = (name or "").strip()
    if not name:
        raise WikiFolderError("A name is required.")
    if project_id is not None and db.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
        raise WikiFolderError("That project doesn't exist.")
    if location == "local":
        resolved = wiki_folders.setup_folder(path, allowed_roots, create=create, name=name)
        instance_id = None
    elif location == "remote":
        instance = _instance(db, instance_id)
        try:
            resolved = client.wiki_setup(instance, path, create=create, name=name)
        except RemoteInstanceError as exc:
            raise WikiFolderError(str(exc)) from exc
    else:
        raise WikiFolderError("Location must be 'local' or 'remote'.")
    cur = db.execute(
        "INSERT INTO wiki_sources (name, location, path, instance_id, project_id) VALUES (?, ?, ?, ?, ?)",
        (name, location, resolved, instance_id, project_id),
    )
    db.commit()
    return cur.lastrowid


def _remote(db: sqlite3.Connection, source: WikiSource, call, *args):
    try:
        return call(_instance(db, source.instance_id), source.path, *args)
    except RemoteInstanceError as exc:
        raise WikiFolderError(str(exc)) from exc


def list_pages(db: sqlite3.Connection, allowed_roots: tuple[str, ...], source: WikiSource) -> list[str]:
    if source.location == "local":
        return wiki_folders.list_pages(source.path, allowed_roots)
    return _remote(db, source, client.wiki_pages)


def read_page(db: sqlite3.Connection, allowed_roots: tuple[str, ...], source: WikiSource, page: str) -> str:
    if source.location == "local":
        return wiki_folders.read_page(source.path, allowed_roots, page)
    return _remote(db, source, client.wiki_page, page)


def search(db: sqlite3.Connection, allowed_roots: tuple[str, ...], source: WikiSource,
           query: str, limit: int = 20) -> list[dict]:
    if source.location == "local":
        return wiki_folders.search_pages(source.path, allowed_roots, query, limit=limit)
    return _remote(db, source, client.wiki_search, query, limit)


def write_page(db: sqlite3.Connection, allowed_roots: tuple[str, ...], source: WikiSource, page: str,
               content: str, redact_patterns: tuple[str, ...] = ()) -> str:
    """Create or replace a page. A remote wiki applies the remote instance's
    own redaction patterns, as it does for everything else it stores."""
    if source.location == "local":
        return wiki_folders.write_page(source.path, allowed_roots, page, content, redact_patterns)
    return _remote(db, source, client.wiki_write, page, content)


def session_context(db: sqlite3.Connection, allowed_roots: tuple[str, ...], project_id: int) -> str:
    """Starting-prompt context for a session in `project_id`: each wiki it can
    use, its page list and the top of its index.md. Empty when the project has
    no wikis. An unreachable wiki is listed as such rather than failing the
    session start."""
    sources = list_for_project(db, project_id)
    if not sources:
        return ""
    blocks = []
    for source in sources:
        header = f"## Wiki {source.id}: {source.name} ({source.where})"
        try:
            pages = list_pages(db, allowed_roots, source)
        except WikiFolderError as exc:
            blocks.append(f"{header}\nUnavailable right now: {exc}")
            continue
        shown = pages[:CONTEXT_PAGE_LIMIT]
        lines = [header, "Pages: " + (", ".join(shown) or "(none yet)")
                 + (f" ... and {len(pages) - len(shown)} more" if len(pages) > len(shown) else "")]
        if wiki_folders.STARTER_PAGE in pages:
            try:
                index = read_page(db, allowed_roots, source, wiki_folders.STARTER_PAGE)
            except WikiFolderError:
                index = ""
            if index.strip():
                truncated = index[:CONTEXT_INDEX_CHARS]
                lines.append(f"index.md:\n{truncated}" + ("\n[...truncated]" if len(index) > len(truncated) else ""))
        blocks.append("\n".join(lines))
    return (
        "[System Context]\nThis project keeps its knowledge in the wiki(s) below. Check them before "
        "answering questions they may cover, and when this discussion settles a decision or "
        "uncovers something worth keeping, record it in a wiki page. With AgentFlow tools enabled "
        "use wiki_search / wiki_read / wiki_write (by wiki id); otherwise read a local wiki's files "
        "directly and say what should be recorded.\n\n"
        + "\n\n".join(blocks)
    )
