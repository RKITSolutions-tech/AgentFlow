from __future__ import annotations

import json

import pytest

from app.agents import models as agent_models
from app.db import get_db
from tests.conftest import create_project_with_repo

OPTIONS = [
    {"label": "Postgres (Recommended)", "description": "Robust"},
    {"label": "SQLite", "description": "Simple"},
]


def _session_with_question(app, client, multi_select=False):
    project_id, _ = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        session_id = agent_models.create_agent_session(db, project_id, "fake")
        q = agent_models.create_clarifying_question(
            db, session_id, "Which database?", OPTIONS, header="Database", multi_select=multi_select
        )
        return session_id, q.id


def _stream(client, session_id):
    body = client.get(f"/sessions/{session_id}/stream?after_id=0").get_data(as_text=True)
    return [json.loads(l[6:]) for l in body.splitlines() if l.startswith("data: ")]


def test_stream_sends_question_state_with_other_option(app, client):
    session_id, _ = _session_with_question(app, client)
    (event,) = _stream(client, session_id)
    assert event["event_type"] == "ClarifyingQuestion"
    data = json.loads(event["data"])
    assert data["status"] == "PENDING"
    assert data["header"] == "Database"
    assert [o["label"] for o in data["options"]][-1] == agent_models.OTHER_OPTION_LABEL


def test_answer_records_and_delivers_to_agent(app, client):
    session_id, qid = _session_with_question(app, client)
    resp = client.post(
        f"/sessions/{session_id}/questions/{qid}/answer", data={"selected": "SQLite"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "answered"
    with app.app_context():
        events = agent_models.list_agent_events(get_db(), session_id)
    assert events[-1].event_type == "PromptSubmitted"
    assert events[-1].data == 'Answer to "Which database?": SQLite'
    reloaded = json.loads(_stream(client, session_id)[0]["data"])
    assert reloaded["status"] == "ANSWERED"


def test_other_text_answer(app, client):
    session_id, qid = _session_with_question(app, client)
    resp = client.post(
        f"/sessions/{session_id}/questions/{qid}/answer", data={"other_text": "MySQL"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["question"]["answer"] == {"selected": [], "other": "MySQL"}


def test_skip_does_not_message_agent(app, client):
    session_id, qid = _session_with_question(app, client)
    resp = client.post(f"/sessions/{session_id}/questions/{qid}/answer", data={"skip": "1"})
    assert resp.get_json()["status"] == "skipped"
    with app.app_context():
        types = [e.event_type for e in agent_models.list_agent_events(get_db(), session_id)]
    assert types == ["ClarifyingQuestion"]


def test_invalid_and_repeat_answers_rejected(app, client):
    session_id, qid = _session_with_question(app, client)
    url = f"/sessions/{session_id}/questions/{qid}/answer"
    assert client.post(url, data={}).status_code == 400
    assert client.post(url, data={"selected": "Oracle"}).status_code == 400
    assert client.post(url, data={"selected": "SQLite"}).status_code == 200
    assert client.post(url, data={"selected": "SQLite"}).status_code == 400


def test_question_must_belong_to_session(app, client):
    session_id, qid = _session_with_question(app, client)
    assert client.post(f"/sessions/{session_id + 99}/questions/{qid}/answer").status_code == 404
    assert client.post(f"/sessions/{session_id}/questions/{qid + 99}/answer").status_code == 404


def _new_page(playwright, viewport):
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:  # browser binary missing / cannot start
        pytest.skip(f"Playwright browser unavailable: {exc}")
    return browser, browser.new_page(viewport=viewport)


@pytest.mark.parametrize("viewport", [{"width": 375, "height": 700}, {"width": 1280, "height": 800}])
def test_panel_select_submit_and_layout(browser_type_launch_args, app, client, live_server, viewport):
    sync_api = pytest.importorskip("playwright.sync_api")
    session_id, qid = _session_with_question(app, client)

    with sync_api.sync_playwright() as p:
        browser, page = _new_page(p, viewport)
        try:
            page.goto(f"{live_server}/sessions/{session_id}")
            panel = page.locator("#questionPanel")
            panel.wait_for(state="visible")
            assert page.locator("#chatForm").is_hidden()
            assert page.locator("#questionChip").inner_text() == "Database"

            options = page.locator(".question-option")
            assert options.count() == 3  # two agent options + Other
            for i in range(3):
                assert options.nth(i).bounding_box()["height"] >= 44
            # no horizontal scroll at this width
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )

            submit = page.locator("#questionSubmit")
            assert submit.is_disabled()

            # Other reveals the text field; Submit needs text
            options.nth(2).click()
            other = page.locator("#questionOther")
            assert other.is_visible() and submit.is_disabled()
            other.fill("MySQL")
            assert submit.is_enabled()

            # pick an option with the number key instead
            other.fill("")
            page.locator("body").click(position={"x": 1, "y": 1})
            page.keyboard.press("2")
            assert options.nth(1).get_attribute("aria-checked") == "true"
            assert submit.is_enabled()
            submit.click()

            page.locator("#questionPanel").wait_for(state="hidden")
            assert page.locator("#chatForm").is_visible()
            page.locator(".question-answered").wait_for()
            assert page.locator(".question-answered .is-chosen").inner_text() == "SQLite"
        finally:
            browser.close()


def test_panel_skip_with_escape(browser_type_launch_args, app, client, live_server):
    sync_api = pytest.importorskip("playwright.sync_api")
    session_id, qid = _session_with_question(app, client)

    with sync_api.sync_playwright() as p:
        browser, page = _new_page(p, {"width": 1280, "height": 800})
        try:
            page.goto(f"{live_server}/sessions/{session_id}")
            page.locator("#questionPanel").wait_for(state="visible")
            assert page.locator(".question-pending .question-badge").inner_text() == "Running"
            page.keyboard.press("Escape")
            page.locator(".question-skipped").wait_for()
            assert page.locator("#questionPanel").is_hidden()
        finally:
            browser.close()
    with app.app_context():
        assert agent_models.get_clarifying_question(get_db(), qid).status == "SKIPPED"
