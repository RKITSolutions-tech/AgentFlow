"""Tests for the persistent application shell: sidebar, project tabs, theme."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from bs4 import BeautifulSoup

from app.agents import models as agent_models
from app.db import get_db
from app.shell import SIDEBAR_SESSIONS_PER_PROJECT, relative_age, session_state, session_title
from tests.conftest import create_project_with_repo


def _soup(resp):
    return BeautifulSoup(resp.data, "html.parser")


def _make_sessions(app, project_id, count, agent_type="codex"):
    ids = []
    with app.app_context():
        db = get_db()
        for _ in range(count):
            ids.append(agent_models.create_agent_session(db, project_id, agent_type))
    return ids


def _create_project(client, name):
    resp = client.post("/projects/new", data={"name": name, "description": ""})
    return int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])


# --- helpers ----------------------------------------------------------------

def _stamp(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%d %H:%M:%S")


@pytest.mark.parametrize(
    "delta, expected",
    [
        (timedelta(seconds=5), "now"),
        (timedelta(minutes=5, seconds=10), "5m"),
        (timedelta(hours=3, minutes=1), "3h"),
        (timedelta(days=2, hours=1), "2d"),
    ],
)
def test_relative_age_buckets(delta, expected):
    assert relative_age(_stamp(delta)) == expected


@pytest.mark.parametrize("value", [None, "", "not a date"])
def test_relative_age_tolerates_bad_input(value):
    assert relative_age(value) == ""


def test_session_state_mapping():
    assert session_state("RUNNING") == "running"
    assert session_state("STARTING") == "running"
    assert session_state("FAILED") == "failed"
    assert session_state("STOPPED") == "idle"
    assert session_state("COMPLETED") == "idle"


def test_session_title_is_stable_and_readable():
    assert session_title("codex", 7) == "Codex #7"


# --- sidebar ----------------------------------------------------------------

def test_sidebar_present_on_every_top_level_page(client):
    for url in ("/projects", "/sessions", "/settings", "/projects/new"):
        resp = client.get(url)
        assert resp.status_code == 200, url
        soup = _soup(resp)
        assert soup.select_one("aside#primary-nav.sidebar"), f"{url} missing sidebar"
        assert soup.select_one(".main-pane main.content"), f"{url} missing main pane"


def test_sidebar_lists_projects_with_session_counts(client, app):
    p1 = _create_project(client, "Alpha")
    p2 = _create_project(client, "Beta")
    _make_sessions(app, p1, 3)

    soup = _soup(client.get("/projects"))
    nodes = {n.select_one(".project-name").get_text(strip=True): n for n in soup.select(".project-node")}
    assert set(nodes) == {"Alpha", "Beta"}
    assert nodes["Alpha"].select_one(".count").get_text(strip=True) == "3"
    assert nodes["Beta"].select_one(".count").get_text(strip=True) == "0"
    assert p2 != p1


def test_sidebar_expands_only_the_active_project(client, app):
    p1 = _create_project(client, "Alpha")
    _create_project(client, "Beta")

    soup = _soup(client.get(f"/projects/{p1}"))
    open_names = [
        n.select_one(".project-name").get_text(strip=True)
        for n in soup.select(".project-node[open]")
    ]
    assert open_names == ["Alpha"]


def test_sidebar_session_rows_link_to_chat_and_mark_current(client, app):
    project_id = _create_project(client, "Alpha")
    first, second = _make_sessions(app, project_id, 2)

    soup = _soup(client.get(f"/sessions/{second}"))
    rows = {a["href"]: a for a in soup.select("a.session-row")}
    assert f"/sessions/{first}" in rows and f"/sessions/{second}" in rows
    assert rows[f"/sessions/{second}"].get("aria-current") == "page"
    assert rows[f"/sessions/{first}"].get("aria-current") is None
    # viewing a session expands the project it belongs to
    assert soup.select_one(".project-node[open] .project-name").get_text(strip=True) == "Alpha"


def test_sidebar_caps_sessions_and_links_to_the_rest(client, app):
    project_id = _create_project(client, "Alpha")
    total = SIDEBAR_SESSIONS_PER_PROJECT + 3
    _make_sessions(app, project_id, total)

    soup = _soup(client.get(f"/projects/{project_id}"))
    assert len(soup.select("a.session-row")) == SIDEBAR_SESSIONS_PER_PROJECT
    more = soup.select_one("a.session-more")
    assert more is not None and f"All {total} sessions" in more.get_text()


def test_sidebar_empty_state_without_projects(client):
    soup = _soup(client.get("/projects"))
    assert soup.select_one(".sidebar-empty") is not None


def test_sidebar_session_state_dot_reflects_status(client, app):
    project_id = _create_project(client, "Alpha")
    (running_id, failed_id) = _make_sessions(app, project_id, 2)
    with app.app_context():
        db = get_db()
        agent_models.set_session_status(db, running_id, "RUNNING")
        agent_models.set_session_status(db, failed_id, "FAILED")

    soup = _soup(client.get(f"/projects/{project_id}"))
    dots = {
        a["href"]: a.select_one(".state-dot")["class"]
        for a in soup.select("a.session-row")
    }
    assert "state-running" in dots[f"/sessions/{running_id}"]
    assert "state-failed" in dots[f"/sessions/{failed_id}"]


def test_sidebar_does_not_leak_deleted_project(client):
    project_id = _create_project(client, "Doomed")
    client.post(
        f"/projects/{project_id}/delete", headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert b"Doomed" not in client.get("/projects").data


# --- project tabs ------------------------------------------------------------

def test_overview_and_sessions_pages_have_project_tabs(client, app):
    project_id, _ = create_project_with_repo(client, app.config["allowed_root"])

    for url, active in (
        (f"/projects/{project_id}", "Overview"),
        (f"/sessions/project/{project_id}", "Sessions"),
    ):
        soup = _soup(client.get(url))
        tabs = soup.select_one("nav.project-tabs")
        assert tabs is not None, url
        current = tabs.select('a.tab[aria-current="page"]')
        assert [a.get_text(strip=True) for a in current] == [active], url


def test_chat_page_marks_sessions_tab_and_fills_height(client, app):
    project_id, _ = create_project_with_repo(client, app.config["allowed_root"])
    (session_id,) = _make_sessions(app, project_id, 1)

    soup = _soup(client.get(f"/sessions/{session_id}"))
    current = soup.select('nav.project-tabs a.tab[aria-current="page"]')
    assert [a.get_text(strip=True) for a in current] == ["Sessions"]
    assert "content-fill" in soup.select_one("main.content")["class"]


def test_repository_tabs_omitted_when_project_has_no_repository(client):
    project_id = _create_project(client, "Bare")
    tabs = _soup(client.get(f"/projects/{project_id}")).select_one("nav.project-tabs")
    labels = [a.get_text(strip=True) for a in tabs.select("a.tab")]
    assert labels == ["Overview", "Sessions", "Backlog", "Sprints", "Pipelines", "Ralph", "Artifacts", "Acceptance", "Prompts"]


def test_repository_tabs_use_primary_repository_on_overview(client, app):
    project_id, _ = create_project_with_repo(client, app.config["allowed_root"])
    tabs = _soup(client.get(f"/projects/{project_id}")).select_one("nav.project-tabs")
    hrefs = {a.get_text(strip=True): a["href"] for a in tabs.select("a.tab")}
    assert hrefs["Files"] == f"/projects/{project_id}/repos/1/files"
    assert hrefs["Terminal"] == f"/projects/{project_id}/repos/1/terminal"


# --- theme --------------------------------------------------------------------

def test_theme_defaults_to_dark_and_offers_a_toggle(client):
    soup = _soup(client.get("/projects"))
    assert soup.html["data-theme"] == "dark"
    assert soup.select_one("button[data-theme-toggle]") is not None


def test_stylesheet_defines_both_themes_from_tokens():
    from pathlib import Path

    css = Path("app/static/app.css").read_text()
    assert ":root {" in css and ':root[data-theme="light"]' in css
    # no stray hard-coded brand colours outside the token block
    body = css.split("/* 2. Base", 1)[1]
    assert "#007bff" not in body and "#1f2937" not in body
