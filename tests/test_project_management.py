import os
import subprocess
import time

import pytest

from app.db import _migrate, get_db
from app.projects import clone, models
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}


def _names(client, path="/projects"):
    page = client.get(path).data.decode()
    return page.split("<main", 1)[1]


def test_star_orders_first_and_toggles(client, app):
    a, _ = create_project_with_repo(client, app.config["allowed_root"], "ra", "Alpha")
    z, _ = create_project_with_repo(client, app.config["allowed_root"], "rz", "Zulu")
    body = _names(client)
    assert body.index("Alpha") < body.index("Zulu")
    assert client.post(f"/projects/{z}/star", headers=AJAX).status_code == 200
    body = _names(client)
    assert body.index("Zulu") < body.index("Alpha") and "Starred" in body
    client.post(f"/projects/{z}/unstar", headers=AJAX)
    body = _names(client)
    assert body.index("Alpha") < body.index("Zulu")


def test_archive_hides_and_restore_returns(client, app):
    pid, _ = create_project_with_repo(client, app.config["allowed_root"], "r", "Hideable")
    assert client.post(f"/projects/{pid}/archive", headers=AJAX).status_code == 200
    assert "Hideable" not in _names(client)
    assert "Hideable" in _names(client, "/projects?archived=1")
    # still reachable directly, and shown (marked) in the sidebar while viewed
    assert b"(archived)" in client.get(f"/projects/{pid}").data
    client.post(f"/projects/{pid}/restore", headers=AJAX)
    assert "Hideable" in _names(client)
    assert b"(archived)" not in client.get(f"/projects/{pid}").data


def test_archived_project_not_in_sidebar_elsewhere(client, app):
    pid, _ = create_project_with_repo(client, app.config["allowed_root"], "r", "SideGone")
    client.post(f"/projects/{pid}/archive", headers=AJAX)
    assert b"SideGone" not in client.get("/sessions").data


def test_flag_actions_unknown_project_and_fallback(client):
    assert client.post("/projects/99/star", headers=AJAX).status_code == 404
    assert client.post("/projects/99/archive").status_code == 404


def test_non_ajax_star_redirects(client, app):
    pid, _ = create_project_with_repo(client, app.config["allowed_root"], "r", "Redir")
    resp = client.post(f"/projects/{pid}/star")
    assert resp.status_code == 302


def test_migration_adds_starred_to_old_database(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "old.sqlite3")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, status TEXT)")
    _migrate(db)
    _migrate(db)  # idempotent
    assert "starred" in {r["name"] for r in db.execute("PRAGMA table_info(projects)")}


# -- clone -------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://github.com/o/r.git", "http://host:8080/o/r", "ssh://git@host/o/r.git",
    "git@github.com:o/r.git", "https://user@host/o/r",
])
def test_valid_urls(url):
    assert clone.validate_url(url) == url


@pytest.mark.parametrize("url", [
    "", "-oProxyCommand=x", "--upload-pack=evil", "file:///etc", "/local/path",
    "ext::sh -c id", "https://host/a b", "ftp://host/r", "git@host:o r",
])
def test_invalid_urls(url):
    with pytest.raises(clone.CloneError):
        clone.validate_url(url)


def test_folder_name_from_url():
    assert clone.folder_name_from_url("https://github.com/o/my-repo.git") == "my-repo"
    assert clone.folder_name_from_url("git@github.com:o/thing.git") == "thing"
    assert clone.folder_name_from_url("https://h/o/r/") == "r"


@pytest.fixture
def source_repo(tmp_path):
    src = tmp_path / "src-repo"
    src.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *cmd], cwd=src, check=True, env=env, capture_output=True)
    return src


def _start(client, app, url, **extra):
    data = {"url": url, "parent": app.config["allowed_root"], "folder": "cloned", "name": "Cloned", **extra}
    return client.post("/projects/clone", data=data)


def _wait(client, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/projects/clone/{job_id}").get_json()
        if job["status"] != "RUNNING":
            return job
        time.sleep(0.1)
    raise AssertionError("clone did not finish")


def test_start_clone_rejections(client, app, tmp_path):
    root = app.config["allowed_root"]
    assert _start(client, app, "file:///etc").status_code == 400
    assert client.post("/projects/clone", data={
        "url": "https://h/o/r.git", "parent": str(tmp_path), "folder": "x"}).status_code == 400
    assert client.post("/projects/clone", data={
        "url": "https://h/o/r.git", "parent": root, "folder": "../x"}).status_code == 400
    os.makedirs(os.path.join(root, "cloned"))
    assert _start(client, app, "https://h/o/r.git").status_code == 400
    assert client.post("/projects/clone", data={
        "url": "https://h/o/r.git", "parent": os.path.join(root, "missing"), "folder": "f"}).status_code == 400


def test_clone_success_creates_project(client, app, source_repo):
    # git refuses file:// only via our URL check, so drive the provider with a
    # local path by bypassing validation of the *transport* for this fixture.
    resp = _start_local(client, app, source_repo)
    job = _wait(client, resp.get_json()["id"])
    assert job["status"] == "DONE", job
    assert job["project_url"]
    assert os.path.isdir(os.path.join(app.config["allowed_root"], "cloned", ".git"))
    with app.app_context():
        projects = models.list_projects(get_db())
    assert [p.name for p in projects] == ["Cloned"]
    assert projects[0].repositories[0].path.endswith("cloned")
    again = client.get(f"/projects/clone/{resp.get_json()['id']}").get_json()
    assert again["status"] == "DONE"
    with app.app_context():
        assert len(models.list_projects(get_db())) == 1  # finalised exactly once


def _start_local(client, app, source, folder="cloned", name="Cloned"):
    """Start a clone of a local repo; our URL check rejects paths, so allow
    the path through validation for the test only."""
    import unittest.mock as mock
    with mock.patch.object(clone, "validate_url", lambda u: u):
        return client.post("/projects/clone", data={
            "url": str(source), "parent": app.config["allowed_root"],
            "folder": folder, "name": name})


def test_clone_failure_reports_and_cleans_up(client, app, tmp_path):
    resp = _start_local(client, app, tmp_path / "does-not-exist", folder="broken")
    job = _wait(client, resp.get_json()["id"])
    assert job["status"] == "FAILED" and job["message"]
    assert not os.path.exists(os.path.join(app.config["allowed_root"], "broken"))
    with app.app_context():
        assert models.list_projects(get_db()) == []


def test_clone_status_and_cancel_unknown(client):
    assert client.get("/projects/clone/999").status_code == 404
    assert client.post("/projects/clone/999/cancel").status_code == 404


def test_cancel_finished_job_is_noop(client, app, source_repo):
    job_id = _start_local(client, app, source_repo).get_json()["id"]
    _wait(client, job_id)
    assert client.post(f"/projects/clone/{job_id}/cancel").get_json()["status"] == "DONE"


def test_pages_render(client):
    assert b"Clone from URL" in client.get("/projects/clone").data
    assert b"data-dir-browser" in client.get("/projects/new").data


class _StubProvider:
    """Just enough of an ExecutionProvider to drive poll/cancel deterministically."""

    def __init__(self, status="RUNNING", exit_code=None, stderr=()):
        from types import SimpleNamespace as NS
        self.process = NS(status=status, exit_code=exit_code)
        self.events = [NS(event_type="ProcessOutput", stream="stderr", data=d) for d in stderr]
        self.stopped, self.destroyed = [], []

    def process_status(self, _pid): return self.process
    def stream_output(self, _pid, after_id=None): return self.events
    def stop_process(self, pid): self.stopped.append(pid)
    def destroy_context(self, cid): self.destroyed.append(cid)


def _job(app, dest):
    with app.app_context():
        db = get_db()
        cur = db.execute(
            "INSERT INTO clone_jobs (url, destination, project_name, context_id, process_id) "
            "VALUES ('https://h/o/r', ?, 'P', 1, 1)", (dest,))
        db.commit()
        return cur.lastrowid


def test_progress_percent_parsed_from_latest_line(app):
    job_id = _job(app, os.path.join(app.config["allowed_root"], "p"))
    provider = _StubProvider(stderr=["Cloning into 'p'...", "Receiving objects:  12%", "Receiving objects:  47% (5/10)"])
    with app.app_context():
        job, percent = clone.poll_job(get_db(), provider, app.config["ALLOWED_PROJECT_ROOTS"], job_id)
    assert job.status == "RUNNING" and percent == 47


def test_cancel_running_job_stops_and_removes_partial(app):
    root = app.config["allowed_root"]
    dest = os.path.join(root, "partial")
    os.makedirs(os.path.join(dest, ".git"))
    job_id = _job(app, dest)
    provider = _StubProvider()
    with app.app_context():
        job = clone.cancel_job(get_db(), provider, app.config["ALLOWED_PROJECT_ROOTS"], job_id)
    assert job.status == "CANCELLED" and provider.stopped == [1] and provider.destroyed == [1]
    assert not os.path.exists(dest)


def test_partial_cleanup_never_touches_allowed_root_or_outside(app, tmp_path):
    roots = app.config["ALLOWED_PROJECT_ROOTS"]
    outside = tmp_path / "outside"
    outside.mkdir()
    clone._remove_partial(str(outside), roots)
    clone._remove_partial(roots[0], roots)
    assert outside.exists() and os.path.isdir(roots[0])


def test_failed_process_reports_last_stderr_line(app):
    dest = os.path.join(app.config["allowed_root"], "f")
    job_id = _job(app, dest)
    provider = _StubProvider("FAILED", 128, ["Cloning into 'f'...", "fatal: repository not found", ""])
    with app.app_context():
        job, _ = clone.poll_job(get_db(), provider, app.config["ALLOWED_PROJECT_ROOTS"], job_id)
    assert job.status == "FAILED" and job.message == "fatal: repository not found"
