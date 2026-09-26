"""375px / 1280px layout checks for the backlog and sprint pages.

Skipped (never failed) when Playwright or its browser is unavailable; see
`launch_chromium` in conftest.py.
"""
import pytest

from app.backlog import persistence as backlog
from app.db import get_db
from app.sprints import persistence as sprints
from tests.conftest import launch_chromium, require_playwright

VIEWPORTS = [{"width": 375, "height": 667}, {"width": 1280, "height": 800}]


def _seed(client, app):
    app.config["PLANNING_AGENT"] = "fake"
    resp = client.post("/projects/new", data={"name": "Proj"})
    pid = int(resp.headers["Location"].rsplit("/", 1)[-1])
    with app.app_context():
        db = get_db()
        long_text = "a-very-long-unbroken-description-" * 8
        ids = [backlog.create_item(db, pid, text=long_text), backlog.create_item(db, pid, text="second")]
        sid = sprints.create_sprint(db, pid, "S1", goal="Ship")
    from app.sprints import workflow

    with app.app_context():
        workflow.select_items(get_db(), sid, ids)
    client.post(f"/projects/{pid}/sprints/{sid}/plan", headers={"X-Requested-With": "XMLHttpRequest"})
    return pid, sid


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["375", "1280"])
def test_backlog_and_sprint_pages_do_not_scroll_sideways(app, client, live_server, viewport):
    sync_playwright = require_playwright()
    pid, sid = _seed(client, app)
    pages = [
        f"/projects/{pid}/backlog/inbox",
        f"/projects/{pid}/backlog/triage",
        f"/projects/{pid}/backlog/sprint",
        f"/projects/{pid}/sprints",
        f"/projects/{pid}/sprints/{sid}",
    ]
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=viewport)
            for path in pages:
                page.goto(live_server + path)
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                assert overflow <= 0, f"{path} scrolls horizontally by {overflow}px"
        finally:
            browser.close()


def test_touch_targets_and_graph_orientation(app, client, live_server):
    sync_playwright = require_playwright()
    pid, sid = _seed(client, app)
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            mobile = browser.new_page(viewport=VIEWPORTS[0])
            mobile.goto(f"{live_server}/projects/{pid}/backlog/triage")
            for button in mobile.query_selector_all(".backlog-card .row-actions button"):
                assert button.bounding_box()["height"] >= 40
            mobile.goto(f"{live_server}/projects/{pid}/sprints/{sid}")
            direction = mobile.evaluate(
                "getComputedStyle(document.querySelector('.task-graph')).flexDirection"
            )
            assert direction == "column"

            desktop = browser.new_page(viewport=VIEWPORTS[1])
            desktop.goto(f"{live_server}/projects/{pid}/sprints/{sid}")
            direction = desktop.evaluate(
                "getComputedStyle(document.querySelector('.task-graph')).flexDirection"
            )
            assert direction == "row"
        finally:
            browser.close()
