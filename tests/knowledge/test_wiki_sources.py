"""Folder-backed wikis (docs/WIKI_INTEGRATION_AND_PRESENTATION.md §14): set up
and browse a markdown folder on this machine, or on a remote instance through
its federation API."""
import os

import pytest

from app.db import get_db
from app.instances import models as instance_models
from app.knowledge import wiki_folders, wiki_sources
from app.knowledge.wiki_folders import WikiFolderError
from tests.instances.test_federation_e2e import _make_app, _ServerThread


def _source_id(resp):
    return int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])


# -- wiki_folders (the per-host operations) ---------------------------------

def test_setup_creates_missing_folder_with_starter_page(tmp_path):
    root = str(tmp_path)
    path = wiki_folders.setup_folder(os.path.join(root, "handbook"), (root,), create=True, name="Handbook")
    assert wiki_folders.list_pages(path, (root,)) == ["index.md"]
    assert wiki_folders.read_page(path, (root,), "index.md").startswith("# Handbook")


def test_setup_leaves_existing_folder_untouched(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("A")
    path = wiki_folders.setup_folder(str(tmp_path / "docs"), (str(tmp_path),), create=True)
    assert wiki_folders.list_pages(path, (str(tmp_path),)) == ["a.md"]


def test_setup_requires_create_for_missing_folder(tmp_path):
    with pytest.raises(WikiFolderError, match="does not exist"):
        wiki_folders.setup_folder(str(tmp_path / "nope"), (str(tmp_path),))
    assert not (tmp_path / "nope").exists()


def test_setup_refuses_folder_outside_allowed_roots(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with pytest.raises(WikiFolderError, match="outside"):
        wiki_folders.setup_folder(str(tmp_path / "elsewhere"), (str(allowed),), create=True)
    assert not (tmp_path / "elsewhere").exists()


def test_list_pages_recurses_and_skips_hidden_and_non_markdown(tmp_path):
    (tmp_path / "adr").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "index.md").write_text("i")
    (tmp_path / "adr" / "001.md").write_text("d")
    (tmp_path / ".git" / "x.md").write_text("h")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    assert wiki_folders.list_pages(str(tmp_path), (str(tmp_path),)) == ["index.md", "adr/001.md"]


@pytest.mark.parametrize("page", ["../secret.md", "/etc/passwd", "notes.txt"])
def test_read_page_rejects_escapes_and_non_markdown(tmp_path, page):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (tmp_path / "secret.md").write_text("secret")
    (wiki / "notes.txt").write_text("n")
    with pytest.raises(WikiFolderError):
        wiki_folders.read_page(str(wiki), (str(tmp_path),), page)


# -- local wiki through the UI ----------------------------------------------

def test_local_wiki_setup_and_browse(app, client):
    folder = os.path.join(app.config["allowed_root"], "handbook")
    resp = client.post("/wiki/sources", data={"name": "Handbook", "location": "local", "path": folder, "create": "on"})
    assert resp.status_code == 302
    source_id = _source_id(resp)

    resp = client.get(f"/wiki/sources/{source_id}")
    assert resp.status_code == 200
    assert b'<h1 id="handbook" class="wiki-heading">Handbook' in resp.data  # starter index.md, rendered

    with open(os.path.join(folder, "setup.md"), "w") as fh:
        fh.write("Run <script>alert(1)</script> setup.sh")
    resp = client.get(f"/wiki/sources/{source_id}?page=setup.md")
    assert b"&lt;script&gt;" in resp.data and b"<script>alert" not in resp.data

    resp = client.get("/wiki/sources")
    assert b"Handbook" in resp.data and b"This machine" in resp.data


def test_local_wiki_setup_error_is_flashed_and_nothing_stored(app, client):
    resp = client.post("/wiki/sources", data={"name": "X", "location": "local", "path": "/etc"}, follow_redirects=True)
    assert b"outside the configured allowed roots" in resp.data
    with app.app_context():
        assert wiki_sources.list_sources(get_db()) == []


def test_remove_wiki_via_ajax_keeps_folder(app, client):
    folder = os.path.join(app.config["allowed_root"], "w")
    source_id = _source_id(client.post("/wiki/sources", data={"name": "W", "location": "local", "path": folder, "create": "on"}))
    resp = client.post(f"/wiki/sources/{source_id}/delete", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200 and resp.get_json()["status"] == "deleted"
    assert os.path.isfile(os.path.join(folder, "index.md"))
    assert client.get(f"/wiki/sources/{source_id}").status_code == 404


def test_page_view_reports_missing_page(app, client):
    folder = os.path.join(app.config["allowed_root"], "w")
    source_id = _source_id(client.post("/wiki/sources", data={"name": "W", "location": "local", "path": folder, "create": "on"}))
    resp = client.get(f"/wiki/sources/{source_id}?page=../../x.md")
    assert resp.status_code == 200
    assert b"not found" in resp.data


# -- remote wiki via the federation API ---------------------------------------

@pytest.fixture
def remote(tmp_path):
    app = _make_app(tmp_path, "remote", federation_token="shared-secret")
    thread = _ServerThread(app)
    thread.start()
    yield app, f"http://127.0.0.1:{thread.port}"
    thread.shutdown()
    thread.join(timeout=5)


def test_federation_wiki_endpoints_require_token(remote):
    remote_app, _ = remote
    resp = remote_app.test_client().post("/federation/api/wiki/setup", json={"path": "/tmp"})
    assert resp.status_code == 401


def test_remote_wiki_setup_and_browse(tmp_path, remote):
    remote_app, remote_url = remote
    master_app = _make_app(tmp_path, "master")
    master_client = master_app.test_client()
    with master_app.app_context():
        instance_id = instance_models.add_instance(get_db(), "remote-win", remote_url, "shared-secret")

    remote_folder = os.path.join(remote_app.config["allowed_root"], "team-wiki")
    resp = master_client.post("/wiki/sources", data={
        "name": "Team", "location": f"remote:{instance_id}", "path": remote_folder, "create": "on",
    })
    assert resp.status_code == 302
    source_id = _source_id(resp)

    # The folder was created on the remote host, under the remote's allowed root.
    assert os.path.isfile(os.path.join(remote_folder, "index.md"))
    with open(os.path.join(remote_folder, "deploy.md"), "w") as fh:
        fh.write("Deploy notes")

    resp = master_client.get(f"/wiki/sources/{source_id}?page=deploy.md")
    assert resp.status_code == 200
    assert b"Deploy notes" in resp.data
    assert b"Remote: remote-win" in resp.data


def test_remote_wiki_path_checked_against_remote_roots(tmp_path, remote):
    _, remote_url = remote
    master_app = _make_app(tmp_path, "master")
    master_client = master_app.test_client()
    with master_app.app_context():
        instance_id = instance_models.add_instance(get_db(), "remote-win", remote_url, "shared-secret")

    # Allowed on the master, but not on the remote -- the remote decides.
    master_folder = os.path.join(master_app.config["allowed_root"], "w")
    resp = master_client.post("/wiki/sources", data={
        "name": "W", "location": f"remote:{instance_id}", "path": master_folder, "create": "on",
    }, follow_redirects=True)
    assert b"outside the configured allowed roots" in resp.data
    assert not os.path.exists(master_folder)
    with master_app.app_context():
        assert wiki_sources.list_sources(get_db()) == []


def test_unreachable_remote_shows_error_not_500(tmp_path):
    master_app = _make_app(tmp_path, "master")
    with master_app.app_context():
        db = get_db()
        instance_id = instance_models.add_instance(db, "gone", "http://127.0.0.1:9", "t")
        db.execute(
            "INSERT INTO wiki_sources (name, location, path, instance_id) VALUES ('G', 'remote', '/x', ?)",
            (instance_id,),
        )
        db.commit()
        source_id = wiki_sources.list_sources(db)[0].id
    resp = master_app.test_client().get(f"/wiki/sources/{source_id}")
    assert resp.status_code == 200
    assert b"unreachable" in resp.data


# -- knowledge store: search / write ----------------------------------------

def test_search_ranks_by_term_frequency_and_requires_all_terms(tmp_path):
    root = str(tmp_path)
    (tmp_path / "auth.md").write_text("# Auth\nWe use JWT tokens.\nJWT refresh every hour.")
    (tmp_path / "deploy.md").write_text("Deploy with JWT secrets from vault.")
    (tmp_path / "other.md").write_text("Nothing relevant.")
    hits = wiki_folders.search_pages(root, (root,), "jwt")
    assert [h["page"] for h in hits] == ["auth.md", "deploy.md"]
    assert hits[0]["snippet"] == "We use JWT tokens."
    assert [h["page"] for h in wiki_folders.search_pages(root, (root,), "jwt vault")] == ["deploy.md"]
    assert wiki_folders.search_pages(root, (root,), "   ") == []


def test_write_page_creates_subfolders_and_redacts(tmp_path):
    root = str(tmp_path)
    page = wiki_folders.write_page(root, (root,), "decisions/auth.md", "token: sk-abcdefghijklmnopqrstuvwxyz123456", (r"token: \S+",))
    assert page == "decisions/auth.md"
    text = (tmp_path / "decisions" / "auth.md").read_text()
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in text


@pytest.mark.parametrize("page", ["../escape.md", ".hidden/x.md", "notes.txt", "/abs/../../x.md"])
def test_write_page_rejects_bad_paths(tmp_path, page):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    with pytest.raises(WikiFolderError):
        wiki_folders.write_page(str(wiki), (str(tmp_path),), page, "x")
    assert not (tmp_path / "escape.md").exists()


# -- project scoping and session context --------------------------------------

def _project(client, app, name):
    from tests.conftest import create_project_with_repo
    pid, _ = create_project_with_repo(client, app.config["allowed_root"], repo_name=name, project_name=name)
    return pid


def _add_local(client, app, name, project_id=None):
    folder = os.path.join(app.config["allowed_root"], f"wiki-{name}")
    data = {"name": name, "location": "local", "path": folder, "create": "on"}
    if project_id:
        data["project_id"] = str(project_id)
    return _source_id(client.post("/wiki/sources", data=data)), folder


def test_project_sees_own_and_shared_wikis_only(app, client):
    a, b = _project(client, app, "A"), _project(client, app, "B")
    _add_local(client, app, "shared")
    _add_local(client, app, "a-only", project_id=a)
    _add_local(client, app, "b-only", project_id=b)
    with app.app_context():
        names = [s.name for s in wiki_sources.list_for_project(get_db(), a)]
    assert names == ["a-only", "shared"]


def test_session_start_includes_wiki_context(app, client):
    from app.agents.models import PLACEHOLDER_PROMPT, get_agent_session, list_agent_events

    pid = _project(client, app, "Ctx")
    source_id, folder = _add_local(client, app, "Handbook", project_id=pid)
    with open(os.path.join(folder, "index.md"), "a") as fh:
        fh.write("\nWe deploy on Fridays.\n")

    assert b'name="wiki_context"' in client.get(f"/sessions/project/{pid}").data
    client.post(f"/sessions/project/{pid}/create", data={"agent_type": "fake", "wiki_context": "1"})
    with app.app_context():
        db = get_db()
        session_id = db.execute("SELECT id FROM agent_sessions ORDER BY id DESC LIMIT 1").fetchone()[0]
        prompt = next(e.data for e in list_agent_events(db, session_id) if e.event_type == "PromptSubmitted")
        injected = get_agent_session(db, session_id).metadata["injected_context"]
    assert f"Wiki {source_id}: Handbook" in prompt
    assert "We deploy on Fridays." in prompt
    assert "wiki_write" in prompt
    assert prompt.endswith(PLACEHOLDER_PROMPT)
    assert injected["wiki_context"] is True


def test_session_without_wiki_context_is_unchanged(app, client):
    from app.agents.models import PLACEHOLDER_PROMPT, list_agent_events

    pid = _project(client, app, "NoCtx")
    _add_local(client, app, "Handbook", project_id=pid)
    client.post(f"/sessions/project/{pid}/create", data={"agent_type": "fake"})
    with app.app_context():
        db = get_db()
        session_id = db.execute("SELECT id FROM agent_sessions ORDER BY id DESC LIMIT 1").fetchone()[0]
        prompt = next(e.data for e in list_agent_events(db, session_id) if e.event_type == "PromptSubmitted")
    assert prompt == PLACEHOLDER_PROMPT


def test_session_context_survives_unreachable_remote(app, client):
    pid = _project(client, app, "Down")
    with app.app_context():
        db = get_db()
        instance_id = instance_models.add_instance(db, "gone", "http://127.0.0.1:9", "t")
        db.execute(
            "INSERT INTO wiki_sources (name, location, path, instance_id, project_id) VALUES ('R', 'remote', '/x', ?, ?)",
            (instance_id, pid),
        )
        db.commit()
        context = wiki_sources.session_context(db, app.config["ALLOWED_PROJECT_ROOTS"], pid)
    assert "Unavailable right now" in context


def test_wiki_search_in_ui(app, client):
    source_id, folder = _add_local(client, app, "S")
    with open(os.path.join(folder, "auth.md"), "w") as fh:
        fh.write("JWT refresh policy")
    resp = client.get(f"/wiki/sources/{source_id}?q=jwt")
    assert b"1 result" in resp.data and b"auth.md" in resp.data


# -- MCP wiki tools -------------------------------------------------------------

def test_mcp_wiki_tools_search_read_write_and_scope(app, client):
    from app.mcp.tools import wiki as wiki_tools

    a, b = _project(client, app, "A"), _project(client, app, "B")
    wiki_a, _ = _add_local(client, app, "a-wiki", project_id=a)
    wiki_b, _ = _add_local(client, app, "b-wiki", project_id=b)
    roots = app.config["ALLOWED_PROJECT_ROOTS"]
    with app.app_context():
        db = get_db()
        saved = wiki_tools.write(db, a, roots, wiki_a, "decisions/db.md", "Chose SQLite over Postgres.")
        assert saved["page"] == "decisions/db.md"
        hits = wiki_tools.search(db, a, roots, "sqlite")
        assert [(h["wiki_id"], h["page"]) for h in hits] == [(wiki_a, "decisions/db.md")]
        assert "SQLite" in wiki_tools.read(db, a, roots, wiki_a, "decisions/db.md")["content"]
        listed = wiki_tools.list_wikis(db, a, roots)
        assert [w["id"] for w in listed] == [wiki_a]
        assert "decisions/db.md" in listed[0]["pages"]
        with pytest.raises(LookupError):
            wiki_tools.read(db, a, roots, wiki_b, "index.md")
        with pytest.raises(LookupError):
            wiki_tools.write(db, a, roots, wiki_b, "x.md", "nope")


def test_mcp_server_command_forwards_roots_and_escapes_codex_env(monkeypatch):
    from app.agents import mcp_config

    monkeypatch.setenv("AGENTFLOW_ALLOWED_ROOTS", "/srv/a")
    monkeypatch.setenv("AGENTFLOW_REDACT_PATTERNS", '["key=\\\\w+"]')
    _, _, env = mcp_config.server_command(1, 2, "/db")
    assert env["AGENTFLOW_ALLOWED_ROOTS"] == "/srv/a"
    flags = mcp_config.codex_mcp_flags(1, 2, "/db")
    env_flag = next(f for f in flags if f.startswith("mcp_servers.agentflow.env="))
    assert 'AGENTFLOW_REDACT_PATTERNS = "[\\"key=\\\\\\\\w+\\"]"' in env_flag
    assert {"wiki_search", "wiki_write"} <= set(mcp_config.TOOL_NAMES)


# -- remote search / write ---------------------------------------------------------

def test_remote_wiki_search_and_write(tmp_path, remote):
    from app.mcp.tools import wiki as wiki_tools

    remote_app, remote_url = remote
    master_app = _make_app(tmp_path, "master")
    master_client = master_app.test_client()
    with master_app.app_context():
        instance_id = instance_models.add_instance(get_db(), "remote-win", remote_url, "shared-secret")
    remote_folder = os.path.join(remote_app.config["allowed_root"], "kb")
    source_id = _source_id(master_client.post("/wiki/sources", data={
        "name": "KB", "location": f"remote:{instance_id}", "path": remote_folder, "create": "on",
    }))
    with master_app.app_context():
        db = get_db()
        project_id = db.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')").lastrowid
        db.commit()
        roots = master_app.config["ALLOWED_PROJECT_ROOTS"]
        wiki_tools.write(db, project_id, roots, source_id, "runbook.md", "Restart the worker with systemctl.")
        assert os.path.isfile(os.path.join(remote_folder, "runbook.md"))
        hits = wiki_tools.search(db, project_id, roots, "systemctl")
    assert [h["page"] for h in hits] == ["runbook.md"]
