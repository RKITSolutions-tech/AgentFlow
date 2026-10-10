import pytest
from bs4 import BeautifulSoup

from app.agents import models as agent_models
from app.backlog import persistence as backlog_persistence
from app.db import get_db
from app.pipelines import executions as pipeline_executions
from app.pipelines import persistence as pipeline_persistence


def _dashboard_section(client, testid):
    resp = client.get("/dashboards/home")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.data, "html.parser")
    return soup.find(attrs={"data-testid": testid})


@pytest.fixture
def project_id(app):
    with app.app_context():
        conn = get_db()
        cur = conn.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        conn.commit()
        return cur.lastrowid


def test_home_dashboard_empty_state(client):
    assert "No recent sessions." in _dashboard_section(client, "recent-sessions").get_text()
    assert "No active pipelines." in _dashboard_section(client, "active-pipelines").get_text()
    assert "No recent backlog items." in _dashboard_section(client, "recent-backlog").get_text()


def test_home_dashboard_shows_recent_items(app, client, project_id):
    with app.app_context():
        db = get_db()
        session_id = agent_models.create_agent_session(
            db, project_id, "fake", role="GENERAL", metadata={"title": "Investigate flaky test"}
        )
        pipeline = pipeline_persistence.list_pipelines(db)[0]
        execution_id = pipeline_executions.create_execution(
            db, pipeline.id, pipeline.current_version, project_id, elements=[]
        )
        pipeline_executions.update_execution(db, execution_id, status="RUNNING")
        item_id = backlog_persistence.create_item(
            db, project_id, text="do thing", title="Fix the widget"
        )

    sessions = _dashboard_section(client, "recent-sessions")
    pipelines = _dashboard_section(client, "active-pipelines")
    backlog = _dashboard_section(client, "recent-backlog")

    assert "Investigate flaky test" in sessions.get_text()
    assert sessions.find("a", href=f"/sessions/{session_id}") is not None

    assert pipeline.name in pipelines.get_text()
    assert pipelines.find(
        "a", href=f"/projects/{project_id}/pipelines/executions/{execution_id}"
    ) is not None

    assert "Fix the widget" in backlog.get_text()
    assert backlog.find(
        "a", href=f"/projects/{project_id}/backlog/items/{item_id}"
    ) is not None


def test_home_dashboard_excludes_non_general_sessions_and_inactive_pipelines(app, client, project_id):
    with app.app_context():
        db = get_db()
        agent_models.create_agent_session(
            db, project_id, "fake", role="PLANNING", metadata={"title": "Plan sprint"}
        )
        pipeline = pipeline_persistence.list_pipelines(db)[0]
        execution_id = pipeline_executions.create_execution(
            db, pipeline.id, pipeline.current_version, project_id, elements=[]
        )
        pipeline_executions.update_execution(db, execution_id, status="COMPLETED")

    sessions = _dashboard_section(client, "recent-sessions")
    pipelines = _dashboard_section(client, "active-pipelines")

    assert "Plan sprint" not in sessions.get_text()
    assert "No active pipelines." in pipelines.get_text()


def test_home_dashboard_limits_recent_sessions_to_five(app, client, project_id):
    with app.app_context():
        db = get_db()
        for i in range(6):
            agent_models.create_agent_session(
                db, project_id, "fake", role="GENERAL", metadata={"title": f"Session {i}"}
            )

    sessions = _dashboard_section(client, "recent-sessions")
    cards = sessions.find_all(attrs={"class": "dashboard-card"})
    assert len(cards) == 5
    assert "Session 0" not in sessions.get_text()
    assert "Session 5" in sessions.get_text()


def test_wikis_dashboard_loads(client):
    """Test wikis dashboard loads without errors."""
    resp = client.get("/dashboards/wikis")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(attrs={"data-testid": "dashboard-wikis"}) is not None


def test_wikis_dashboard_empty_state(client):
    """Test wikis dashboard shows empty state when no wikis exist."""
    resp = client.get("/dashboards/wikis")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.data, "html.parser")
    preview = soup.find(attrs={"data-testid": "wiki-preview"})
    assert "No wikis" in preview.get_text() or "wikis configured" in preview.get_text().lower()


def test_project_dashboard_loads(client, project_id):
    """Test project dashboard loads without errors."""
    resp = client.get(f"/dashboards/project/{project_id}")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(attrs={"data-testid": f"dashboard-project-{project_id}"}) is not None


def test_project_dashboard_shows_sections(client, project_id):
    """Test project dashboard shows all main sections."""
    resp = client.get(f"/dashboards/project/{project_id}")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.data, "html.parser")

    # Check for main sections
    assert soup.find(attrs={"data-testid": "current-sprint"}) is not None
    assert soup.find(attrs={"data-testid": "backlog-breakdown"}) is not None
    assert soup.find(attrs={"data-testid": "active-pipelines"}) is not None


def test_project_dashboard_no_sprint_empty_state(client, project_id):
    """Test project dashboard shows no active sprint message when none exists."""
    resp = client.get(f"/dashboards/project/{project_id}")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.data, "html.parser")
    sprint_section = soup.find(attrs={"data-testid": "current-sprint"})
    assert "No Active Sprint" in sprint_section.get_text()


def test_project_dashboard_with_backlog_items(app, client, project_id):
    """Test project dashboard displays backlog status summary."""
    with app.app_context():
        db = get_db()
        backlog_persistence.create_item(db, project_id, text="Task 1", title="First task")
        backlog_persistence.create_item(db, project_id, text="Task 2", title="Second task")

        # Mark one as triaged
        items = backlog_persistence.list_items(db)
        if len(items) > 0:
            backlog_persistence.transition(db, items[0].id, "TRIAGED")

    resp = client.get(f"/dashboards/project/{project_id}")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.data, "html.parser")
    backlog_section = soup.find(attrs={"data-testid": "backlog-breakdown"})

    # Should show backlog items
    assert backlog_section is not None
    text = backlog_section.get_text()
    # Check for status indicators (count > 0)
    assert "1" in text or "2" in text


def test_project_dashboard_with_active_pipeline(app, client, project_id):
    """Test project dashboard shows active pipelines."""
    with app.app_context():
        db = get_db()
        pipeline = pipeline_persistence.list_pipelines(db)[0]
        execution_id = pipeline_executions.create_execution(
            db, pipeline.id, pipeline.current_version, project_id, elements=[]
        )
        pipeline_executions.update_execution(db, execution_id, status="RUNNING")

    resp = client.get(f"/dashboards/project/{project_id}")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.data, "html.parser")
    pipelines_section = soup.find(attrs={"data-testid": "active-pipelines"})

    assert pipeline.name in pipelines_section.get_text()
    assert "RUNNING" in pipelines_section.get_text()
