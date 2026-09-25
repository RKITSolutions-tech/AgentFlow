from __future__ import annotations

import pytest

from app.agents import models as agent_models
from app.db import get_db
from app.notifications import models
from app.projects import models as project_models


def _session(db):
    project_id = project_models.create_project(db, "Notify Project")
    return agent_models.create_agent_session(db, project_id, "fake")


def test_notify_creates_row_per_enabled_channel(app):
    with app.app_context():
        db = get_db()
        sid = _session(db)
        created = models.notify(db, sid, "blocked_on_question", "hello")
        assert {n.channel for n in created} == {"in_app", "browser"}
        assert models.unread_count(db) == 1
        assert models.list_notifications(db)[0].message == "hello"


def test_preferences_are_honoured(app):
    with app.app_context():
        db = get_db()
        sid = _session(db)
        models.set_preference(db, "blocked_on_question", "browser", False)
        created = models.notify(db, sid, "blocked_on_question", "x")
        assert [n.channel for n in created] == ["in_app"]

        models.set_preference(db, "blocked_on_question", "in_app", False)
        assert models.notify(db, sid, "blocked_on_question", "y") == []
        # other kinds are unaffected
        assert len(models.notify(db, sid, "awaiting_permission", "z")) == 2


def test_invalid_kind_channel_rejected(app):
    with app.app_context():
        db = get_db()
        with pytest.raises(ValueError):
            models.notify(db, _session(db), "nope", "x")
        with pytest.raises(ValueError):
            models.set_preference(db, "blocked_on_question", "carrier-pigeon", True)
        with pytest.raises(ValueError):
            models.set_preference(db, "nope", "in_app", True)


def test_question_creates_unread_notification_and_answer_clears_it(app, client):
    with app.app_context():
        db = get_db()
        sid = _session(db)
        q = agent_models.create_clarifying_question(
            db, sid, "Which database?", ["a", "b"], header="Database"
        )
        (n,) = models.list_notifications(db)
        assert n.message == "Database needs your input: Which database?"
        assert n.read_at is None and n.session_id == sid
        assert models.unread_count(db) == 1

    assert client.post(f"/sessions/{sid}/questions/{q.id}/answer", data={"skip": "1"}).status_code == 200
    with app.app_context():
        assert models.unread_count(get_db()) == 0


def test_read_and_read_all(app, client):
    with app.app_context():
        db = get_db()
        sid = _session(db)
        first = models.notify(db, sid, "blocked_on_question", "one")[0]
        models.notify(db, sid, "blocked_on_question", "two")

    resp = client.post(f"/notifications/{first.id}/read")
    assert resp.get_json()["unread"] == 1
    assert client.post("/notifications/999/read").status_code == 404
    resp = client.post("/notifications/read-all", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.get_json()["unread"] == 0


def test_poll_delivers_browser_notifications_only_when_asked(app, client):
    with app.app_context():
        db = get_db()
        models.notify(db, _session(db), "blocked_on_question", "ping")

    data = client.get("/notifications/poll").get_json()
    assert data["unread"] == 1 and data["browser"] == []  # permission not granted: keep pending

    data = client.get("/notifications/poll?deliver=1").get_json()
    assert [n["message"] for n in data["browser"]] == ["ping"]
    assert data["browser"][0]["url"].startswith("/sessions/")
    # only delivered once
    assert client.get("/notifications/poll?deliver=1").get_json()["browser"] == []


def test_preferences_form_and_page(app, client):
    resp = client.get("/notifications")
    assert resp.status_code == 200
    assert b"Enable browser notifications" in resp.data

    resp = client.post(
        "/notifications/preferences", data={"enabled": ["blocked_on_question:in_app"]}
    )
    assert resp.status_code == 302
    with app.app_context():
        prefs = models.get_preferences(get_db())
    assert prefs["blocked_on_question"] == {"in_app": True, "browser": False}
    assert prefs["awaiting_permission"] == {"in_app": False, "browser": False}


def test_sidebar_badge_shows_unread_count(app, client):
    with app.app_context():
        db = get_db()
        models.notify(db, _session(db), "blocked_on_question", "ping")
    html = client.get("/notifications").get_data(as_text=True)
    assert 'data-notify-badge>1<' in html.replace("\n", "").replace("  ", "")


def test_deleting_session_removes_notifications(app):
    with app.app_context():
        db = get_db()
        sid = _session(db)
        models.notify(db, sid, "blocked_on_question", "x")
        agent_models.delete_agent_session(db, sid)
        assert models.list_notifications(db) == []


@pytest.mark.parametrize("viewport", [{"width": 375, "height": 700}, {"width": 1280, "height": 800}])
def test_notifications_page_layout_and_badge_polling(
    browser_type_launch_args, app, client, live_server, viewport
):
    sync_api = pytest.importorskip("playwright.sync_api")
    with app.app_context():
        db = get_db()
        sid = _session(db)
        models.notify(db, sid, "blocked_on_question", "A very long question " * 12)

    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser binary missing / cannot start
            pytest.skip(f"Playwright browser unavailable: {exc}")
        try:
            page = browser.new_page(viewport=viewport)
            page.goto(f"{live_server}/notifications")
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
            assert page.locator(".notify-item.is-unread").count() == 1
            # The button only shows while permission is undecided; otherwise the
            # page explains the state (headless Chromium reports "denied").
            permission = page.evaluate("Notification.permission")
            enable = page.locator("#enableBrowserNotifications")
            status = page.locator("#browserNotificationStatus")
            if permission == "default":
                assert enable.is_visible()
            else:
                assert enable.is_hidden() and status.inner_text() != ""
            for box in page.locator(".notify-prefs input[type=checkbox]").all():
                assert box.is_visible()

            # badge is refreshed by polling once something is read elsewhere
            client.post("/notifications/read-all")
            page.wait_for_selector("[data-notify-badge]", state="hidden", timeout=15000)
        finally:
            browser.close()
