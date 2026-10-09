"""Project wiki browser (docs/WIKI_INTEGRATION_AND_PRESENTATION.md §4.1, §7):
`/projects/<id>/wiki/` lists the project's wiki roots (repository docs folders
and local wiki folders, app/wikis/persistence.py); `/<root>/<path>` renders a
page, lists a folder (a timeline for decision-log folders) or serves an image
the pages embed; `/search` is full-text search (JSON for AJAX callers).
"""
from __future__ import annotations

import os

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for

from app.db import get_db
from app.projects import models as project_models
from app.settings import wiki_config
from app.wikis import adr, metadata, persistence, renderer
from app.wikis.models import ROOT_REPO, WikiPageMeta, WikiRoot
from app.wikis.persistence import WikiNotFound
from app.wikis.scanner import IMAGE_EXTENSIONS, PAGE_EXTENSIONS, SCANNER, parse_frontmatter
from app.wikis.search import ENGINE
from app.workspace import files

bp = Blueprint("project_wiki", __name__, url_prefix="/projects/<int:project_id>/wiki")

SORTS = ("name", "modified")
INDEX_PAGES = ("index.md", "README.md", "readme.md")
RECENT_LIMIT = 10
OPEN_TREE_MAX_PAGES = 60


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _config():
    config = wiki_config.load(current_app.config)
    SCANNER.configure(config.search_index_interval, config.max_pages, config.max_page_bytes)
    return config


def _setup(project_id: int):
    db = get_db()
    project = project_models.get_project(db, project_id)
    if project is None:
        abort(404)
    roots = persistence.list_roots(db, project_id, _config(), current_app.config["ALLOWED_PROJECT_ROOTS"])
    return project, roots


def page_url(project_id: int, root_key: str, path: str) -> str:
    return url_for("project_wiki.view", project_id=project_id, root_key=root_key, page_path=path)


def _link_mapper(project_id: int, root: WikiRoot, pages: dict[str, WikiPageMeta]):
    """Relative links in a page -> browser URLs for pages/folders/images in the
    same root; anything else is left alone."""
    def link_for(target: str) -> str | None:
        lower = target.lower()
        if target in pages or lower.endswith(IMAGE_EXTENSIONS):
            return page_url(project_id, root.key, target)
        if lower.endswith(PAGE_EXTENSIONS):
            return page_url(project_id, root.key, target)  # missing page: the browser shows a 404 inside the wiki
        if os.path.isdir(os.path.join(root.path, target)):
            return page_url(project_id, root.key, target.rstrip("/") + "/")
        return None
    return link_for


def _tree(pages: dict[str, WikiPageMeta]) -> list[dict]:
    """Nested folder/page nodes, each folder's pages before its sub-folders."""
    top: dict = {"folders": {}, "pages": []}
    for path, meta in pages.items():
        node = top
        parts = path.split("/")
        for index, part in enumerate(parts[:-1]):
            node = node["folders"].setdefault(part, {"folders": {}, "pages": [], "path": "/".join(parts[: index + 1])})
        node["pages"].append(meta)

    def build(node) -> list[dict]:
        items = [{"is_dir": False, "meta": m, "name": m.title, "path": m.path}
                 for m in sorted(node["pages"], key=lambda m: (m.name.lower() not in [i.lower() for i in INDEX_PAGES], m.title.lower()))]
        for name in sorted(node["folders"], key=str.lower):
            child = node["folders"][name]
            items.append({"is_dir": True, "name": name, "path": child["path"], "children": build(child)})
        return items

    return build(top)


def _toc(project_id: int, roots: list[WikiRoot], sort: str) -> list[dict]:
    toc = []
    for root, pages in zip(roots, (SCANNER.scan_root(r) for r in roots)):
        entry = {"root": root, "count": len(pages)}
        if sort == "modified":
            entry["recent"] = sorted(pages.values(), key=lambda m: m.modified, reverse=True)
        else:
            entry["tree"] = _tree(pages)
        toc.append(entry)
    return toc


def _render(project, roots, template_args: dict, status: int = 200):
    sort = request.args.get("sort", "name")
    sort = sort if sort in SORTS else "name"
    current_root = template_args.get("root")
    current_path = template_args.get("path", "")
    total = sum(len(SCANNER.scan_root(r)) for r in roots)
    sort_urls = {
        s: (url_for("project_wiki.view", project_id=project.id, root_key=current_root.key,
                    page_path=current_path or None, sort=s) if current_root
            else url_for("project_wiki.index", project_id=project.id, sort=s))
        for s in SORTS
    }
    return render_template(
        "wikis/browser.html", project=project, roots=roots, toc=_toc(project.id, roots, sort), sort=sort,
        sort_urls=sort_urls, open_all=total <= OPEN_TREE_MAX_PAGES, query=request.args.get("q", ""),
        page_url=lambda root_key, path: page_url(project.id, root_key, path),
        **{"root": None, "path": "", "crumbs": [], **template_args},
    ), status


@bp.get("/")
def index(project_id: int):
    project, roots = _setup(project_id)
    overview = []
    recent: list[tuple[WikiRoot, WikiPageMeta]] = []
    for root in roots:
        pages = SCANNER.scan_root(root)
        start = next((p for p in INDEX_PAGES if p in pages), None)
        overview.append({"root": root, "count": len(pages), "start": start,
                         "decisions": sum(1 for m in pages.values() if m.is_decision)})
        recent.extend((root, m) for m in pages.values())
    recent.sort(key=lambda pair: pair[1].modified, reverse=True)
    return _render(project, roots, {"view": "overview", "overview": overview, "recent": recent[:RECENT_LIMIT]})


@bp.get("/search")
def search(project_id: int):
    project, roots = _setup(project_id)
    query = request.args.get("q", "").strip()
    results = ENGINE.search(roots, query) if query else []
    for result in results:
        result.url = page_url(project_id, result.root_key, result.page_path)
    if _wants_json() or request.args.get("format") == "json":
        return jsonify({"query": query, "results": [r.to_dict() for r in results]})
    return _render(project, roots, {"view": "search", "results": results})


@bp.post("/rescan")
def rescan(project_id: int):
    project, roots = _setup(project_id)
    SCANNER.invalidate(roots)
    ENGINE.invalidate()
    persistence.list_by_project(roots, force=True)
    message = "Wiki rescanned."
    if _wants_json():
        return jsonify({"status": "rescanned", "message": message})
    flash(message, "info")
    return redirect(request.referrer or url_for("project_wiki.index", project_id=project_id))


@bp.get("/<root_key>/", defaults={"page_path": ""})
@bp.get("/<root_key>/<path:page_path>")
def view(project_id: int, root_key: str, page_path: str):
    project, roots = _setup(project_id)
    try:
        root = persistence.get_root(roots, root_key)
    except WikiNotFound:
        abort(404)
    pages = SCANNER.scan_root(root)
    path = page_path.strip("/")

    if path.lower().endswith(IMAGE_EXTENSIONS):
        return _asset(root, path)
    if not path or page_path.endswith("/") or (path not in pages and os.path.isdir(os.path.join(root.path, path))):
        return _folder(project, roots, root, pages, path)
    if path not in pages:
        crumbs = metadata.breadcrumbs(root, path)
        return _render(project, roots, {"view": "missing", "root": root, "path": path, "crumbs": crumbs}, 404)
    return _page(project, roots, root, pages, path)


def _asset(root: WikiRoot, path: str):
    """An image a page embeds; same path checks as page reads."""
    try:
        absolute = files.resolve_path(root.path, path)
    except ValueError:
        abort(404)
    if not os.path.isfile(absolute):
        abort(404)
    return send_file(absolute, max_age=300)


def _folder(project, roots, root: WikiRoot, pages: dict[str, WikiPageMeta], path: str):
    prefix = f"{path}/" if path else ""
    try:
        files.resolve_path(root.path, path)
    except ValueError:
        abort(404)
    direct = [m for p, m in pages.items() if p.startswith(prefix) and "/" not in p[len(prefix):]]
    subfolders = sorted({p[len(prefix):].split("/", 1)[0] for p in pages if p.startswith(prefix) and "/" in p[len(prefix):]}, key=str.lower)
    if not direct and not subfolders and path:
        abort(404)
    crumbs = metadata.breadcrumbs(root, path, is_dir=True)
    decisions = [m for m in direct if m.is_decision]
    timeline = None
    if decisions and len(decisions) * 2 >= len(direct):
        timeline = adr.timeline([(m, adr.parse(m, SCANNER.text(root, m.path) or "")) for m in decisions])
    direct.sort(key=lambda m: m.title.lower())
    return _render(project, roots, {
        "view": "folder", "root": root, "path": path, "crumbs": crumbs, "folder_pages": direct,
        "subfolders": [(name, f"{prefix}{name}/") for name in subfolders], "timeline": timeline,
    })


def _page(project, roots, root: WikiRoot, pages: dict[str, WikiPageMeta], path: str):
    meta = pages[path]
    page = persistence.get_page(root, path)
    link_for = _link_mapper(project.id, root, pages)
    record = sections = None
    body_html = ""
    if not meta.too_large:
        if meta.is_decision:
            record = adr.parse(meta, page.content)
            sections = adr.render_sections(record, path, link_for)
        else:
            body = parse_frontmatter(page.content)[1]
            body_html = renderer.render_page((root.key, root.path, path, meta.modified, meta.size), body, path, link_for)

    allowed = current_app.config["ALLOWED_PROJECT_ROOTS"]

    def provider():
        from app.execution.host import HostExecutionProvider

        return HostExecutionProvider(current_app.config["DATABASE_PATH"], allowed)

    author = metadata.get_file_author(root, meta, provider, allowed)
    edit_url = None
    if root.kind == ROOT_REPO:
        relative = os.path.relpath(os.path.join(root.path, path), root.repo_path)
        edit_url = url_for("workspace.edit_file", project_id=project.id, repo_id=root.ref_id, path=relative)
    share_url = request.url_root.rstrip("/") + page_url(project.id, root.key, path)
    return _render(project, roots, {
        "view": "page", "root": root, "path": path, "meta": meta, "page": page, "body_html": body_html,
        "record": record, "sections": sections, "author": author,
        "related_tasks": metadata.get_related_tasks(root, meta),
        "backlinks": metadata.get_backlinks(root, pages, path),
        "related": metadata.related_pages(pages, meta),
        "crumbs": metadata.breadcrumbs(root, path), "edit_url": edit_url, "share_url": share_url,
        "share_markdown": f"[{meta.title}]({share_url})",
    })
