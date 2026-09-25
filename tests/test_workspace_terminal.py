import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app.execution.host import HostExecutionProvider
from app.security import PathNotAllowedError
from app.workspace import terminal, terminal_models
from tests.conftest import create_project_with_repo

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


def _create_project_with_repo(client, allowed_root, repo_name="repo-a"):
    _project_id, repo_path = create_project_with_repo(client, allowed_root, repo_name)
    return repo_path


@pytest.fixture
def provider(app):
    return HostExecutionProvider(
        app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"]
    )


@pytest.fixture
def tmp_repo(app):
    repo_path = Path(app.config["allowed_root"]) / "test-repo"
    repo_path.mkdir()
    return repo_path


@pytest.fixture
def repo(app, tmp_repo):
    # terminal_sessions.repo_id has a FK to repositories(id), so direct
    # terminal.create_session() calls (bypassing the route layer) need a
    # real repository row, not just a directory on disk.
    from app.db import get_db
    from app.projects import models as project_models

    with app.app_context():
        db = get_db()
        project_id = project_models.create_project(db, "Proj")
        repo_id = project_models.add_repository(
            db,
            project_id,
            "test-repo",
            str(tmp_repo),
            app.config["ALLOWED_PROJECT_ROOTS"],
            is_primary=True,
        )
        return project_models.get_repository(db, project_id, repo_id)


@pytest.fixture
def created_sessions():
    # Tracks sessions created directly via terminal.create_session (bypassing
    # the route layer) so the underlying tmux session is always cleaned up,
    # even if an assertion fails mid-test.
    created = []
    yield created
    for provider, db, session in created:
        try:
            terminal.kill_session(provider, db, session)
        except Exception:
            subprocess.run(
                ["tmux", "kill-session", "-t", session.tmux_session_name],
                capture_output=True,
            )


def _poll_until(predicate, timeout=5.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _has_tmux_session(name: str) -> bool:
    result = subprocess.run(["tmux", "has-session", "-t", name], capture_output=True)
    return result.returncode == 0


class TestCreateSession:
    def test_create_session_starts_tmux(self, app, provider, repo, created_sessions):
        with app.app_context():
            from app.db import get_db

            db = get_db()
            session = terminal.create_session(
                provider,
                db,
                app.config["DATABASE_PATH"],
                repo_id=repo.id,
                repo_root=repo.path,
                allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"],
                label="main",
            )
            created_sessions.append((provider, db, session))

        assert session.status == "RUNNING"
        assert _has_tmux_session(session.tmux_session_name)

    def test_create_session_rejects_path_outside_root(self, app, provider, tmp_path, created_sessions):
        outside = tmp_path / "outside"
        outside.mkdir()

        with app.app_context():
            from app.db import get_db

            db = get_db()
            with pytest.raises(PathNotAllowedError):
                terminal.create_session(
                    provider,
                    db,
                    app.config["DATABASE_PATH"],
                    repo_id=1,
                    repo_root=str(outside),
                    allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"],
                )


class TestInputAndOutput:
    def test_send_input_and_capture_output(self, app, provider, repo, created_sessions):
        with app.app_context():
            from app.db import get_db

            db = get_db()
            session = terminal.create_session(
                provider,
                db,
                app.config["DATABASE_PATH"],
                repo_id=repo.id,
                repo_root=repo.path,
                allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"],
            )
            created_sessions.append((provider, db, session))

            terminal.send_input(provider, db, session, text="echo terminal-test-marker")
            terminal.send_input(provider, db, session, key="Enter")

            def marker_seen():
                events = terminal_models.list_terminal_events(db, session.id)
                return any("terminal-test-marker" in e.data for e in events)

            assert _poll_until(marker_seen, timeout=8.0)

    def test_tailer_restart_resumes_without_duplicating_output(
        self, app, provider, repo, created_sessions
    ):
        # Regression test: a tailer restarted after an AgentFlow process
        # restart must resume from where it left off (tracked via
        # terminal_sessions.pipe_offset), not re-read the whole pipe file
        # from byte 0 and re-emit already-recorded output as new events.
        with app.app_context():
            from app.db import get_db

            db = get_db()
            session = terminal.create_session(
                provider,
                db,
                app.config["DATABASE_PATH"],
                repo_id=repo.id,
                repo_root=repo.path,
                allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"],
            )
            created_sessions.append((provider, db, session))

            terminal.send_input(provider, db, session, text="echo before-restart")
            terminal.send_input(provider, db, session, key="Enter")

            def before_marker_seen():
                events = terminal_models.list_terminal_events(db, session.id)
                return any("before-restart" in e.data for e in events)

            assert _poll_until(before_marker_seen, timeout=8.0)
            # `before_marker_seen` can go true as soon as the echoed input
            # line lands, before the command's own output line has been
            # tailed; give it a moment to settle before snapshotting.
            time.sleep(0.5)
            events_before = terminal_models.list_terminal_events(db, session.id)
            count_before = len(events_before)

            # Simulate the in-process tailer thread dying (e.g. AgentFlow
            # restarting) without the tmux session itself going away.
            terminal._manager.stop_tailer(session.id)
            time.sleep(0.3)

            refreshed = terminal_models.get_terminal_session(db, session.id)
            assert terminal.ensure_running(
                provider, db, app.config["DATABASE_PATH"], refreshed
            )

            # Give the restarted tailer a moment; it should not re-emit
            # anything already recorded before the "restart".
            time.sleep(0.5)
            events_after_restart = terminal_models.list_terminal_events(db, session.id)
            assert len(events_after_restart) == count_before
            assert [e.id for e in events_after_restart] == [e.id for e in events_before]

            terminal.send_input(provider, db, session, text="echo after-restart")
            terminal.send_input(provider, db, session, key="Enter")

            def after_marker_seen():
                events = terminal_models.list_terminal_events(db, session.id)
                return any("after-restart" in e.data for e in events)

            assert _poll_until(after_marker_seen, timeout=8.0)

    def test_after_id_skips_seen_events(self, app, provider, repo, created_sessions):
        with app.app_context():
            from app.db import get_db

            db = get_db()
            session = terminal.create_session(
                provider,
                db,
                app.config["DATABASE_PATH"],
                repo_id=repo.id,
                repo_root=repo.path,
                allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"],
            )
            created_sessions.append((provider, db, session))

            first_id = terminal_models.add_terminal_event(db, session.id, "first\n")
            terminal_models.add_terminal_event(db, session.id, "second\n")

            events = terminal_models.list_terminal_events(db, session.id, after_id=first_id)

        assert [e.data for e in events] == ["second\n"]


class TestKillSession:
    def test_kill_session_stops_tmux(self, app, provider, repo, created_sessions):
        with app.app_context():
            from app.db import get_db

            db = get_db()
            session = terminal.create_session(
                provider,
                db,
                app.config["DATABASE_PATH"],
                repo_id=repo.id,
                repo_root=repo.path,
                allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"],
            )
            created_sessions.append((provider, db, session))
            name = session.tmux_session_name

            terminal.kill_session(provider, db, session)
            updated = terminal_models.get_terminal_session(db, session.id)

        assert updated.status == "KILLED"
        assert not _has_tmux_session(name)


class TestTerminalRoutes:
    def test_create_input_stream_kill_roundtrip(self, client, app):
        repo_path = _create_project_with_repo(client, app.config["allowed_root"])

        create_resp = client.post("/projects/1/repos/1/terminal")
        assert create_resp.status_code == 201
        term_id = create_resp.get_json()["id"]

        input_resp = client.post(
            f"/projects/1/repos/1/terminal/{term_id}/input",
            json={"text": "echo route-test-marker"},
        )
        assert input_resp.status_code == 200
        client.post(f"/projects/1/repos/1/terminal/{term_id}/input", json={"key": "Enter"})

        def marker_seen():
            resp = client.get(f"/projects/1/repos/1/terminal/{term_id}/stream?after_id=0")
            return b"route-test-marker" in resp.data

        assert _poll_until(marker_seen, timeout=8.0)

        kill_resp = client.post(f"/projects/1/repos/1/terminal/{term_id}/kill")
        assert kill_resp.status_code == 200

    def test_unknown_repo_404s(self, client, app):
        _create_project_with_repo(client, app.config["allowed_root"])
        resp = client.get("/projects/1/repos/999/terminal")
        assert resp.status_code == 404

    def test_mismatched_repo_404s(self, client, app):
        allowed_root = app.config["allowed_root"]
        _create_project_with_repo(client, allowed_root, repo_name="repo-a")
        _create_project_with_repo(client, allowed_root, repo_name="repo-b")

        create_resp = client.post("/projects/1/repos/1/terminal")
        term_id = create_resp.get_json()["id"]

        # term_id belongs to repo 1, not repo 2
        resp = client.get(f"/projects/1/repos/2/terminal/{term_id}/stream")
        assert resp.status_code == 404

        client.post(f"/projects/1/repos/1/terminal/{term_id}/kill")
