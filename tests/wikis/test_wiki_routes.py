"""Project wiki browser routes (tasks 74-81): browsing, rendering, search JSON,
folder/ADR timeline views, image serving, metadata sidebar, navigation."""
import os
import subprocess

from app.db import get_db
from tests.conftest import create_project_with_repo
from tests.wikis.conftest import PNG, write_docs

AJAX = {"X-Requested-With": "XMLHttpRequest"}


def _project(client, app, git=False):
    pid, repo = create_project_with_repo(client, app.config["allowed_root"])
    write_docs(repo)
    if git:
        env = {**os.environ, "GIT_AUTHOR_NAME": "Doc Writer", "GIT_AUTHOR_EMAIL": "d@example.com",
               "GIT_COMMITTER_NAME": "Doc Writer", "GIT_COMMITTER_EMAIL": "d@example.com"}
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "docs"]):
            subprocess.run(cmd, cwd=repo, check=True, capture_output=True, env=env)
    with app.app_context():
        repo_id = get_db().execute("SELECT id FROM repositories WHERE project_id = ?", (pid,)).fetchone()[0]
    return pid, repo, f"repo-{repo_id}"


def test_wiki_tab_and_overview(client, app):
    pid, _, root = _project(client, app)
    html = client.get(f"/projects/{pid}/wiki/").get_data(as_text=True)
    assert 'aria-current="page">Wiki</a>' in html
    assert f'/projects/{pid}/wiki/{root}/index.md' in html  # root card opens its index page
    assert "5 pages, 2 decision records" in html
    assert "Recently changed" in html
    assert html.count("data-wiki-toc-item") == 5
    # Wiki tab appears on other project pages too.
    assert f'href="/projects/{pid}/wiki/"' in client.get(f"/projects/{pid}").get_data(as_text=True)


def test_empty_project_explains_where_pages_come_from(client, app):
    pid, _ = create_project_with_repo(client, app.config["allowed_root"], repo_name="bare")
    html = client.get(f"/projects/{pid}/wiki/").get_data(as_text=True)
    assert "No wiki pages yet" in html and "docs/" in html
    assert client.get("/projects/999/wiki/").status_code == 404


def test_page_renders_markdown_with_metadata(client, app):
    pid, _, root = _project(client, app)
    html = client.get(f"/projects/{pid}/wiki/{root}/ARCHITECTURE.md").get_data(as_text=True)
    assert '<h2 id="layers" class="wiki-heading">' in html
    assert '<div class="wiki-table-wrap"><table>' in html
    assert 'class="wiki-code highlight"' in html
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "data-wiki-collapsible" in html
    # Metadata sidebar: modified, type, actions, backlinks.
    assert "Last modified" in html and "architecture" in html
    assert "Copy link" in html and "data-copy=" in html and ">Share<" in html
    assert "/files/edit?path=docs/ARCHITECTURE.md" in html
    assert "Linked from" in html and f'/projects/{pid}/wiki/{root}/index.md">Project docs</a>' in html
    # Breadcrumb: project wiki > root > page.
    assert f'href="/projects/{pid}/wiki/{root}/">repo-a</a>' in html
    assert 'aria-current="page">/ ARCHITECTURE</span>' in html


def test_relative_links_and_images_are_rewritten_and_served(client, app):
    pid, _, root = _project(client, app)
    html = client.get(f"/projects/{pid}/wiki/{root}/index.md").get_data(as_text=True)
    assert f'href="/projects/{pid}/wiki/{root}/ARCHITECTURE.md#layers"' in html
    assert f'href="/projects/{pid}/wiki/{root}/guides/setup.md"' in html
    assert f'src="/projects/{pid}/wiki/{root}/img/diagram.png"' in html
    image = client.get(f"/projects/{pid}/wiki/{root}/img/diagram.png")
    assert image.status_code == 200 and image.data == PNG and image.mimetype == "image/png"
    assert client.get(f"/projects/{pid}/wiki/{root}/img/missing.png").status_code == 404


def test_frontmatter_is_not_rendered(client, app):
    pid, _, root = _project(client, app)
    html = client.get(f"/projects/{pid}/wiki/{root}/guides/setup.md").get_data(as_text=True)
    assert "title: Setting up" not in html and "Install the scheduler" in html


def test_missing_page_and_escape_attempts(client, app):
    pid, _, root = _project(client, app)
    resp = client.get(f"/projects/{pid}/wiki/{root}/nope.md")
    assert resp.status_code == 404 and "Page not found" in resp.get_data(as_text=True)
    assert client.get(f"/projects/{pid}/wiki/{root}/../../../etc/passwd").status_code == 404
    assert client.get(f"/projects/{pid}/wiki/{root}/.hidden/secret.md").status_code == 404
    assert client.get(f"/projects/{pid}/wiki/repo-999/").status_code == 404
    assert client.get(f"/projects/{pid}/wiki/evil/").status_code == 404
    other, _ = create_project_with_repo(client, app.config["allowed_root"], repo_name="other", project_name="Other")
    assert client.get(f"/projects/{other}/wiki/{root}/index.md").status_code == 404  # another project's root


def test_adr_card_and_timeline(client, app):
    pid, _, root = _project(client, app)
    html = client.get(f"/projects/{pid}/wiki/{root}/ADRs/001-use-jwt.md").get_data(as_text=True)
    assert 'class="adr-card adr-status-accepted"' in html
    assert ">ADR</span>" in html and "Accepted" in html and "Alice, Bob" in html
    for label in ("Problem", "Decision", "Consequences", "Alternatives"):
        assert f"<h2>{label}</h2>" in html
    folder = client.get(f"/projects/{pid}/wiki/{root}/ADRs/").get_data(as_text=True)
    assert "Decision log" in folder
    assert folder.index("Queue backend") < folder.index("Use JWT for sessions")  # newest first
    assert "adr-status-proposed" in folder


def test_folder_listing(client, app):
    pid, _, root = _project(client, app)
    html = client.get(f"/projects/{pid}/wiki/{root}/").get_data(as_text=True)
    assert "ADRs/" in html and "guides/" in html and "Project docs" in html
    assert client.get(f"/projects/{pid}/wiki/{root}/guides").status_code == 200  # no trailing slash
    assert client.get(f"/projects/{pid}/wiki/{root}/nothing-here/").status_code == 404


def test_search_json_and_html(client, app):
    pid, _, root = _project(client, app)
    resp = client.get(f"/projects/{pid}/wiki/search?q=scheduler", headers=AJAX)
    paths = [r["path"] for r in resp.json["results"]]
    assert set(paths) == {"ARCHITECTURE.md", "guides/setup.md"}
    first = resp.json["results"][0]
    assert first["url"].startswith(f"/projects/{pid}/wiki/{root}/") and "<mark>" in first["snippet"]
    html = client.get(f"/projects/{pid}/wiki/search?q=scheduler").get_data(as_text=True)
    assert "2 results for" in html and "<mark>scheduler</mark>" in html
    assert client.get(f"/projects/{pid}/wiki/search?q=", headers=AJAX).json["results"] == []


def test_sort_by_modified(client, app):
    pid, repo, root = _project(client, app)
    os.utime(os.path.join(repo, "docs", "guides", "setup.md"), (2_000_000_000, 2_000_000_000))
    html = client.get(f"/projects/{pid}/wiki/?sort=modified").get_data(as_text=True)
    toc = html[html.index('id="wikiToc"'):html.index('class="wiki-main"')]
    assert toc.index("Setting up") < toc.index("Project docs")
    assert "wiki-toc-date" in toc


def test_rescan_picks_up_new_pages(client, app):
    pid, repo, root = _project(client, app)
    client.get(f"/projects/{pid}/wiki/")
    with open(os.path.join(repo, "docs", "fresh.md"), "w") as fh:
        fh.write("# Fresh page")
    resp = client.post(f"/projects/{pid}/wiki/rescan", headers=AJAX)
    assert resp.json["status"] == "rescanned"
    assert "Fresh page" in client.get(f"/projects/{pid}/wiki/").get_data(as_text=True)


def test_local_wiki_folder_is_a_root(client, app):
    pid, _, _ = _project(client, app)
    folder = os.path.join(app.config["allowed_root"], "team-wiki")
    client.post("/wiki/sources", data={"name": "Team wiki", "location": "local", "path": folder,
                                       "create": "on", "project_id": pid})
    html = client.get(f"/projects/{pid}/wiki/").get_data(as_text=True)
    assert "Team wiki" in html and "Wiki folder" in html
    with app.app_context():
        source_id = get_db().execute("SELECT id FROM wiki_sources").fetchone()[0]
    page = client.get(f"/projects/{pid}/wiki/folder-{source_id}/index.md").get_data(as_text=True)
    assert "Team wiki" in page and "/files/edit" not in page  # folders aren't repository files


def test_git_author_in_sidebar(client, app):
    pid, _, root = _project(client, app, git=True)
    html = client.get(f"/projects/{pid}/wiki/{root}/index.md").get_data(as_text=True)
    assert "Last commit" in html and "Doc Writer" in html


def test_folder_wiki_viewer_renders_markdown(client, app):
    folder = os.path.join(app.config["allowed_root"], "rendered-wiki")
    resp = client.post("/wiki/sources", data={"name": "Rendered", "location": "local", "path": folder, "create": "on"})
    source_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    with open(os.path.join(folder, "other.md"), "w") as fh:
        fh.write("# Other")
    with open(os.path.join(folder, "index.md"), "w") as fh:
        fh.write("# Home\n\nSee [other](other.md).\n\n<script>x</script>")
    html = client.get(f"/wiki/sources/{source_id}").get_data(as_text=True)
    assert '<h1 id="home" class="wiki-heading">' in html
    assert f'href="/wiki/sources/{source_id}?page=other.md"' in html
    assert "<script>x" not in html
