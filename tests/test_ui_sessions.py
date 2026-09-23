"""UI and integration tests for sessions using BeautifulSoup and Playwright."""
import json
import os
from bs4 import BeautifulSoup


class TestSessionsUIWithBeautifulSoup:
    """Tests that parse and validate HTML structure with BeautifulSoup."""

    def test_sessions_page_structure(self, client):
        """Verify sessions page has correct HTML structure."""
        resp = client.get("/sessions")
        assert resp.status_code == 200

        soup = BeautifulSoup(resp.data, "html.parser")

        # Check page title
        assert soup.title.string == "Sessions - AgentFlow"

        # Check main heading
        h1 = soup.find("h1")
        assert h1 is not None
        assert "Sessions" in h1.text

        # Check for project table
        table = soup.find("table")
        assert table is not None
        assert soup.find("thead") is not None

    def test_project_sessions_page_form_fields(self, client, app):
        """Verify project sessions page has correct form fields."""
        from app.projects import models as project_models
        from app.db import get_db

        allowed_root = app.config["allowed_root"]
        repo_path = os.path.join(allowed_root, "test-form-fields")
        os.makedirs(repo_path, exist_ok=True)

        # Create project
        client.post(
            "/projects/new",
            data={
                "name": "FormTest",
                "description": "Test",
                "repo_name": "main",
                "repo_path": repo_path,
            },
        )

        with app.app_context():
            db = get_db()
            projects = project_models.list_projects(db)
            project = [p for p in projects if p.name == "FormTest"][0]

        # Get sessions page
        resp = client.get(f"/sessions/project/{project.id}")
        assert resp.status_code == 200

        soup = BeautifulSoup(resp.data, "html.parser")

        # Check for "Start New Session" section
        headings = soup.find_all("h2")
        session_section = any("Start New Session" in h.text for h in headings)
        assert session_section, "Start New Session section not found"

        # Check for agent type dropdown
        agent_select = soup.find("select", {"name": "agent_type"})
        assert agent_select is not None, "Agent type select not found"

        # Check for repo dropdown
        repo_select = soup.find("select", {"name": "repo_id"})
        assert repo_select is not None, "Repository select not found"

        # Check for form button
        buttons = soup.find_all("button")
        submit_btn = any("Start Session" in b.text for b in buttons)
        assert submit_btn, "Start Session button not found"

    def test_chat_interface_layout(self, client, app):
        """Verify chat interface has correct layout with BeautifulSoup."""
        from app.db import get_db
        from app.projects import models as project_models
        from app.agents.models import create_agent_session

        allowed_root = app.config["allowed_root"]
        repo_path = os.path.join(allowed_root, "chat-layout-test")
        os.makedirs(repo_path, exist_ok=True)

        # Create project and session
        client.post(
            "/projects/new",
            data={
                "name": "ChatTest",
                "description": "Test",
                "repo_name": "main",
                "repo_path": repo_path,
            },
        )

        with app.app_context():
            db = get_db()
            projects = project_models.list_projects(db)
            project = [p for p in projects if p.name == "ChatTest"][0]
            repo = project.repositories[0]

            session_id = create_agent_session(
                db, project.id, "fake", execution_target=repo_path, metadata={"repo_id": repo.id}
            )

        # Get chat page
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200

        soup = BeautifulSoup(resp.data, "html.parser")

        # Check for chat container
        chat_container = soup.find("div", {"id": "chatContainer"})
        assert chat_container is not None, "Chat container not found"

        # Check for prompt input
        prompt_input = soup.find("input", {"id": "promptInput"})
        assert prompt_input is not None, "Prompt input not found"
        assert prompt_input.get("placeholder") is not None, "Input should have placeholder"

        # Check for send button
        buttons = soup.find_all("button")
        send_btn = any("Send" in b.text for b in buttons)
        assert send_btn, "Send button not found"

        # Check for back link
        links = soup.find_all("a")
        back_link = any("Back" in l.text or "back" in l.text.lower() for l in links)
        assert back_link, "Back link not found"

    def test_chat_form_validation(self, client, app):
        """Verify chat form has proper input validation attributes."""
        from app.db import get_db
        from app.projects import models as project_models
        from app.agents.models import create_agent_session

        allowed_root = app.config["allowed_root"]
        repo_path = os.path.join(allowed_root, "form-validation-test")
        os.makedirs(repo_path, exist_ok=True)

        client.post(
            "/projects/new",
            data={
                "name": "ValidTest",
                "description": "Test",
                "repo_name": "main",
                "repo_path": repo_path,
            },
        )

        with app.app_context():
            db = get_db()
            projects = project_models.list_projects(db)
            project = [p for p in projects if p.name == "ValidTest"][0]
            repo = project.repositories[0]

            session_id = create_agent_session(
                db, project.id, "fake", execution_target=repo_path, metadata={"repo_id": repo.id}
            )

        resp = client.get(f"/sessions/{session_id}")
        soup = BeautifulSoup(resp.data, "html.parser")

        # Check input field attributes
        prompt_input = soup.find("input", {"id": "promptInput"})
        assert prompt_input.get("type") == "text"
        assert prompt_input.get("placeholder") is not None


class TestSessionsResponseContent:
    """Tests that verify response content and data integrity."""

    def test_sessions_list_displays_all_projects(self, client, app):
        """Verify all projects appear in sessions list."""
        from app.db import get_db
        from app.projects import models as project_models

        allowed_root = app.config["allowed_root"]

        # Create multiple projects
        for i in range(3):
            repo_path = os.path.join(allowed_root, f"project-{i}")
            os.makedirs(repo_path, exist_ok=True)
            client.post(
                "/projects/new",
                data={
                    "name": f"Project{i}",
                    "description": f"Test {i}",
                    "repo_name": "main",
                    "repo_path": repo_path,
                },
            )

        resp = client.get("/sessions")
        soup = BeautifulSoup(resp.data, "html.parser")

        # Check that all projects are in the table
        table_rows = soup.find_all("tr")
        # Header row + 3 data rows = 4 total
        assert len(table_rows) >= 4, f"Expected at least 4 rows, got {len(table_rows)}"

        # Check project names appear
        for i in range(3):
            assert f"Project{i}" in resp.data.decode()

    def test_chat_header_displays_correct_info(self, client, app):
        """Verify chat header shows project and session info."""
        from app.db import get_db
        from app.projects import models as project_models
        from app.agents.models import create_agent_session

        allowed_root = app.config["allowed_root"]
        repo_path = os.path.join(allowed_root, "header-test")
        os.makedirs(repo_path, exist_ok=True)

        client.post(
            "/projects/new",
            data={
                "name": "HeaderTest",
                "description": "Test",
                "repo_name": "main",
                "repo_path": repo_path,
            },
        )

        with app.app_context():
            db = get_db()
            projects = project_models.list_projects(db)
            project = [p for p in projects if p.name == "HeaderTest"][0]
            repo = project.repositories[0]

            session_id = create_agent_session(
                db, project.id, "fake", execution_target=repo_path, metadata={"repo_id": repo.id}
            )

        resp = client.get(f"/sessions/{session_id}")
        soup = BeautifulSoup(resp.data, "html.parser")

        # Check project name in header
        h2 = soup.find("h2")
        assert h2 is not None
        assert "HeaderTest" in h2.text, f"Project name not found in header: {h2.text}"

        # Check for status info
        status_text = resp.data.decode()
        assert "Status:" in status_text, "Status not displayed"
