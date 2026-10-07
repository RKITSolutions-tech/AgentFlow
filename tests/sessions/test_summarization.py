from datetime import datetime, timedelta, timezone

import pytest

from app.agents.base import AgentContext
from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.sessions import chat, summarization


@pytest.fixture
def db(app):
    app.config["PLANNING_AGENT"] = "fake"
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        conn.commit()
        yield conn


def _old_message(db, project_id, text, days_ago):
    adapter = FakeAgentAdapter(db)
    session = adapter.start(
        AgentContext(project_id=project_id, working_directory="."), "hi",
        options={"role": "GENERAL", "script": [{"action": "message", "text": text}]},
    )
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE agent_events SET created_at = ? WHERE session_id = ?", (stamp, session.id))
    db.commit()
    return session.id


def test_summarize_project_groups_by_day_and_skips_recent_and_too_old(app, db):
    _old_message(db, 1, "within window", days_ago=5)
    _old_message(db, 1, "too recent", days_ago=0)  # < 24h: not eligible yet
    _old_message(db, 1, "too old", days_ago=40)  # > 30d: already past the archive window

    new_ids = summarization.summarize_project(db, app.config, project_id=1, provider=None)
    assert len(new_ids) == 1
    summaries = chat.list_summaries_for_project(db, 1)
    assert len(summaries) == 1
    # "within window"'s session also wrote the developer's initial "hi" prompt
    # at the same (rewritten) timestamp, so both count toward this day's total.
    assert summaries[0].message_count == 2
    assert "messages exchanged" in summaries[0].summary


def test_summarize_project_is_idempotent(app, db):
    _old_message(db, 1, "hello", days_ago=5)
    first = summarization.summarize_project(db, app.config, project_id=1, provider=None)
    second = summarization.summarize_project(db, app.config, project_id=1, provider=None)
    assert len(first) == 1
    assert second == []
    assert len(chat.list_summaries_for_project(db, 1)) == 1


def test_summarize_project_separates_distinct_days(app, db):
    _old_message(db, 1, "day one", days_ago=5)
    _old_message(db, 1, "day two", days_ago=6)
    new_ids = summarization.summarize_project(db, app.config, project_id=1, provider=None)
    assert len(new_ids) == 2


def test_summarize_project_no_eligible_messages(app, db):
    assert summarization.summarize_project(db, app.config, project_id=1, provider=None) == []


def test_run_for_all_projects(app, db, tmp_path):
    _old_message(db, 1, "hello", days_ago=5)
    results = summarization.run_for_all_projects(app.config, db_path=app.config["DATABASE_PATH"])
    assert results.get(1) and len(results[1]) == 1
