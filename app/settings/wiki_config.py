"""Project wiki browser configuration (docs/WIKI_INTEGRATION_AND_PRESENTATION.md §5.3).

Phase 1 is repo-backed and read-only (§9 Option B): pages are the markdown
files under each repository's docs folder(s) plus the project's local wiki
folders, scanned on demand. Each value can be set on the Flask config
(`WIKI_*`) or the environment (`AGENTFLOW_WIKI_*`); the Flask config wins.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

STORAGE_MODES = ("repo", "database", "hybrid")


@dataclass(frozen=True)
class WikiConfig:
    # Sync in-app edits back to the repository (Phase 2; read-only today).
    repo_sync: bool = True
    # Where wiki pages live. Only "repo" is implemented: "database"/"hybrid"
    # are reserved for the Phase 2 editor and currently behave like "repo".
    storage: str = "repo"
    # Seconds a scanned index may be reused before a full rescan. Changed
    # files are picked up sooner: every scan re-reads any file whose mtime or
    # size moved.
    search_index_interval: int = 3600
    # Folders (relative to each repository root) treated as its wiki.
    docs_dirs: tuple[str, ...] = ("docs",)
    # Pages larger than this are listed but not rendered or indexed.
    max_page_bytes: int = 2_000_000
    # Upper bound on pages scanned per wiki root.
    max_pages: int = 2000


def _setting(app_config, name: str, default):
    if app_config is not None and name in app_config:
        return app_config[name]
    return os.environ.get(f"AGENTFLOW_{name}", default)


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load(app_config=None) -> WikiConfig:
    """The effective wiki settings; an unknown storage mode falls back to "repo"."""
    defaults = WikiConfig()
    storage = str(_setting(app_config, "WIKI_STORAGE", defaults.storage)).lower()
    docs_dirs = _setting(app_config, "WIKI_DOCS_DIRS", None)
    if isinstance(docs_dirs, str):
        docs_dirs = tuple(d.strip().strip("/") for d in docs_dirs.split(",") if d.strip())
    return WikiConfig(
        repo_sync=_bool(_setting(app_config, "WIKI_REPO_SYNC", defaults.repo_sync)),
        storage=storage if storage in STORAGE_MODES else "repo",
        search_index_interval=int(_setting(app_config, "WIKI_SEARCH_INDEX_INTERVAL", defaults.search_index_interval)),
        docs_dirs=tuple(docs_dirs) if docs_dirs else defaults.docs_dirs,
        max_page_bytes=int(_setting(app_config, "WIKI_MAX_PAGE_BYTES", defaults.max_page_bytes)),
        max_pages=int(_setting(app_config, "WIKI_MAX_PAGES", defaults.max_pages)),
    )
