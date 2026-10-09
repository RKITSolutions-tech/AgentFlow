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
