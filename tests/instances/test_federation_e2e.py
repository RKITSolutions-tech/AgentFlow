"""Two real AgentFlow instances talking over HTTP: one registers the other
as a remote instance (app/instances/) and drives a session on it through
that instance's federation API (app/federation/), exactly like the Linux
devserver -> Windows server scenario this feature was built for -- just
both loopback here, since that's all a test can rely on.
"""

import threading

import pytest
from werkzeug.serving import make_server

from app import create_app
from app.config import Config
from app.db import get_db
from app.instances import models as instance_models
from tests.conftest import create_project_with_repo


class _ServerThread(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self._server = make_server("127.0.0.1", 0, app)
        self.port = self._server.server_port

    def run(self):
        self._server.serve_forever()

    def shutdown(self):
        self._server.shutdown()


def _make_app(tmp_path, name, federation_token=""):
    allowed_root = tmp_path / name / "projects"
    allowed_root.mkdir(parents=True)
    config = Config(
        DATABASE_PATH=str(tmp_path / name / "db.sqlite3"),
        SECRET_KEY="test-secret",
        ALLOWED_PROJECT_ROOTS=(str(allowed_root),),
        TESTING=True,
        FEDERATION_TOKEN=federation_token,
    )
    application = create_app(config)
    application.config["allowed_root"] = str(allowed_root)
    return application


@pytest.fixture
def remote(tmp_path):
    """The instance "on the Windows server": has a project/repo and exposes
    its federation API with a token."""
    app = _make_app(tmp_path, "remote", federation_token="shared-secret")
    client = app.test_client()
    project_id, repo_path = create_project_with_repo(client, app.config["allowed_root"], project_name="RemoteProj")

    thread = _ServerThread(app)
    thread.start()
    yield app, f"http://127.0.0.1:{thread.port}", project_id
    thread.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def master(tmp_path):
    """The instance the user actually drives -- the master control plane."""
    app = _make_app(tmp_path, "master")
    return app, app.test_client()


def test_master_lists_and_drives_remote_session(master, remote):
    master_app, master_client = master
    remote_app, remote_url, remote_project_id = remote

    with master_app.app_context():
        instance_id = instance_models.add_instance(get_db(), "remote-win", remote_url, "shared-secret")

    # Registry + liveness: the index page pings the remote and records status.
    resp = master_client.get("/instances")
    assert resp.status_code == 200
    assert b"remote-win" in resp.data
    assert b"ONLINE" in resp.data

    # Master sees the remote's project without visiting its UI directly.
    resp = master_client.get(f"/instances/{instance_id}/projects")
    assert resp.status_code == 200
    assert b"RemoteProj" in resp.data

    # Start a session on the remote, from the master.
    resp = master_client.post(
        f"/instances/{instance_id}/projects/{remote_project_id}/sessions",
        data={"agent_type": "fake", "mcp_tools": "0"},
    )
    assert resp.status_code == 302
    session_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])

    # It really lives on the remote's own database, not the master's.
    with remote_app.app_context():
        from app.agents.models import get_agent_session

        remote_session = get_agent_session(get_db(), session_id)
        assert remote_session is not None
        assert remote_session.agent_type == "fake"
    with master_app.app_context():
        from app.agents.models import get_agent_session

        assert get_agent_session(get_db(), session_id) is None

    # Chat view renders via the proxy.
    resp = master_client.get(f"/instances/{instance_id}/sessions/{session_id}")
    assert resp.status_code == 200

    # Send a prompt through the master; it reaches the remote's adapter.
    resp = master_client.post(
        f"/instances/{instance_id}/sessions/{session_id}/send", data={"prompt": "hello from master"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "sent"

    with remote_app.app_context():
        from app.agents.models import list_agent_events

        events = list_agent_events(get_db(), session_id)
        assert any(e.event_type == "PromptSubmitted" and e.data == "hello from master" for e in events)

    # Streaming proxies the remote's events back as the same SSE-ish body
    # the local chat template already knows how to parse.
    resp = master_client.get(f"/instances/{instance_id}/sessions/{session_id}/stream?after_id=0")
    assert resp.status_code == 200
    assert b"data: " in resp.data
    assert b"hello from master" in resp.data

    # Stop proxies too.
    resp = master_client.post(f"/instances/{instance_id}/sessions/{session_id}/stop")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "stopped"
    with remote_app.app_context():
        from app.agents.models import get_agent_session

        assert get_agent_session(get_db(), session_id).status == "STOPPED"


def test_master_handles_unreachable_instance(master):
    master_app, master_client = master
    with master_app.app_context():
        instance_id = instance_models.add_instance(
            get_db(), "offline", "http://127.0.0.1:1", "whatever-token"
        )

    resp = master_client.get("/instances")
    assert resp.status_code == 200
    assert b"UNREACHABLE" in resp.data

    resp = master_client.get(f"/instances/{instance_id}/projects", follow_redirects=True)
    assert resp.status_code == 200
    # Falls back to the registry page with a flashed error, not a crash.
    assert b"Remote Instances" in resp.data
