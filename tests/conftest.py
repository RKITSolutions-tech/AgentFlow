import os
import threading

import pytest
from werkzeug.serving import make_server

from app import create_app
from app.config import Config


def require_playwright():
    """Return Playwright's `sync_playwright`, skipping when the package is absent."""
    return pytest.importorskip("playwright.sync_api").sync_playwright


def launch_chromium(playwright):
    """Launch Chromium, skipping only if the browser itself cannot start.

    Callers' assertions stay outside any try/except so real failures are
    never reported as skips (see CLAUDE.md, Playwright viewport tests).
    """
    try:
        return playwright.chromium.launch()
    except Exception as exc:  # browser binary missing / cannot start
        pytest.skip(f"Playwright browser unavailable: {exc}")


def create_project_with_repo(client, allowed_root, repo_name="repo-a", project_name="Proj"):
    """Create a project with one repository under `allowed_root`.

    Returns (project_id, repo_path). Shared by workspace tests that need a
    project/repo to exercise workspace routes against.
    """
    repo_path = os.path.join(allowed_root, repo_name)
    os.makedirs(repo_path, exist_ok=True)

    resp = client.post("/projects/new", data={"name": project_name, "description": ""})
    project_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(
        f"/projects/{project_id}/repositories",
        data={"name": repo_name, "path": repo_path, "is_primary": "on"},
    )
    return project_id, repo_path


def add_repository(client, project_id, allowed_root, repo_name):
    """Add an additional repository to an existing project. Returns repo_path."""
    repo_path = os.path.join(allowed_root, repo_name)
    os.makedirs(repo_path, exist_ok=True)
    client.post(
        f"/projects/{project_id}/repositories",
        data={"name": repo_name, "path": repo_path},
    )
    return repo_path


@pytest.fixture
def app(tmp_path):
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()

    config = Config(
        DATABASE_PATH=str(tmp_path / "test.sqlite3"),
        SECRET_KEY="test-secret",
        ALLOWED_PROJECT_ROOTS=(str(allowed_root),),
        TESTING=True,
    )
    application = create_app(config)
    application.config["allowed_root"] = str(allowed_root)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


class _LiveServerThread(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self._server = make_server("127.0.0.1", 0, app)
        self.port = self._server.server_port

    def run(self):
        self._server.serve_forever()

    def shutdown(self):
        self._server.shutdown()


@pytest.fixture
def live_server(app):
    """Serves `app` over real HTTP in a background thread, sharing the same
    (per-test, tmp_path-backed) database as the `app`/`client` fixtures.

    Playwright drives a real browser, which can't use Flask's in-process
    test client -- it needs an actual server to connect to. Pointing it at
    a separately-started `flask run` process would talk to a *different*
    database than whatever the test set up via `client`, so nothing the
    test created would be visible to the browser.
    """
    thread = _LiveServerThread(app)
    thread.start()
    yield f"http://127.0.0.1:{thread.port}"
    thread.shutdown()
    thread.join(timeout=5)


# -- real-browser assertions shared by the Phase 2 UI verification tests ----------------------

MOBILE = {"width": 375, "height": 667}
DESKTOP = {"width": 1280, "height": 800}
MIN_TAP = 44
_TAP_SELECTOR = (
    "main button, main select, main input:not([type=hidden]):not([type=checkbox]):not([type=radio]), "
    "main textarea, main .row-actions a, main .btn-primary, main summary"
)


def assert_no_horizontal_overflow(page, label=""):
    """The page itself must never scroll sideways (wide content scrolls inside its own box)."""
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"{label or page.url} scrolls horizontally by {overflow}px"


def assert_touch_target_size(page, selector=_TAP_SELECTOR, minimum=MIN_TAP, label=""):
    """Every visible control matching `selector` is at least `minimum` px tall."""
    small = page.evaluate(
        """([selector, minimum]) => [...document.querySelectorAll(selector)]
            .filter(e => e.offsetParent !== null && getComputedStyle(e).visibility !== 'hidden')
            .map(e => [e, e.getBoundingClientRect()])
            .filter(([e, r]) => r.width > 0 && r.height > 0 && r.height < minimum - 0.5)
            .map(([e, r]) => (e.tagName.toLowerCase() + (e.className ? '.' + String(e.className).split(' ')[0] : '')
                + ' "' + (e.innerText || e.value || e.name || '').trim().slice(0, 24) + '" ' + Math.round(r.height) + 'px'))""",
        [selector, minimum],
    )
    assert not small, f"{label or page.url}: touch targets under {minimum}px: {small}"


def assert_viewport_layout_switches(page, base_url, selector, property_name, mobile_value, desktop_value):
    """A CSS property flips between the mobile and desktop layouts on resize."""
    page.set_viewport_size(MOBILE)
    page.goto(base_url)
    assert page.evaluate(f"getComputedStyle(document.querySelector('{selector}')).{property_name}") == mobile_value
    page.set_viewport_size(DESKTOP)
    assert page.evaluate(f"getComputedStyle(document.querySelector('{selector}')).{property_name}") == desktop_value


def watch_console(page):
    """Collect uncaught exceptions and console errors for a later `assert not errors`."""
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on(
        "console",
        lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error",) else None,
    )
    return errors
