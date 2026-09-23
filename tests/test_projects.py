import os

from app.config import Config


def test_loopback_binding_by_default(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_HOST", raising=False)
    monkeypatch.delenv("AGENTFLOW_ALLOW_UNSAFE_BIND", raising=False)
    config = Config.from_env()
    assert config.HOST == "127.0.0.1"


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
