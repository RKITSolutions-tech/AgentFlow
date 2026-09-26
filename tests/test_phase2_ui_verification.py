"""Real-browser pass over the Phase 2 pages at 375px (mobile) and 1280px (desktop).

Every page is visited with console/JS errors captured; it must not scroll
sideways and (on mobile) its controls must be at least 44px tall. Behaviour
tests then drive the shared JavaScript (app.js, pipeline.js, acceptance.js).
Each test that guards a fixed defect says which one.

Playwright/Chromium missing skips (see `launch_chromium`); nothing else does.
"""
import subprocess
import sys

import pytest

from app.agents.fake import FakeAgentAdapter
from app.backlog import persistence as backlog
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.pipelines import persistence as pipelines
from app.pipelines.engine import PipelineEngine
from app.ralph import models as ralph
from app.sprints import persistence as sprints
from app.sprints import queue
from tests.conftest import (
    DESKTOP,
    MOBILE,
    assert_no_horizontal_overflow,
    assert_touch_target_size,
    assert_viewport_layout_switches,
    create_project_with_repo,
    launch_chromium,
    require_playwright,
    watch_console,
)

PY = sys.executable
AJAX = {"X-Requested-With": "XMLHttpRequest"}
VIEWPORTS = [MOBILE, DESKTOP]
IDS = ["375", "1280"]


@pytest.fixture
def world(app, client, tmp_path):
    """A project with backlog items, an approved+released sprint, a pipeline
    execution (with a failing, looping and waiting step), a Ralph run with an
    iteration, artifacts and acceptance criteria."""
    app.config["PLANNING_AGENT"] = "fake"
    pid, repo = create_project_with_repo(client, app.config["allowed_root"])
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    with app.app_context():
        db = get_db()
        long = "an-unbroken-title-" * 6
        items = [backlog.create_item(db, pid, text=long), backlog.create_item(db, pid, text="second item")]
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        engine = PipelineEngine(db, provider, str(tmp_path / "artifacts"), lambda c: FakeAgentAdapter(c), sleep=lambda s: None)
        elements = [
            {"name": "build", "type": "COMMAND", "config": {"command": f'{PY} -c "print(1)"'}},
            {"name": "unit", "type": "TEST", "config": {"command": f'{PY} -c "import sys; sys.exit(1)"'},
             "depends_on": ["build"], "compensation": {"action": "CONTINUE"}},
            {"name": "gate", "type": "MANUAL_APPROVAL", "config": {"prompt": "Ship it?"}, "depends_on": ["build"]},
        ]
        pipelines.create_pipeline(db, {"name": "verify", "elements": elements}, pid)
        eid = engine.create("verify", pid, 1, variables={"task": "demo"})
        engine.run(eid)
        run_id = ralph.create_run(db, pid, 1, "Ralph task", "do it", "verify", acceptance=["it works"])
        it = ralph.add_iteration(db, run_id, 1, "the prompt", False)
        ralph.update_iteration(db, it, reply="reply text", status="FAILED", analysis="unit failed", verification_execution_id=eid, changed_files=["a.py"])
        ralph.update_run(db, run_id, status="BLOCKED", needs_attention=1, reason="No progress")
    with app.app_context():
        db = get_db()
        sprint_id = sprints.create_sprint(db, pid, "Sprint 1", goal="Ship it")
        from app.sprints import workflow

        workflow.select_items(db, sprint_id, items)
    client.post(f"/projects/{pid}/sprints/{sprint_id}/plan", headers=AJAX)
    with app.app_context():
        db = get_db()
        for w in sprints.list_work_items(db, sprint_id):
            sprints.update_work_item(db, w.id, status="REVIEWED")
        from app.sprints import workflow

        workflow.approve(db, sprint_id, "Sam")
        queue.release_sprint(db, sprint_id)
    client.post(
        f"/projects/{pid}/acceptance",
        data={"title": "Login works", "author": "Sam", "ralph_run_id": run_id, "required": "on"}, headers=AJAX,
    )
    with app.app_context():
        art = get_db().execute("SELECT id FROM artifact_library ORDER BY id LIMIT 1").fetchone()
        crit = get_db().execute("SELECT id FROM acceptance_criteria ORDER BY id LIMIT 1").fetchone()
    return type("W", (), {
        "pid": pid, "sprint": sprint_id, "eid": eid, "run": run_id,
        "artifact": art[0] if art else None, "criterion": crit[0] if crit else None,
    })


def _pages(w):
    p = f"/projects/{w.pid}"
    pages = {
        "overview": p,
        "backlog inbox": f"{p}/backlog/inbox",
        "backlog triage": f"{p}/backlog/triage",
        "backlog sprint": f"{p}/backlog/sprint",
        "sprints": f"{p}/sprints",
        "sprint detail": f"{p}/sprints/{w.sprint}",
        "sprint queue": f"{p}/sprints/{w.sprint}/queue",
        "pipelines": f"{p}/pipelines",
        "pipeline execution": f"{p}/pipelines/executions/{w.eid}",
        "ralph": f"{p}/ralph",
        "ralph run": f"{p}/ralph/{w.run}",
        "artifacts": f"{p}/artifacts",
        "acceptance": f"{p}/acceptance",
        "prompt library": "/prompts/library",
        "prompt blocks": "/prompts/ralph-blocks",
        "prompt template": "/prompts/templates",
    }
    if w.artifact:
        pages["artifact"] = f"{p}/artifacts/{w.artifact}"
    if w.criterion:
        pages["criterion"] = f"{p}/acceptance/{w.criterion}"
    return pages


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=IDS)
def test_every_phase2_page_is_clean_and_fits(world, live_server, viewport):
    """No JS errors, no sideways page scroll, and (mobile) 44px touch targets."""
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=viewport)
            errors = watch_console(page)
            problems = []  # report every page's problems at once, not just the first
            for name, path in _pages(world).items():
                page.goto(live_server + path)
                page.wait_for_load_state("networkidle")
                try:
                    assert_no_horizontal_overflow(page, f"{name} ({path})")
                    if viewport is MOBILE:
                        assert_touch_target_size(page, label=f"{name} ({path})")
                except AssertionError as failure:
                    problems.append(str(failure))
            assert problems == []
            assert errors == []
        finally:
            browser.close()


def test_ajax_form_submits_exactly_once(world, live_server):
    """Defect: two document-level `submit` handlers matched `form[data-ajax-form]`,
    so every AJAX form posted twice (a double-created record on Add)."""
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=DESKTOP)
            errors = watch_console(page)
            posts = []
            page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/prompts/library" in r.url else None)
            page.goto(live_server + "/prompts/library")
            page.fill("form[data-ajax-form] input[name=name]", "once-only")
            page.fill("form[data-ajax-form] textarea[name=content]", "text")
            page.click("form[data-ajax-form] button[type=submit]")
            page.wait_for_selector("text=once-only")
            assert len(posts) == 1, posts
            assert page.locator("table >> text=once-only").count() == 1
            assert errors == []
        finally:
            browser.close()


def test_ajax_action_confirms_and_removes_the_row(world, live_server):
    """`data-ajax-action` honours `data-confirm` (dismiss keeps the row, accept removes it)."""
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=MOBILE)
            errors = watch_console(page)
            page.goto(live_server + "/prompts/library")
            rows = page.locator("tr[data-row]")
            before = rows.count()
            page.once("dialog", lambda d: d.dismiss())
            rows.first.locator("[data-ajax-action]").click()
            assert rows.count() == before
            page.once("dialog", lambda d: d.accept())
            rows.first.locator("[data-ajax-action]").click()
            page.wait_for_function(f"document.querySelectorAll('tr[data-row]').length === {before - 1}")
            assert errors == []
        finally:
            browser.close()


def test_non_json_error_response_shows_a_readable_message(world, live_server):
    """Defect: a 500/HTML reply made `response.json()` throw, flashing 'Unexpected token <'."""
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=DESKTOP)
            page.route("**/prompts/library", lambda route: route.fulfill(status=500, content_type="text/html", body="<h1>Oops</h1>")
                       if route.request.method == "POST" else route.continue_())
            page.goto(live_server + "/prompts/library")
            page.fill("form[data-ajax-form] input[name=name]", "x")
            page.fill("form[data-ajax-form] textarea[name=content]", "y")
            page.click("form[data-ajax-form] button[type=submit]")
            page.wait_for_selector(".flash-error")
            text = page.inner_text(".flash-error")
            assert "Unexpected token" not in text and "JSON" not in text
        finally:
            browser.close()


def test_drawer_opens_closes_and_returns_focus(world, live_server):
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=MOBILE)
            page.goto(f"{live_server}/projects/{world.pid}")
            assert not page.locator("#primary-nav").evaluate("e => e.classList.contains('open')")
            page.click(".nav-toggle")
            assert page.locator("#primary-nav").evaluate("e => e.classList.contains('open')")
            assert page.get_attribute(".nav-toggle", "aria-expanded") == "true"
            page.keyboard.press("Escape")
            assert not page.locator("#primary-nav").evaluate("e => e.classList.contains('open')")
            # Focus returns to the toggle (it used to be left wherever it was).
            assert page.evaluate("document.activeElement && document.activeElement.classList.contains('nav-toggle')")
        finally:
            browser.close()


def test_pipeline_graph_reflows_and_step_click_opens_inspector(world, live_server):
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=MOBILE)
            errors = watch_console(page)
            url = f"{live_server}/projects/{world.pid}/pipelines/executions/{world.eid}"
            assert_viewport_layout_switches(page, url, ".pipeline-layers", "flexDirection", "column", "row")
            page.wait_for_selector(".pipeline-edges path", state="attached")
            assert page.locator(".pipeline-edges path").count() >= 2
            for viewport in (DESKTOP, MOBILE):
                page.set_viewport_size(viewport)
                assert_no_horizontal_overflow(page, f"execution at {viewport['width']}")
                page.click('[data-node="unit"]')
                page.wait_for_selector("[data-inspector] h2:has-text('unit')")
                assert page.get_attribute('[data-node="unit"]', "aria-pressed") == "true"
            # Edges are redrawn after a resize rather than left at the old geometry.
            page.set_viewport_size(DESKTOP)
            width = page.evaluate("+document.querySelector('.pipeline-edges').getAttribute('width')")
            assert width > 0
            assert errors == []
        finally:
            browser.close()


def test_acceptance_template_fields_appear_and_clear(world, live_server):
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=MOBILE)
            errors = watch_console(page)
            page.goto(f"{live_server}/projects/{world.pid}/acceptance")
            options = page.locator("[data-template-select] option[data-fields]:not([data-fields=''])")
            assert options.count() >= 1
            value = options.first.get_attribute("value")
            fields = options.first.get_attribute("data-fields").split(",")
            page.select_option("[data-template-select]", value)
            assert page.locator("[data-template-fields] input").count() == len(fields)
            page.select_option("[data-template-select]", "")
            assert page.locator("[data-template-fields] input").count() == 0
            assert errors == []
        finally:
            browser.close()


def test_sprint_queue_actions_work_from_the_page(world, live_server):
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=MOBILE)
            errors = watch_console(page)
            page.goto(f"{live_server}/projects/{world.pid}/sprints/{world.sprint}/queue")
            assert page.locator(".queue-table tbody tr").count() >= 1
            assert page.locator(".queue-progress li").count() == 6
            assert "Run next task" in page.inner_text("main")
            assert_no_horizontal_overflow(page)
            assert errors == []
        finally:
            browser.close()


def test_autorefresh_waits_while_the_user_is_typing(world, live_server):
    """Defect: `data-autorefresh` reloaded the page every few seconds even while
    steering text was being typed into a running Ralph page, discarding it."""
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=DESKTOP)
            errors = watch_console(page)

            def add_marker(route):
                response = route.fetch()
                body = response.text().replace("</main>", '<div data-autorefresh="300" hidden></div></main>', 1)
                route.fulfill(response=response, body=body)

            page.route(f"**/ralph/{world.run}", add_marker)
            page.goto(f"{live_server}/projects/{world.pid}/ralph/{world.run}")
            page.evaluate("window.__still_here = true")
            page.fill("textarea[name=message]", "use the old uploader")
            page.wait_for_timeout(1500)  # five refresh intervals
            assert page.evaluate("window.__still_here === true"), "the page reloaded while typing"
            assert page.input_value("textarea[name=message]") == "use the old uploader"
            assert errors == []
        finally:
            browser.close()


def test_static_javascript_has_no_control_characters():
    """Defect: app.js held literal NUL bytes (markdown placeholders), which make
    grep, diff and some editors treat the file as binary. Use escapes instead."""
    import glob
    import os

    root = os.path.join(os.path.dirname(__file__), "..", "app", "static")
    for path in glob.glob(os.path.join(root, "*.js")):
        data = open(path, "rb").read()
        assert b"\x00" not in data, f"{os.path.basename(path)} contains a NUL byte"


@pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])
@pytest.mark.parametrize("viewport", VIEWPORTS, ids=IDS)
def test_key_pages_work_in_every_engine(world, live_server, engine, viewport):
    """Layout, JS errors, the graph, replay and an AJAX form in Chromium, Firefox and WebKit."""
    from tests.conftest import launch_browser

    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_browser(p, engine)
        try:
            page = browser.new_page(viewport=viewport)
            errors = watch_console(page)
            for path in (f"/projects/{world.pid}/sprints/{world.sprint}/queue", "/prompts/ralph-blocks"):
                page.goto(live_server + path)
                page.wait_for_load_state("networkidle")
                assert_no_horizontal_overflow(page, path)
            page.goto(f"{live_server}/projects/{world.pid}/pipelines/executions/{world.eid}")
            page.wait_for_selector(".pipeline-edges path", state="attached")
            page.click('[data-node="unit"]')
            page.wait_for_selector("[data-inspector] h2:has-text('unit')")
            page.click("[data-replay-toggle]")
            page.wait_for_selector(".replay-event")
            page.locator(".replay-event", has_text="Step Completed · build").first.click()
            page.wait_for_function("document.querySelector('[data-node=build] .node-state').textContent.includes('Passed')")
            assert_no_horizontal_overflow(page, "replay")
            page.goto(live_server + "/prompts/library")
            page.fill("form[data-ajax-form] input[name=name]", f"engine-{engine}")
            page.fill("form[data-ajax-form] textarea[name=content]", "text")
            page.click("form[data-ajax-form] button[type=submit]")
            page.wait_for_selector(f"text=engine-{engine}")
            assert errors == []
        finally:
            browser.close()


def test_sidebar_section_links_are_live(world, live_server):
    """Backlog / Sprints / Runs used to be dead `#` placeholders."""
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=DESKTOP)
            page.goto(f"{live_server}/projects/{world.pid}")
            for label, fragment in (("Backlog", "/backlog"), ("Sprints", "/sprints"), ("Runs", "/runs")):
                link = page.locator(".sidebar-links a", has_text=label)
                assert fragment in link.get_attribute("href")
                assert link.get_attribute("aria-disabled") is None
            page.click(".sidebar-links a:has-text('Sprints')")
            page.wait_for_url("**/sprints")
        finally:
            browser.close()
