"""Project wiki browser data model (docs/WIKI_INTEGRATION_AND_PRESENTATION.md §5).

Phase 1 is repo-backed (`WIKI_STORAGE=repo`, §9 Option B): there is no wiki
table -- a page *is* a markdown file under a wiki root, and these dataclasses
describe what the scanner (app/wikis/scanner.py) reads from disk. `WikiPage`
keeps the §5.1 field names so a later database/hybrid store can fill the same
shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

ROOT_REPO = "repo"      # a repository's docs folder
ROOT_FOLDER = "folder"  # a local folder-backed wiki (wiki_sources, §14)

# Page kinds recognised by path convention or `type:` frontmatter (§4.5).
PAGE_TYPES = ("page", "adr", "decision", "architecture", "api", "guide")
DECISION_TYPES = ("adr", "decision")


@dataclass(frozen=True)
class WikiRoot:
    """One browsable folder of markdown pages belonging to a project."""

    key: str          # URL segment: "repo-<id>" or "folder-<id>"
    kind: str         # ROOT_REPO | ROOT_FOLDER
    label: str        # shown in the TOC and breadcrumb
    path: str         # absolute folder path on this host
    project_id: int
    ref_id: int       # repository id or wiki_sources id
    repo_path: str = ""  # repository root (for git metadata), "" for folders


@dataclass
class WikiPageMeta:
    """Scanner output for one page: everything but the rendered body."""

    root_key: str
    path: str                 # relative to the root, "/"-separated
    title: str
    size: int
    modified: float           # epoch seconds
    wiki_type: str = "page"
    frontmatter: dict[str, Any] = field(default_factory=dict)
    links: tuple[str, ...] = ()      # other pages in the same root this one links to
    headings: tuple[tuple[int, str], ...] = ()
    too_large: bool = False

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def directory(self) -> str:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""

    @property
    def modified_at(self) -> datetime:
        return datetime.fromtimestamp(self.modified, tz=timezone.utc)

    @property
    def modified_display(self) -> str:
        return self.modified_at.strftime("%Y-%m-%d %H:%M UTC")

    @property
    def is_decision(self) -> bool:
        return self.wiki_type in DECISION_TYPES


@dataclass
class WikiPage:
    """A page with its content, in the §5.1 WikiPage shape."""

    id: str                   # "<root key>:<path>"
    project_id: int
    sprint_id: int | None
    title: str
    slug: str
    path: str
    content: str
    version: int              # repo-backed: the file's mtime (changes on every save)
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str
    published: bool
    metadata: dict[str, Any]
    meta: WikiPageMeta | None = None
