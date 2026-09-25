"""End to end: an agent asks a question -> notification + sidebar cue -> the
session page shows the panel -> answering clears the notification."""
from __future__ import annotations

import pytest

from app.agents import models as agent_models
from app.agents.base import AgentContext
from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.notifications import models as notification_models
from tests.conftest import create_project_with_repo

SCRIPT = [
    {"action": "message", "text": "I need a decision."},
    {
        "action": "ask",
        "header": "Database",
        "question": "Which database?",
        "options": [{"label": "Postgres", "description": "Robust"}, {"label": "SQLite"}],
    },
]


def _start_asking_session(app, client, browser_notifications=True):
    project_id, path = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        notification_models.set_preference(
            db, "blocked_on_question", "browser", browser_notifications
        )
        session = FakeAgentAdapter(db).start(
            AgentContext(project_id=project_id, working_directory=path),
            "go",
            {"script": SCRIPT},
        )
        return session.id


def test_fake_agent_ask_step_notifies_and_marks_sidebar(app, client):
    session_id = _start_asking_session(app, client)
    with app.app_context():
        db = get_db()
        assert agent_models.list_clarifying_questions(db, session_id, status="PENDING")
        assert notification_models.unread_count(db) == 1

    html = client.get("/notifications").get_data(as_text=True)
    assert "state-waiting" in html and "Needs your input" in html
    assert 'data-notify-badge>' in html.replace("\n", "").replace("  ", "")


@pytest.mark.parametrize("viewport", [{"width": 375, "height": 700}, {"width": 1280, "height": 800}])
@pytest.mark.parametrize("browser_notifications", [True, False])
def test_full_flow_in_browser(
    browser_type_launch_args, app, client, live_server, viewport, browser_notifications
):
    sync_api = pytest.importorskip("playwright.sync_api")
    session_id = _start_asking_session(app, client, browser_notifications)

    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser binary missing / cannot start
            pytest.skip(f"Playwright browser unavailable: {exc}")
        try:
            context = browser.new_context(viewport=viewport)
            page = context.new_page()

            # Notification centre lists it as unread; opening it goes to the session.
            page.goto(f"{live_server}/notifications")
            assert page.locator(".notify-item.is-unread").count() == 1
            page.locator(".notify-item a").click()
            page.wait_for_url(f"**/sessions/{session_id}")

            # The session page shows the docked panel and question, persisted
            # across a reload.
            for attempt in range(2):
                if attempt:
                    page.reload()
                page.locator("#questionPanel").wait_for(state="visible")
                assert page.locator("#questionText").inner_text() == "Which database?"
                assert page.locator(".question-pending .question-badge").inner_text() == "Running"

            page.keyboard.press("1")
            page.locator("#questionSubmit").click()
            page.locator(".question-answered").wait_for()

            # Answering resolves the blocker, so the notification is read.
            page.goto(f"{live_server}/notifications")
            assert page.locator(".notify-item.is-unread").count() == 0
            assert page.locator("[data-notify-badge]").is_hidden()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
        finally:
            browser.close()

    with app.app_context():
        db = get_db()
        channels = {n.channel for n in _all_notifications(db)}
        assert channels == ({"in_app", "browser"} if browser_notifications else {"in_app"})
        assert agent_models.list_agent_events(db, session_id)[-1].data == (
            'Answer to "Which database?": Postgres'
        )


def _all_notifications(db):
    return [
        notification_models.Notification(**dict(r))
        for r in db.execute("SELECT * FROM notifications")
    ]
