import json
import os

import pytest

from app.config import Config
from app.agents.models import create_agent_session, add_agent_event, list_agent_sessions_for_project


def test_list_sessions_page(client):
    """Test the sessions list page loads"""
    resp = client.get("/sessions")
    assert resp.status_code == 200
    assert b"Sessions" in resp.data
    assert b"Select a project" in resp.data


def test_project_sessions_page(client, app):
    """Test the project sessions page"""
    # Create a project
    resp = client.post(
        "/projects/new",
        data={"name": "Test Project", "description": "A test"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # Check project sessions page
    resp = client.get("/sessions/project/1")
    assert resp.status_code == 200
    assert b"Test Project" in resp.data
    assert b"Start New Session" in resp.data


def test_project_sessions_unknown_project(client):
    """Test accessing sessions for unknown project returns 404"""
    resp = client.get("/sessions/project/999")
    assert resp.status_code == 404


def test_create_session_no_repositories(client):
    """Test creating session when project has no repositories"""
    client.post(
        "/projects/new",
        data={"name": "Test Project", "description": ""},
    )

    resp = client.post(
        "/sessions/project/1/create",
        data={"agent_type": "fake"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Project has no repositories" in resp.data


def test_create_session_with_repository(client, app):
    """Test creating a session with a repository"""
    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "test-repo")
    os.makedirs(repo_path, exist_ok=True)

    # Create project
    client.post(
        "/projects/new",
        data={
            "name": "Test Project",
            "description": "",
            "repo_name": "main",
            "repo_path": repo_path,
        },
    )

    # Create session
    resp = client.post(
        "/sessions/project/1/create",
        data={"agent_type": "fake"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Test Project" in resp.data


def test_view_session_unknown(client):
    """Test viewing unknown session returns 404"""
    resp = client.get("/sessions/999")
    assert resp.status_code == 404


def test_view_session_chat_interface(client, app):
    """Test the chat interface loads"""
    from app.db import get_db
    from app.projects import models as project_models

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "test-repo")
    os.makedirs(repo_path, exist_ok=True)

    # Create project with repo
    resp = client.post(
        "/projects/new",
        data={
            "name": "Test Project",
            "description": "",
            "repo_name": "main",
            "repo_path": repo_path,
        },
    )

    with app.app_context():
        db = get_db()
        project = project_models.get_project(db, 1)
        repo = project.repositories[0] if project.repositories else None

        session_id = create_agent_session(
            db, 1, "fake", execution_target=repo_path, metadata={"repo_id": repo.id if repo else 1}
        )

        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        assert b"Chat" in resp.data or b"chat" in resp.data.lower()
        assert b"Test Project" in resp.data


def test_send_prompt_missing_prompt(client, app):
    """Test sending empty prompt"""
    from app.db import get_db
    from app.projects import models as project_models

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "test-repo")
    os.makedirs(repo_path, exist_ok=True)

    with app.app_context():
        db = get_db()
        project_id = project_models.create_project(db, "Test Project", "")
        project_models.add_repository(db, project_id, "main", repo_path, (allowed_root,), is_primary=True)

        session_id = create_agent_session(
            db, project_id, "fake", execution_target=repo_path, metadata={"repo_id": 1}
        )

        resp = client.post(
            f"/sessions/{session_id}/send",
            data={"prompt": ""},
        )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "error" in data


def test_send_prompt_unknown_session(client):
    """Test sending prompt to unknown session"""
    resp = client.post(
        "/sessions/999/send",
        data={"prompt": "Hello"},
    )
    assert resp.status_code == 404


def test_stream_output_unknown_session(client):
    """Test streaming output from unknown session"""
    resp = client.get("/sessions/999/stream?after_id=0")
    assert resp.status_code == 404


def test_stream_output_with_events(client, app):
    """Test streaming output returns events"""
    from app.db import get_db
    from app.projects import models as project_models

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "test-repo")
    os.makedirs(repo_path, exist_ok=True)

    with app.app_context():
        db = get_db()
        project_id = project_models.create_project(db, "Test Project", "")
        project_models.add_repository(db, project_id, "main", repo_path, (allowed_root,), is_primary=True)

        session_id = create_agent_session(
            db, project_id, "fake", execution_target=repo_path, metadata={"repo_id": 1}
        )

        # Add some events
        add_agent_event(db, session_id, "output", "Hello, world!")
        add_agent_event(db, session_id, "output", "Response from agent")

        resp = client.get(f"/sessions/{session_id}/stream?after_id=0")
        assert resp.status_code == 200
        assert b"Hello, world!" in resp.data
        assert b"Response from agent" in resp.data


def test_stream_output_after_id_filter(client, app):
    """Test streaming output with after_id filters old events"""
    from app.db import get_db
    from app.projects import models as project_models

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "test-repo")
    os.makedirs(repo_path, exist_ok=True)

    with app.app_context():
        db = get_db()
        project_id = project_models.create_project(db, "Test Project", "")
        project_models.add_repository(db, project_id, "main", repo_path, (allowed_root,), is_primary=True)

        session_id = create_agent_session(
            db, project_id, "fake", execution_target=repo_path, metadata={"repo_id": 1}
        )

        # Add events
        event1_id = add_agent_event(db, session_id, "output", "First message")
        event2_id = add_agent_event(db, session_id, "output", "Second message")

        # Stream only events after the first
        resp = client.get(f"/sessions/{session_id}/stream?after_id={event1_id}")
        assert resp.status_code == 200
        assert b"First message" not in resp.data
        assert b"Second message" in resp.data


def test_stream_and_stop_use_claude_adapter_not_fake(client, app, monkeypatch):
    """Regression test: `/stream` and `/stop` used to hand-roll their own
    agent-type check that only special-cased "codex", silently falling back
    to FakeAgentAdapter for a "claude" session. FakeAgentAdapter.stream()
    never runs `_sync_events()` (it has no real process to sync), so a real
    Claude turn's reply would never surface after the first poll, and
    FakeAgentAdapter.stop() never terminates the real subprocess. Both
    routes must go through `_adapter_for`, which does handle "claude"."""
    from app.db import get_db
    from app.projects import models as project_models
    from app.agents.claude import ClaudeAdapter
    from app.agents.fake import FakeAgentAdapter

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "claude-adapter-routing")
    os.makedirs(repo_path, exist_ok=True)

    with app.app_context():
        db = get_db()
        project_id = project_models.create_project(db, "Claude Routing Test", "")
        project_models.add_repository(db, project_id, "main", repo_path, (allowed_root,), is_primary=True)

        session_id = create_agent_session(
            db, project_id, "claude", execution_target="1", metadata={"working_directory": repo_path}
        )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("FakeAgentAdapter must not be used for a claude session")

    monkeypatch.setattr(FakeAgentAdapter, "stream", fail_if_called)
    monkeypatch.setattr(FakeAgentAdapter, "stop", fail_if_called)
    monkeypatch.setattr(ClaudeAdapter, "stream", lambda self, session_id, after_id=None: [])
    monkeypatch.setattr(ClaudeAdapter, "stop", lambda self, session_id: None)

    resp = client.get(f"/sessions/{session_id}/stream?after_id=0")
    assert resp.status_code == 200

    # stop_session wraps the adapter call in try/except and flashes the
    # exception message rather than raising, so check the flash instead of
    # just the status code (which is 200 either way).
    resp = client.post(f"/sessions/{session_id}/stop", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Error stopping session" not in resp.data
    assert b"Session stopped." in resp.data


def test_stop_session_unknown(client):
    """Test stopping unknown session"""
    resp = client.post(
        "/sessions/999/stop",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Sessions" in resp.data


def test_stop_session_redirects(client, app):
    """Test stopping session redirects back to session view"""
    from app.db import get_db
    from app.projects import models as project_models

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "test-repo")
    os.makedirs(repo_path, exist_ok=True)

    with app.app_context():
        db = get_db()
        project_id = project_models.create_project(db, "Test Project", "")
        project_models.add_repository(db, project_id, "main", repo_path, (allowed_root,), is_primary=True)

        session_id = create_agent_session(
            db, project_id, "fake", execution_target=repo_path, metadata={"repo_id": 1}
        )

        resp = client.post(
            f"/sessions/{session_id}/stop",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Should redirect back to session view
        assert b"Test Project" in resp.data or b"Chat" in resp.data or b"chat" in resp.data.lower()


def test_stop_session_ajax_returns_json(client, app):
    """The composer's Stop button (chat.html, `data-ajax-action`) needs a
    JSON reply, not a redirect, per CLAUDE.md's AJAX-action convention --
    the generic handler in app.js only reloads the page on success and
    flashes `data.error` on failure, so a non-JSON body would appear as
    "The server sent an unexpected reply" instead of the real error."""
    from app.db import get_db
    from app.projects import models as project_models

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "stop-ajax-repo")
    os.makedirs(repo_path, exist_ok=True)

    with app.app_context():
        db = get_db()
        project_id = project_models.create_project(db, "Stop AJAX Test", "")
        project_models.add_repository(db, project_id, "main", repo_path, (allowed_root,), is_primary=True)
        session_id = create_agent_session(
            db, project_id, "fake", execution_target=repo_path, metadata={"repo_id": 1}
        )

    resp = client.post(
        f"/sessions/{session_id}/stop",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "stopped"

    resp = client.post(
        "/sessions/999/stop",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 404
    assert "error" in json.loads(resp.data)


def test_delete_session_unknown(client):
    """Deleting an unknown session redirects to the sessions list without error"""
    resp = client.post(
        "/sessions/999/delete",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Sessions" in resp.data


def test_delete_session_removes_session_and_events(client, app):
    """Deleting a session removes it (and its events) and redirects to the project view"""
    from app.db import get_db
    from app.agents.models import get_agent_session, list_agent_events
    from app.projects import models as project_models

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "delete-test-repo")
    os.makedirs(repo_path, exist_ok=True)

    with app.app_context():
        db = get_db()
        project_id = project_models.create_project(db, "Delete Test Project", "")
        project_models.add_repository(db, project_id, "main", repo_path, (allowed_root,), is_primary=True)

        session_id = create_agent_session(
            db, project_id, "fake", execution_target=repo_path, metadata={"repo_id": 1}
        )
        add_agent_event(db, session_id, "AgentText", data="hello")

        resp = client.post(
            f"/sessions/{session_id}/delete",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Delete Test Project" in resp.data

        assert get_agent_session(db, session_id) is None
        assert list_agent_events(db, session_id, after_id=0) == []


def test_create_session_form_parsing(client, app):
    """Test that session creation properly handles repo_id form parameter"""
    from app.db import get_db
    from app.projects import models as project_models

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "form-test-repo")
    os.makedirs(repo_path, exist_ok=True)

    # Create project with repository
    resp = client.post(
        "/projects/new",
        data={
            "name": "FormTest",
            "description": "Test form parsing",
            "repo_name": "main",
            "repo_path": repo_path,
        },
    )
    assert resp.status_code == 302

    # Get the project
    with app.app_context():
        db = get_db()
        projects = project_models.list_projects(db)
        project = [p for p in projects if p.name == "FormTest"][0]
        repo = project.repositories[0]
        print(f"DEBUG: Project ID: {project.id}, Repo ID: {repo.id}, Path: {repo.path}")

    # Test 1: POST with repo_id as integer string (normal case)
    print(f"DEBUG: Submitting form with repo_id={repo.id}")
    resp = client.post(
        f"/sessions/project/{project.id}/create",
        data={"agent_type": "fake", "repo_id": str(repo.id)},
        follow_redirects=True,
    )
    print(f"DEBUG: Response status: {resp.status_code}")
    print(f"DEBUG: Response body (first 500 chars):\n{resp.data[:500]}")
    assert resp.status_code == 200

    # Test 2: POST without repo_id (should use primary)
    print(f"DEBUG: Submitting form without repo_id")
    resp = client.post(
        f"/sessions/project/{project.id}/create",
        data={"agent_type": "fake"},
        follow_redirects=True,
    )
    print(f"DEBUG: Response status: {resp.status_code}")
    assert resp.status_code == 200

    # Test 3: POST with repo_id as path (edge case that might cause the error)
    print(f"DEBUG: Submitting form with repo_id={repo.path}")
    resp = client.post(
        f"/sessions/project/{project.id}/create",
        data={"agent_type": "fake", "repo_id": repo.path},
        follow_redirects=True,
    )
    print(f"DEBUG: Response status: {resp.status_code}")
    # This should NOT crash, should gracefully fall back
    assert resp.status_code == 200


def test_codex_session_with_chat_interaction(client, app):
    """Test creating and interacting with a Codex session"""
    from app.db import get_db
    from app.projects import models as project_models

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "SAM6")
    os.makedirs(repo_path, exist_ok=True)

    # Create project with repository in one step
    resp = client.post(
        "/projects/new",
        data={
            "name": "SAM6",
            "description": "Test project for Codex chat",
            "repo_name": "main",
            "repo_path": repo_path,
        },
    )
    assert resp.status_code == 302

    # Get the created project
    with app.app_context():
        db = get_db()
        projects = project_models.list_projects(db)
        sam6_projects = [p for p in projects if p.name == "SAM6"]
        assert len(sam6_projects) > 0
        project = sam6_projects[0]
        assert len(project.repositories) > 0

    # Start a Codex session via the form
    resp = client.post(
        f"/sessions/project/{project.id}/create",
        data={"agent_type": "codex"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # Get the session ID from the database
    with app.app_context():
        db = get_db()
        sessions = list_agent_sessions_for_project(db, project.id)
        assert len(sessions) > 0
        session = sessions[0]
        session_id = session.id

        # Verify session is initialized with Codex. `start()` runs the codex
        # process synchronously, so by the time it returns the turn may
        # already have finished (successfully or not) rather than still be
        # RUNNING/STARTING.
        assert session.agent_type == "codex"
        assert session.status in ("RUNNING", "STARTING", "COMPLETED", "FAILED")

    # View the chat interface
    resp = client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    assert b"Chat" in resp.data or b"chat" in resp.data.lower()

    # Send a test prompt
    resp = client.post(
        f"/sessions/{session_id}/send",
        data={"prompt": "Test"},
    )
    # May return 200 with {"status": "sent"} or 400 if Codex is unavailable (that's OK)
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        data = json.loads(resp.data)
        assert data.get("status") == "sent"

    # Stream output (verify endpoint works)
    resp = client.get(f"/sessions/{session_id}/stream?after_id=0")
    assert resp.status_code == 200
    # Should contain some event data
    assert len(resp.data) > 0


def test_create_session_mcp_tools_opt_in_recorded(client, app):
    """The `mcp_tools` checkbox (docs/AGENT_ADAPTER.md, MCP tool bridge
    section) must reach both the adapter's session metadata (so `resume()`/
    `send()` keep declaring the MCP server on later turns) and the audit
    trail in `injected_context` -- and default to off when unchecked."""
    from app.agents.models import get_agent_session

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "mcp-test-repo")
    os.makedirs(repo_path, exist_ok=True)

    client.post(
        "/projects/new",
        data={
            "name": "MCP Test",
            "description": "",
            "repo_name": "main",
            "repo_path": repo_path,
        },
    )

    resp = client.post(
        "/sessions/project/1/create",
        data={"agent_type": "fake", "mcp_tools": "1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        from app.db import get_db
        db = get_db()
        session = get_agent_session(db, 1)
        assert session.metadata["mcp_tools"] is True
        assert session.metadata["injected_context"]["mcp_tools"] is True

    resp = client.post(
        "/sessions/project/1/create",
        data={"agent_type": "fake"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        db = get_db()
        session = get_agent_session(db, 2)
        assert session.metadata["mcp_tools"] is False


def test_fake_adapter_tool_call_step_writes_backlog_item(app):
    """Exercises the `tool_call` script step end to end: a scripted "agent"
    turn calls the same `app/mcp/tools/backlog` functions the real MCP
    server (`app/mcp/server.py`) wraps, and the write lands in the real
    Backlog tables -- proving the `options["mcp_tools"]` -> tool-call path
    works without needing a real Codex/Claude CLI to spawn a subprocess."""
    from app.agents.base import AgentContext
    from app.agents.fake import FakeAgentAdapter
    from app.backlog import persistence as backlog_persistence
    from app.db import get_db

    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        db.commit()

        adapter = FakeAgentAdapter(db)
        context = AgentContext(project_id=1, working_directory=".")
        script = [
            {
                "action": "tool_call",
                "tool": "backlog_create_item",
                "args": {"title": "Found in chat", "priority": "high"},
            },
            {"action": "complete"},
        ]
        session = adapter.start(context, "plan the backlog", options={"mcp_tools": True, "script": script})

        assert session.status == "COMPLETED"
        items = backlog_persistence.list_items(db, 1)
        assert len(items) == 1
        assert items[0].title == "Found in chat"
        assert items[0].created_by == f"agent:{session.id}"


def test_fake_adapter_tool_call_step_requires_mcp_tools_opt_in(app):
    """A scripted `tool_call` step must fail loudly (not silently write)
    when the session never opted into `mcp_tools` -- mirrors a real CLI
    having no MCP server declared at all when the checkbox was off."""
    from app.agents.base import AgentContext
    from app.agents.fake import FakeAgentAdapter
    from app.db import get_db

    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        db.commit()

        adapter = FakeAgentAdapter(db)
        context = AgentContext(project_id=1, working_directory=".")
        script = [{"action": "tool_call", "tool": "backlog_create_item", "args": {}}]
        with pytest.raises(ValueError):
            adapter.start(context, "plan the backlog", options={"script": script})
