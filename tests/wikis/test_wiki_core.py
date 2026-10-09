"""Project wiki browser internals (tasks 72-79): config, scanner/index and cache
invalidation, markdown rendering, search ranking, ADR parsing, metadata."""
import json
import os
import time

import pytest

from app.settings import wiki_config
from app.wikis import adr, metadata, persistence, renderer, scanner
from app.wikis.models import WikiRoot
from app.wikis.scanner import SCANNER, WikiScanner
from app.wikis.search import WikiSearchEngine
from tests.wikis.conftest import write_docs


def _root(tmp_path, key="repo-1", repo=True):
    base = tmp_path / "repo"
    docs = write_docs(str(base))
    return WikiRoot(key=key, kind="repo", label="repo", path=docs, project_id=1, ref_id=1,
                    repo_path=str(base) if repo else "")


# -- config (task 72) -----------------------------------------------------------------


def test_config_defaults_and_overrides(monkeypatch):
    config = wiki_config.load({})
    assert (config.repo_sync, config.storage, config.search_index_interval) == (True, "repo", 3600)
    assert config.docs_dirs == ("docs",)
    monkeypatch.setenv("AGENTFLOW_WIKI_STORAGE", "hybrid")
    monkeypatch.setenv("AGENTFLOW_WIKI_REPO_SYNC", "0")
    assert wiki_config.load({}).storage == "hybrid"
    assert wiki_config.load({}).repo_sync is False
    config = wiki_config.load({"WIKI_STORAGE": "bogus", "WIKI_DOCS_DIRS": "docs, wiki/", "WIKI_SEARCH_INDEX_INTERVAL": "60"})
    assert config.storage == "repo" and config.docs_dirs == ("docs", "wiki") and config.search_index_interval == 60


# -- scanner (task 73) ----------------------------------------------------------------


def test_scan_finds_markdown_and_skips_hidden(tmp_path):
    pages = SCANNER.scan_root(_root(tmp_path))
    assert set(pages) == {"index.md", "ARCHITECTURE.md", "guides/setup.md", "ADRs/001-use-jwt.md", "ADRs/002-queue.md"}


def test_scan_metadata(tmp_path):
    pages = SCANNER.scan_root(_root(tmp_path))
    assert pages["index.md"].title == "Project docs"
    assert pages["guides/setup.md"].title == "Setting up"  # frontmatter title wins
    assert pages["index.md"].links == ("ARCHITECTURE.md", "guides/setup.md")
    assert pages["ARCHITECTURE.md"].headings[:2] == ((1, "Architecture"), (2, "Layers"))
    assert pages["ADRs/001-use-jwt.md"].wiki_type == "adr"
    assert pages["guides/setup.md"].wiki_type == "guide"
    assert pages["ARCHITECTURE.md"].wiki_type == "architecture"
    assert pages["index.md"].wiki_type == "page"
    assert pages["index.md"].size > 0 and pages["index.md"].modified > 0


def test_scan_project_docs_entry_point(tmp_path):
    write_docs(str(tmp_path))
    assert "index.md" in WikiScanner().scan_project_docs(str(tmp_path))


def test_title_falls_back_to_file_name(tmp_path):
    root = _root(tmp_path)
    with open(os.path.join(root.path, "no_heading-here.md"), "w") as fh:
        fh.write("just text\n```\n# not a heading\n```\n")
    assert SCANNER.scan_root(root)["no_heading-here.md"].title == "no heading here"


def test_type_from_frontmatter_and_paths():
    assert scanner.page_type("notes/x.md", {"type": "ADR"}) == "adr"
    assert scanner.page_type("DECISIONS/x.md", {}) == "decision"
    assert scanner.page_type("api/x.md", {}) == "api"
    assert scanner.page_type("adr-0001-thing.md", {}) == "adr"


def test_frontmatter_parser():
    data, body = scanner.parse_frontmatter("---\ntitle: 'Quoted'\ntags: [a, b]\nowners:\n  - x\n  - y\n---\n\nBody")
    assert data == {"title": "Quoted", "tags": ["a", "b"], "owners": ["x", "y"]} and body == "Body"
    assert scanner.parse_frontmatter("---\nno closing fence\n")[0] == {}
    assert scanner.parse_frontmatter("# Not frontmatter")[1] == "# Not frontmatter"


def test_resolve_link_rejects_escapes_and_externals():
    assert scanner.resolve_link("a/b.md", "../c.md") == "c.md"
    assert scanner.resolve_link("a/b.md", "../../etc/passwd") is None
    assert scanner.resolve_link("a/b.md", "https://example.com/x.md") is None
    assert scanner.resolve_link("a/b.md", "#anchor") is None
    assert scanner.resolve_link("a/b.md", "/top.md") == "top.md"


def test_symlink_outside_root_is_ignored(tmp_path):
    root = _root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("# leaked")
    os.symlink(outside, os.path.join(root.path, "linked"))
    os.symlink(outside / "leak.md", os.path.join(root.path, "leak.md"))
    pages = SCANNER.scan_root(root)
    assert "leak.md" not in pages and not any(p.startswith("linked/") for p in pages)


def test_cache_reparses_only_changed_files(tmp_path, monkeypatch):
    root = _root(tmp_path)
    s = WikiScanner()
    s.scan_root(root)
    parsed = []
    original = s._parse
    monkeypatch.setattr(s, "_parse", lambda *a: parsed.append(a[1]) or original(*a))
    assert s.scan_root(root)["index.md"].title == "Project docs"
    assert parsed == []  # nothing changed: served from cache
    path = os.path.join(root.path, "index.md")
    with open(path, "w") as fh:
        fh.write("# Renamed docs\n")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert s.scan_root(root)["index.md"].title == "Renamed docs"
    assert parsed == ["index.md"]
    with open(os.path.join(root.path, "new.md"), "w") as fh:
        fh.write("# New page")
    os.utime(root.path, (time.time() + 10, time.time() + 10))
    assert "new.md" in s.scan_root(root)


def test_invalidate_and_interval(tmp_path):
    root = _root(tmp_path)
    s = WikiScanner(interval=0)
    s.scan_root(root)
    s.invalidate([root])
    assert s.scan_root(root)
    s.invalidate()
    assert s.text(root, "index.md").startswith("# Project docs")
    assert s.text(root, "../../etc/passwd") is None


def test_too_large_pages_are_listed_not_read(tmp_path):
    root = _root(tmp_path)
    s = WikiScanner(max_page_bytes=10)
    meta = s.scan_root(root)["ARCHITECTURE.md"]
    assert meta.too_large and s.text(root, "ARCHITECTURE.md") is None


def test_max_pages_cap(tmp_path):
    root = _root(tmp_path)
    assert len(WikiScanner(max_pages=2).scan_root(root)) == 2


# -- persistence (task 72) ------------------------------------------------------------


def test_get_page_shape(tmp_path):
    root = _root(tmp_path)
    page = persistence.get_page(root, "ADRs/001-use-jwt.md")
    assert page.id == "repo-1:ADRs/001-use-jwt.md" and page.project_id == 1 and page.sprint_id is None
    assert page.metadata["type"] == "adr" and page.metadata["status"] == "Accepted"
    assert page.published and page.version > 0 and "JWT" in page.content
    with pytest.raises(persistence.WikiNotFound):
        persistence.get_page(root, "missing.md")
    with pytest.raises(persistence.WikiNotFound):
        persistence.get_root([root], "repo-2")
    with pytest.raises(persistence.WikiNotFound):
        persistence.get_root([root], "../etc")
    assert persistence.list_by_sprint(None, 1) == []
    assert set(persistence.list_by_project([root])) == {"repo-1"}


# -- renderer (task 75) ---------------------------------------------------------------


def test_render_escapes_html_and_neutralises_js_links():
    html = renderer.render_markdown("<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n<img src=x onerror=y>")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "javascript:" not in html
    assert "<img src=x" not in html


def test_render_headings_tables_code_images():
    html = renderer.render_markdown(
        "# Title\n\n## Part\n\n## Part\n\n| a |\n|---|\n| 1 |\n\n```python\nx = 1\n```\n\n```\nplain <b>\n```\n\n![alt](pic.png)",
        "dir/page.md", lambda target: "/wiki/" + target,
    )
    assert '<h2 id="part" class="wiki-heading">' in html and 'id="part-1"' in html
    assert 'href="#part"' in html
    assert '<div class="wiki-table-wrap"><table>' in html
    assert 'class="wiki-code highlight" data-language="python"' in html and '<span class="n">x</span>' in html
    assert "plain &lt;b&gt;" in html
    assert '<img loading="lazy" src="/wiki/dir/pic.png"' in html


def test_render_rewrites_relative_links_only():
    html = renderer.render_markdown(
        "[a](other.md#sec) [b](https://example.com) [c](#local) [d](../../escape.md)",
        "sub/page.md", lambda target: "/W/" + target,
    )
    assert 'href="/W/sub/other.md#sec"' in html
    assert 'href="https://example.com"' in html and 'href="#local"' in html
    assert 'href="../../escape.md"' in html


def test_render_page_is_cached():
    first = renderer.render_page(("k", 1), "# One", "p.md", None)
    assert renderer.render_page(("k", 1), "# Changed", "p.md", None) == first


# -- search (task 76) -----------------------------------------------------------------


def test_search_ranking_snippets_and_terms(tmp_path):
    root = _root(tmp_path)
    engine = WikiSearchEngine()
    results = engine.search([root], "scheduler")
    assert {r.page_path for r in results} == {"ARCHITECTURE.md", "guides/setup.md"}
    assert all("<mark>scheduler</mark>" in r.snippet for r in results)
    # Multi-term: every term must match.
    assert [r.page_path for r in engine.search([root], "scheduler queue")] == ["ARCHITECTURE.md"]
    assert engine.search([root], "scheduler nonexistentword") == []
    # Prefix match and path match.
    assert "ARCHITECTURE.md" in {r.page_path for r in engine.search([root], "schedul")}
    assert engine.search([root], "adrs")[0].page_path.startswith("ADRs/")
    assert engine.search([root], "") == [] and engine.search([], "x") == []


def test_search_title_and_recency_boost(tmp_path):
    root = _root(tmp_path)
    for name in ("old.md", "new.md"):
        with open(os.path.join(root.path, name), "w") as fh:
            fh.write("# Note\n\nwidget widget\n")
    old = time.time() - 400 * 86400
    os.utime(os.path.join(root.path, "old.md"), (old, old))
    results = WikiSearchEngine().search([root], "widget")
    assert [r.page_path for r in results] == ["new.md", "old.md"]
    assert WikiSearchEngine().search([root], "architecture")[0].page_path == "ARCHITECTURE.md"


def test_search_snippet_is_escaped(tmp_path):
    root = _root(tmp_path)
    snippet = WikiSearchEngine().search([root], "alert")[0].snippet
    assert "<script>" not in snippet and "&lt;script&gt;" in snippet


def test_search_index_rebuilds_after_change(tmp_path):
    root = _root(tmp_path)
    engine = WikiSearchEngine()
    assert engine.search([root], "zebra") == []
    with open(os.path.join(root.path, "zoo.md"), "w") as fh:
        fh.write("# Zoo\n\nzebra")
    os.utime(root.path, (time.time() + 10, time.time() + 10))
    assert [r.page_path for r in engine.search([root], "zebra")] == ["zoo.md"]


def test_search_handles_many_pages_quickly(tmp_path):
    root = _root(tmp_path)
    for i in range(150):
        with open(os.path.join(root.path, f"bulk-{i}.md"), "w") as fh:
            fh.write(f"# Bulk {i}\n\n" + "lorem ipsum dolor " * 200 + ("needle" if i % 10 == 0 else ""))
    engine = WikiSearchEngine()
    engine.search([root], "warmup")
    start = time.monotonic()
    results = engine.search([root], "needle")
    assert len(results) == 15
    assert time.monotonic() - start < 1.0


# -- ADRs (task 78) -------------------------------------------------------------------


def test_adr_from_frontmatter_and_sections(tmp_path):
    root = _root(tmp_path)
    meta = SCANNER.scan_root(root)["ADRs/001-use-jwt.md"]
    record = adr.parse(meta, SCANNER.text(root, meta.path))
    assert (record.status, record.status_key, record.date, record.author) == ("Accepted", "accepted", "2026-01-10", "Ryan")
    assert record.stakeholders == ["Alice", "Bob"]
    assert record.sections["problem"] == "Tokens need secure storage."
    assert record.sections["decision"] == "Use JWT."
    assert "Server sessions" in record.sections["alternatives"]
    labels = [label for label, _ in adr.render_sections(record, meta.path, None)]
    assert labels == ["Problem", "Decision", "Consequences", "Alternatives"]


def test_adr_from_inline_metadata_and_timeline(tmp_path):
    root = _root(tmp_path)
    pages = SCANNER.scan_root(root)
    records = [(pages[p], adr.parse(pages[p], SCANNER.text(root, p))) for p in ("ADRs/001-use-jwt.md", "ADRs/002-queue.md")]
    second = records[1][1]
    assert (second.status_key, second.date) == ("proposed", "2026-03-02")
    assert second.sections["problem"] == "Need a queue."
    assert "Status" not in second.other
    assert [m.path for m, _ in adr.timeline(records)] == ["ADRs/002-queue.md", "ADRs/001-use-jwt.md"]


def test_adr_unknown_status_and_date_fallback(tmp_path):
    root = _root(tmp_path)
    path = os.path.join(root.path, "ADRs", "003-odd.md")
    with open(path, "w") as fh:
        fh.write("# Odd\n\n## Status\n\nMaybe later\n")
    meta = SCANNER.scan_root(root)["ADRs/003-odd.md"]
    record = adr.parse(meta, SCANNER.text(root, meta.path))
    assert record.status == "Maybe later" and record.status_key == "unknown"
    assert len(record.date) == 10


# -- metadata, breadcrumbs, related pages (tasks 77/79) -------------------------------


def test_backlinks_and_related(tmp_path):
    root = _root(tmp_path)
    pages = SCANNER.scan_root(root)
    assert [m.path for m in metadata.get_backlinks(root, pages, "ARCHITECTURE.md")] == ["index.md"]
    # A plain mention of the file name counts too.
    assert "guides/setup.md" in {m.path for m in metadata.get_backlinks(root, pages, "index.md")}
    assert [m.path for m in metadata.related_pages(pages, pages["index.md"])] == ["ARCHITECTURE.md", "guides/setup.md"]


def test_related_tasks_from_task_master(tmp_path):
    root = _root(tmp_path)
    tasks_dir = os.path.join(root.repo_path, ".taskmaster", "tasks")
    os.makedirs(tasks_dir)
    with open(os.path.join(tasks_dir, "tasks.json"), "w") as fh:
        json.dump({"master": {"tasks": [
            {"id": 7, "title": "Layering", "status": "pending", "details": "See docs/ARCHITECTURE.md"},
            {"id": 8, "title": "Other", "status": "done", "details": "nothing", "subtasks": [{"title": "x", "details": "ARCHITECTURE.md too"}]},
            {"id": 9, "title": "Unrelated", "status": "done"},
        ]}}, fh)
    meta = SCANNER.scan_root(root)["ARCHITECTURE.md"]
    assert [t["id"] for t in metadata.get_related_tasks(root, meta)] == [7, 8]
    folder_root = WikiRoot(key="folder-1", kind="folder", label="f", path=root.path, project_id=1, ref_id=1)
    assert metadata.get_related_tasks(folder_root, meta) == []


def test_author_without_git_is_none(tmp_path):
    root = _root(tmp_path)
    meta = SCANNER.scan_root(root)["index.md"]
    assert metadata.get_file_author(root, meta, provider_factory=None) is None


def test_breadcrumbs(tmp_path):
    root = _root(tmp_path)
    crumbs = metadata.breadcrumbs(root, "ADRs/001-use-jwt.md")
    assert [(c.label, c.path) for c in crumbs] == [("repo", ""), ("ADRs", "ADRs/"), ("001-use-jwt", "ADRs/001-use-jwt.md")]
    crumbs = metadata.breadcrumbs(root, "ADRs", is_dir=True)
    assert [(c.label, c.path) for c in crumbs] == [("repo", ""), ("ADRs", "ADRs/")]
    assert metadata.breadcrumbs(None, "x") == []
