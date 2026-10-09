"""Where a project's wiki pages come from, and page lookup
(docs/WIKI_INTEGRATION_AND_PRESENTATION.md §5, §9 Option B).

With `WIKI_STORAGE=repo` (the only mode implemented) a project's wiki is:

- each repository's docs folder(s) (`WIKI_DOCS_DIRS`, default `docs/`), and
- each *local* folder-backed wiki attached to the project or shared
  (`wiki_sources`, §14). Remote wiki folders are read through their own
  instance and stay on `/wiki/sources/<id>`.

Phase 1 is read-only: there is no create/update/delete here yet. Pages are
edited in the repository (or by agents through the `wiki_*` MCP tools), and
the scanner picks the change up on the next request.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone

from app.knowledge import wiki_sources
from app.projects import models as project_models
from app.security import validate_repository_path
from app.settings.wiki_config import WikiConfig
from app.wikis.models import ROOT_FOLDER, ROOT_REPO, WikiPage, WikiPageMeta, WikiRoot
from app.wikis.scanner import SCANNER

_ROOT_KEY = re.compile(r"^(repo|folder)-(\d+)$")


class WikiNotFound(LookupError):
    """No such wiki root or page in this project."""


def _within_allowed(path: str, allowed_roots: tuple[str, ...]) -> bool:
    try:
        validate_repository_path(path, allowed_roots)
        return True
    except ValueError:
        return False


def list_roots(db: sqlite3.Connection, project_id: int, config: WikiConfig,
               allowed_roots: tuple[str, ...]) -> list[WikiRoot]:
    """The project's wiki roots that exist on this host, repositories first."""
    project = project_models.get_project(db, project_id)
    if project is None:
        return []
    roots: list[WikiRoot] = []
    repos = sorted(project.repositories, key=lambda r: (not r.is_primary, r.name.lower()))
    for repo in repos:
        for docs_dir in config.docs_dirs:
            path = os.path.join(repo.path, docs_dir)
            if os.path.isdir(path) and _within_allowed(path, allowed_roots):
                label = repo.name if len(config.docs_dirs) == 1 else f"{repo.name}/{docs_dir}"
                roots.append(WikiRoot(
                    key=f"repo-{repo.id}", kind=ROOT_REPO, label=label, path=path,
                    project_id=project_id, ref_id=repo.id, repo_path=repo.path,
                ))
                break  # first existing docs dir per repository
    for source in wiki_sources.list_for_project(db, project_id):
        if source.location != "local" or not os.path.isdir(source.path):
            continue
        if not _within_allowed(source.path, allowed_roots):
            continue
        roots.append(WikiRoot(
            key=f"folder-{source.id}", kind=ROOT_FOLDER, label=source.name, path=source.path,
            project_id=project_id, ref_id=source.id,
        ))
    return roots


def get_root(roots: list[WikiRoot], key: str) -> WikiRoot:
    if not _ROOT_KEY.match(key or ""):
        raise WikiNotFound(f"No wiki called {key!r}.")
    for root in roots:
        if root.key == key:
            return root
    raise WikiNotFound(f"No wiki called {key!r} in this project.")


def list_by_project(roots: list[WikiRoot], force: bool = False) -> dict[str, dict[str, WikiPageMeta]]:
    """{root key: {page path: metadata}} for every root."""
    return {root.key: SCANNER.scan_root(root, force=force) for root in roots}


def list_by_sprint(db: sqlite3.Connection, sprint_id: int) -> list[WikiPageMeta]:
    """Sprint wikis are Phase 3 (§6); none exist yet."""
    return []


def get_meta(root: WikiRoot, path: str) -> WikiPageMeta:
    meta = SCANNER.scan_root(root).get(path)
    if meta is None:
        raise WikiNotFound(f"Page {path!r} not found.")
    return meta


def get_page(root: WikiRoot, path: str) -> WikiPage:
    """The page with its content, in the §5.1 shape (author fields are filled
    in by app/wikis/metadata.py, which needs git)."""
    meta = get_meta(root, path)
    content = SCANNER.text(root, path) if not meta.too_large else None
    stamp = datetime.fromtimestamp(meta.modified, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return WikiPage(
        id=f"{root.key}:{path}", project_id=root.project_id, sprint_id=None, title=meta.title,
        slug=path.rsplit(".", 1)[0], path=path, content=content or "", version=int(meta.modified),
        created_at=stamp, updated_at=stamp, created_by="", updated_by="", published=True,
        metadata={"type": meta.wiki_type, **meta.frontmatter}, meta=meta,
    )
