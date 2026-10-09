"""Document information for the wiki browser's metadata sidebar and navigation
(docs/WIKI_INTEGRATION_AND_PRESENTATION.md §4.1.3, §4.4).

- `get_file_author`: last commit touching the page (git, through the same
  `app.workspace.git` helper the Files/History tabs use); empty outside git.
- `get_related_tasks`: Task Master tasks in the page's repository that mention
  the page's file name.
- `get_backlinks` / `related_pages`: pages linking to / linked from this one
  (parsed markdown links, plus a plain mention of the file name for backlinks).
- `breadcrumbs`: Project > wiki root > folders > page.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass

from app.wikis.models import WikiPageMeta, WikiRoot
from app.wikis.scanner import SCANNER

RELATED_TASK_LIMIT = 8
_TASKS_FILE = os.path.join(".taskmaster", "tasks", "tasks.json")


@dataclass
class Author:
    name: str
    date: str
    commit: str


_author_cache: dict[tuple, Author | None] = {}
_tasks_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def get_file_author(root: WikiRoot, meta: WikiPageMeta, provider_factory=None,
                    allowed_roots: tuple[str, ...] = ()) -> Author | None:
    """Who last changed the page, per `git log -1`. Cached per file version;
    None for folder wikis, non-git repositories or untracked files."""
    if not root.repo_path:
        return None
    key = (root.path, meta.path, meta.modified, meta.size)
    with _lock:
        if key in _author_cache:
            return _author_cache[key]
    author = None
    if provider_factory is not None:
        from app.workspace import git

        relative = os.path.relpath(os.path.join(root.path, meta.path), root.repo_path)
        try:
            output = git._run_git(
                provider_factory(), root.repo_path,
                ["log", "-1", "--format=%an%x1f%cI%x1f%h", "--", relative], allowed_roots,
            )
        except Exception:  # noqa: BLE001 -- not a git repo, git missing, etc.
            output = ""
        parts = output.strip().split("\x1f")
        if len(parts) == 3 and parts[0]:
            author = Author(name=parts[0], date=parts[1][:16].replace("T", " "), commit=parts[2])
    with _lock:
        _author_cache[key] = author
    return author


def _load_tasks(repo_path: str) -> list[dict]:
    path = os.path.join(repo_path, _TASKS_FILE)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    with _lock:
        cached = _tasks_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    tasks = []
    for tag in data.values() if isinstance(data, dict) else []:
        for task in (tag.get("tasks", []) if isinstance(tag, dict) else []):
            text = " ".join(str(task.get(f, "")) for f in ("title", "description", "details", "testStrategy"))
            for sub in task.get("subtasks", []) or []:
                text += " " + " ".join(str(sub.get(f, "")) for f in ("title", "description", "details"))
            tasks.append({
                "id": task.get("id"), "title": task.get("title", ""), "status": task.get("status", ""),
                "text": text,
            })
    with _lock:
        _tasks_cache[path] = (mtime, tasks)
    return tasks


def get_related_tasks(root: WikiRoot, meta: WikiPageMeta) -> list[dict]:
    """Task Master tasks (id, title, status) whose text mentions this page's
    file name -- e.g. a task citing docs/SESSION_TOPICS.md."""
    if not root.repo_path:
        return []
    needle = meta.name
    if len(needle) < 5:
        return []
    related = [
        {"id": t["id"], "title": t["title"], "status": t["status"]}
        for t in _load_tasks(root.repo_path) if needle in t["text"]
    ]
    return related[:RELATED_TASK_LIMIT]


def get_backlinks(root: WikiRoot, pages: dict[str, WikiPageMeta], path: str) -> list[WikiPageMeta]:
    """Pages in the same root that link to `path`, or mention its file name."""
    name = path.rsplit("/", 1)[-1]
    backlinks = []
    for other_path, other in pages.items():
        if other_path == path:
            continue
        if path in other.links:
            backlinks.append(other)
            continue
        text = SCANNER.text(root, other_path) or ""
        if len(name) >= 5 and name in text:
            backlinks.append(other)
    return sorted(backlinks, key=lambda m: m.title.lower())


def related_pages(pages: dict[str, WikiPageMeta], meta: WikiPageMeta) -> list[WikiPageMeta]:
    """Pages this one links to (in link order)."""
    return [pages[p] for p in meta.links if p in pages]


@dataclass
class Crumb:
    label: str
    root_key: str | None = None
    path: str | None = None  # a folder ("" for the root itself) or a page


def breadcrumbs(root: WikiRoot | None, path: str = "", is_dir: bool = False) -> list[Crumb]:
    """Crumbs after "Wiki": the root, each folder, then the page/folder itself
    (the last crumb is the current location)."""
    if root is None:
        return []
    crumbs = [Crumb(root.label, root.key, "")]
    parts = [p for p in path.split("/") if p]
    for index, part in enumerate(parts):
        sub = "/".join(parts[: index + 1])
        is_last = index == len(parts) - 1
        label = part.rsplit(".", 1)[0] if is_last and not is_dir else part
        crumbs.append(Crumb(label, root.key, sub + ("/" if (is_dir or not is_last) else "")))
    return crumbs
