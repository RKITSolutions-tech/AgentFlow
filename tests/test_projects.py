import os
import socket

from app.config import Config


def test_loopback_binding_by_default(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_HOST", raising=False)
    monkeypatch.delenv("AGENTFLOW_ALLOW_UNSAFE_BIND", raising=False)
    config = Config.from_env()
    # Default is this host's own hostname, not the literal "127.0.0.1" -- but
    # it must still resolve to loopback only (Config.from_env() would have
    # raised RuntimeError otherwise, since AGENTFLOW_ALLOW_UNSAFE_BIND is unset).
    assert config.HOST == socket.gethostname()


def test_create_view_and_reload_project(client, app):
    resp = client.post(
        "/projects/new",
        data={"name": "My Project", "description": "A test project"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"My Project" in resp.data

    # Reload the list to confirm persistence across requests.
    resp = client.get("/projects")
    assert b"My Project" in resp.data


def test_add_repository_within_allowed_root(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "repo-a")
    os.makedirs(repo_path)

    client.post("/projects/new", data={"name": "Proj", "description": ""})
    resp = client.get("/projects")
    project_id = 1

    resp = client.post(
        f"/projects/{project_id}/repositories",
        data={"name": "repo-a", "path": repo_path, "is_primary": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert repo_path.encode() in resp.data
    assert b"Yes" in resp.data


def test_delete_project_removes_it_from_list(client, app):
    client.post("/projects/new", data={"name": "Doomed Project", "description": ""})
    project_id = 1

    resp = client.post(
        f"/projects/{project_id}/delete",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "deleted"

    resp = client.get("/projects")
    assert b"Doomed Project" not in resp.data
    assert b"No projects yet." in resp.data


def test_delete_project_cascades_repositories(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "repo-doomed")
    os.makedirs(repo_path)

    client.post("/projects/new", data={"name": "Proj3", "description": ""})
    project_id = 1
    client.post(
        f"/projects/{project_id}/repositories",
        data={"name": "repo-doomed", "path": repo_path, "is_primary": "on"},
    )

    from app.db import get_db

    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM repositories WHERE project_id = ?", (project_id,)).fetchone()[0] == 1

    client.post(f"/projects/{project_id}/delete")

    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM repositories WHERE project_id = ?", (project_id,)).fetchone()[0] == 0


def test_reject_repository_path_outside_allowed_root(client, app, tmp_path):
    outside_path = tmp_path / "outside"
    outside_path.mkdir()

    client.post("/projects/new", data={"name": "Proj2", "description": ""})
    project_id = 1

    resp = client.post(
        f"/projects/{project_id}/repositories",
        data={"name": "bad-repo", "path": str(outside_path)},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"outside the configured allowed roots" in resp.data
    assert b"No repositories yet." in resp.data


def _start_fake_chat(app, project_id, script):
    from app.agents.base import AgentContext
    from app.agents.fake import FakeAgentAdapter
    from app.db import get_db

    with app.app_context():
        db = get_db()
        adapter = FakeAgentAdapter(db)
        return adapter.start(
            AgentContext(project_id=project_id, working_directory="."), "hello",
            options={"script": script},
        ).id


def test_project_detail_shows_recent_chat_widget_and_redacts_secrets(client, app):
    resp = client.post("/projects/new", data={"name": "ChatProj", "description": ""})
    project_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    _start_fake_chat(
        app, project_id,
        [{"action": "message", "text": "hi, api_key=sk-abcdefghijklmnopqrst"}],
    )

    resp = client.get(f"/projects/{project_id}")
    assert resp.status_code == 200
    assert b"Recent Chat" in resp.data
    assert b"hi, api_key" in resp.data
    assert b"sk-abcdefghijklmnopqrst" not in resp.data


def test_chat_history_page_filters_and_redacts(client, app):
    resp = client.post("/projects/new", data={"name": "ChatProj2", "description": ""})
    project_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    session_id = _start_fake_chat(
        app, project_id, [{"action": "message", "text": "secret api_key=sk-abcdefghijklmnopqrst"}]
    )

    resp = client.get(f"/projects/{project_id}/chat-history")
    assert resp.status_code == 200
    assert b"[REDACTED]" in resp.data
    assert b"sk-abcdefghijklmnopqrst" not in resp.data

    resp = client.get(f"/projects/{project_id}/chat-history?session_id={session_id}")
    assert resp.status_code == 200
    assert b"secret api_key" in resp.data

    resp = client.get(f"/projects/{project_id}/chat-history?role=system")
    assert resp.status_code == 200
    assert b"No messages match." in resp.data


def test_chat_export_downloads_markdown_and_json(client, app):
    resp = client.post("/projects/new", data={"name": "ChatProj4", "description": ""})
    project_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    _start_fake_chat(app, project_id, [{"action": "message", "text": "export me"}])

    resp = client.get(f"/projects/{project_id}/chat/export?format=markdown")
    assert resp.status_code == 200
    assert resp.mimetype == "text/markdown"
    assert b"export me" in resp.data
    assert "attachment" in resp.headers["Content-Disposition"]

    resp = client.get(f"/projects/{project_id}/chat/export?format=json")
    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    assert b"export me" in resp.data

    resp = client.get(f"/projects/{project_id}/chat/export?format=xml")
    assert resp.status_code == 400


def test_chat_archive_expand_returns_messages_for_summary(client, app):
    resp = client.post("/projects/new", data={"name": "ChatProj3", "description": ""})
    project_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    _start_fake_chat(app, project_id, [{"action": "message", "text": "archived message"}])

    from app.db import get_db
    from app.sessions import chat as chat_module

    with app.app_context():
        db = get_db()
        summary_id = chat_module.create_summary(
            db, project_id, period_start="2000-01-01 00:00:00", period_end="2100-01-01 23:59:59",
            summary="session: 1 message", message_count=1, session_ids=[1],
        )

    resp = client.get(f"/projects/{project_id}/chat/archive/{summary_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert any("archived message" in m["content"] for m in data["messages"])

    resp = client.get(f"/projects/{project_id}/chat/archive/999999")
    assert resp.status_code == 404
