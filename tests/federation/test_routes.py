import pytest

from app import create_app
from app.config import Config
from tests.conftest import create_project_with_repo


def test_federation_disabled_by_default(client):
    """FEDERATION_TOKEN is unset for the plain `app`/`client` fixtures, so
    every federation route must fail closed rather than silently accept."""
    resp = client.get("/federation/api/ping")
    assert resp.status_code == 503


@pytest.fixture
def federated_app(tmp_path):
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    config = Config(
        DATABASE_PATH=str(tmp_path / "test.sqlite3"),
        SECRET_KEY="test-secret",
        ALLOWED_PROJECT_ROOTS=(str(allowed_root),),
        TESTING=True,
        FEDERATION_TOKEN="shared-secret",
    )
    application = create_app(config)
    application.config["allowed_root"] = str(allowed_root)
    return application


@pytest.fixture
def federated_client(federated_app):
    return federated_app.test_client()


def test_missing_token_is_unauthorized(federated_client):
    resp = federated_client.get("/federation/api/ping")
    assert resp.status_code == 401


def test_wrong_token_is_unauthorized(federated_client):
    resp = federated_client.get(
        "/federation/api/ping", headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 401


def test_correct_token_pings_ok(federated_client):
    resp = federated_client.get(
        "/federation/api/ping", headers={"Authorization": "Bearer shared-secret"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def _auth():
    return {"Authorization": "Bearer shared-secret"}


def test_list_projects_empty(federated_client):
    resp = federated_client.get("/federation/api/projects", headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["projects"] == []


def test_project_sessions_round_trip(federated_app, federated_client):
    project_id, repo_path = create_project_with_repo(
        federated_client, federated_app.config["allowed_root"], repo_name="repo-a", project_name="Proj"
    )

    resp = federated_client.get("/federation/api/projects", headers=_auth())
    projects = resp.get_json()["projects"]
    assert len(projects) == 1
    assert projects[0]["id"] == project_id
    assert projects[0]["repositories"][0]["path"] == repo_path

    resp = federated_client.post(
        f"/federation/api/projects/{project_id}/sessions",
        json={"agent_type": "fake", "script": [{"action": "message", "text": "hi"}, {"action": "complete"}]},
        headers=_auth(),
    )
    assert resp.status_code == 201
    session_id = resp.get_json()["id"]

    resp = federated_client.get(f"/federation/api/sessions/{session_id}", headers=_auth())
    detail = resp.get_json()
    assert detail["agent_type"] == "fake"
    assert detail["status"] == "COMPLETED"

    resp = federated_client.get(
        f"/federation/api/sessions/{session_id}/stream?after_id=0", headers=_auth()
    )
    events = resp.get_json()["events"]
    assert any(e["event_type"] == "AgentText" and e["data"] == "hi" for e in events)

    resp = federated_client.get(
        f"/federation/api/projects/{project_id}/sessions", headers=_auth()
    )
    summary = resp.get_json()
    assert summary["total"] == 1
    assert summary["sessions"][0]["id"] == session_id


def test_send_and_stop_on_long_running_fake_session(federated_app, federated_client):
    project_id, _ = create_project_with_repo(
        federated_client, federated_app.config["allowed_root"], repo_name="repo-b", project_name="Proj2"
    )

    resp = federated_client.post(
        f"/federation/api/projects/{project_id}/sessions",
        json={"agent_type": "fake", "script": [{"action": "message", "text": "start"}]},
        headers=_auth(),
    )
    session_id = resp.get_json()["id"]

    resp = federated_client.post(
        f"/federation/api/sessions/{session_id}/send", json={"prompt": "go on"}, headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "sent"

    resp = federated_client.post(f"/federation/api/sessions/{session_id}/stop", headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "stopped"

    resp = federated_client.get(f"/federation/api/sessions/{session_id}", headers=_auth())
    assert resp.get_json()["status"] == "STOPPED"
