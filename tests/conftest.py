import threading

import pytest
from werkzeug.serving import make_server

from app import create_app
from app.config import Config


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
