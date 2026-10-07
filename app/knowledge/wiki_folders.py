"""Folder-backed wiki operations (docs/WIKI_INTEGRATION_AND_PRESENTATION.md §14):
set up, list, search, read and write a folder of markdown pages on *this* host.

Used directly for a `local` wiki source and, on the far side, by the
federation API (app/federation/routes.py) for a `remote` one -- so the path is
always checked against the ALLOWED_PROJECT_ROOTS of the instance that owns the
folder, never the instance asking for it.
"""
from __future__ import annotations

import os
import tempfile

from app.runs import security
from app.security import validate_repository_path
from app.workspace import files

PAGE_EXTENSIONS = (".md", ".markdown")
MAX_PAGES = 2000
MAX_PAGE_BYTES = 1_000_000
SNIPPET_CHARS = 160

STARTER_PAGE = "index.md"
STARTER_CONTENT = """# {name}

This wiki is a folder of markdown files. Add pages next to this one (or in
sub-folders) and they appear in AgentFlow's wiki browser.
"""


class WikiFolderError(ValueError):
    """The folder can't be used as a wiki; the message is user-facing."""


def _validate(path: str, allowed_roots: tuple[str, ...]) -> str:
    if not (path or "").strip():
        raise WikiFolderError("A folder path is required.")
    try:
        return validate_repository_path(path.strip(), allowed_roots)
    except ValueError as exc:
        raise WikiFolderError(str(exc)) from exc


def setup_folder(path: str, allowed_roots: tuple[str, ...], create: bool = False, name: str = "Wiki") -> str:
    """Validate `path` as a wiki folder and return its resolved absolute path.

    With `create`, a missing folder is made and seeded with a starter
    `index.md`; an existing folder is never modified."""
    resolved = _validate(path, allowed_roots)
    if os.path.exists(resolved) and not os.path.isdir(resolved):
        raise WikiFolderError(f"{resolved} exists but is not a folder.")
    if not os.path.isdir(resolved):
        if not create:
            raise WikiFolderError(f"{resolved} does not exist (tick 'create it' to set it up).")
        os.makedirs(resolved)
        with open(os.path.join(resolved, STARTER_PAGE), "w", encoding="utf-8") as fh:
            fh.write(STARTER_CONTENT.format(name=name))
    return resolved


def _root(path: str, allowed_roots: tuple[str, ...]) -> str:
    root = _validate(path, allowed_roots)
    if not os.path.isdir(root):
        raise WikiFolderError(f"{path} is no longer a folder.")
    return root


def list_pages(path: str, allowed_roots: tuple[str, ...]) -> list[str]:
    """Relative paths of every markdown page under the wiki folder -- each
    folder's own pages (by name) before its sub-folders' -- skipping hidden
    files/folders. Capped at MAX_PAGES."""
    root = _root(path, allowed_roots)
    pages = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or not name.lower().endswith(PAGE_EXTENSIONS):
                continue
            pages.append(os.path.relpath(os.path.join(dirpath, name), root))
            if len(pages) >= MAX_PAGES:
                return pages
    return pages


def read_page(path: str, allowed_roots: tuple[str, ...], page: str) -> str:
    """Content of one page, which must be a markdown file inside the folder."""
    root = _root(path, allowed_roots)
    if not (page or "").lower().endswith(PAGE_EXTENSIONS):
        raise WikiFolderError("Only markdown pages can be opened.")
    try:
        content = files.read_file(root, page)
    except ValueError as exc:  # escape attempt or not a file
        raise WikiFolderError(f"Page {page!r} not found.") from exc
    if content.too_large:
        raise WikiFolderError(f"Page {page!r} is too large to display.")
    if content.is_binary:
        raise WikiFolderError(f"Page {page!r} is not a text file.")
    return content.content


def search_pages(path: str, allowed_roots: tuple[str, ...], query: str, limit: int = 20) -> list[dict]:
    """Case-insensitive search across the folder's pages. A page matches when
    every query term appears in its path or content; ranked by how often the
    terms occur (a path hit counts extra). Each hit carries a snippet around
    the first matching line."""
    terms = [t for t in (query or "").lower().split() if t]
    if not terms:
        return []
    root = _root(path, allowed_roots)
    hits = []
    for page in list_pages(path, allowed_roots):
        content = files.read_file(root, page)
        text = content.content or ""
        haystack = text.lower()
        name = page.lower()
        if not all(t in haystack or t in name for t in terms):
            continue
        score = sum(haystack.count(t) + 5 * name.count(t) for t in terms)
        snippet = next(
            (line.strip() for line in text.splitlines() if any(t in line.lower() for t in terms)), "",
        )
        hits.append({"page": page, "score": score, "snippet": snippet[:SNIPPET_CHARS]})
    hits.sort(key=lambda h: (-h["score"], h["page"]))
    return hits[:limit]


def write_page(path: str, allowed_roots: tuple[str, ...], page: str, content: str,
               redact_patterns: tuple[str, ...] = ()) -> str:
    """Create or replace one markdown page (sub-folders created as needed),
    redacting secrets first like every other store AgentFlow persists to.
    Returns the page's relative path."""
    root = _root(path, allowed_roots)
    page = (page or "").strip().lstrip("/")
    if not page.lower().endswith(PAGE_EXTENSIONS):
        raise WikiFolderError("Pages must be markdown files ending in .md.")
    if any(part.startswith(".") for part in page.split("/")):
        raise WikiFolderError("Page paths can't contain hidden files or folders.")
    clean, _ = security.redact(content or "", redact_patterns)
    if len(clean.encode()) > MAX_PAGE_BYTES:
        raise WikiFolderError(f"Page is larger than {MAX_PAGE_BYTES} bytes.")
    try:
        absolute = files.resolve_path(root, page)
    except ValueError as exc:
        raise WikiFolderError(f"Page {page!r} is outside the wiki folder.") from exc
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=os.path.dirname(absolute),
                                     delete=False, suffix=".tmp") as fh:
        fh.write(clean)
    os.replace(fh.name, absolute)
    return os.path.relpath(absolute, root)
