import pytest

from app.agents import models as agent_models
from app.agents.research_report import Finding, ResearchReport, Source
from app.db import get_db
from app.knowledge import ingestion, models
from app.projects import models as project_models


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


@pytest.fixture
def session(db):
    """A real project + research_sessions row: source_project_id/
    source_session_id are foreign keys, enforced (PRAGMA foreign_keys = ON,
    app/db.py's get_db), so ingestion must reference rows that really exist
    -- exactly what a real ResearchAgent.research() call always has."""
    project_id = project_models.create_project(db, "Demo")
    session_id = agent_models.create_research_session(db, project_id, "question")
    return project_id, session_id


def test_sourced_finding_becomes_unverified_entry(db, session):
    project_id, session_id = session
    report = ResearchReport(
        summary="Auth uses JWT",
        findings=[Finding(text="Auth is implemented with JWT tokens", source_ids=["S1"], category="auth")],
        sources=[Source(id="S1", file_path="app/auth.py", line_range="1-10", excerpt="jwt code")],
    )
    created = ingestion.ingest_research_report(db, project_id, session_id, report)
    assert len(created) == 1
    entry = models.get_entry(db, created[0])
    assert entry.confidence == "unverified"
    assert entry.source_project_id == project_id
    assert entry.source_session_id == session_id
    assert "auth.py" in entry.content
    assert entry.tags == ["auth"]


def test_unsourced_finding_is_skipped(db, session):
    project_id, session_id = session
    report = ResearchReport(
        summary="s",
        findings=[Finding(text="An unverified guess", source_ids=[])],
        sources=[],
    )
    created = ingestion.ingest_research_report(db, project_id, session_id, report)
    assert created == []
