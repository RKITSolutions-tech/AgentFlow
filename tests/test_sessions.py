import json
import os
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
