import os
import signal
import sqlite3
import sys
import time

import pytest

from app import create_app
from app.config import Config
from app.runs import artifacts, models
from app.runs.executor import pid_alive
from app.runs.security import parse_extra_patterns, redact
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}


def _wait_for_status(app, run_id, wanted, timeout=15.0):
    deadline = time.monotonic() + timeout
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        while time.monotonic() < deadline:
            run = models.get_run(conn, run_id)
            if run.status in wanted:
                return run
            time.sleep(0.05)
        raise AssertionError(f"run {run_id} stuck at {run.status}")
    finally:
        conn.close()


def _start(client, project_id, repo_id, commands, **extra):
    resp = client.post(
        f"/projects/{project_id}/runs",
        data={"repository_id": repo_id, "commands": commands, **extra},
        headers=AJAX,
    )
    return resp


@pytest.fixture
def project(client, app):
    project_id, _ = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        from app.db import get_db

        repo_id = get_db().execute("SELECT id FROM repositories").fetchone()["id"]
    return project_id, repo_id


def test_run_records_status_timing_and_artifacts(client, app, project):
    project_id, repo_id = project
    cmd = f"{sys.executable} -c \"print('hello'); open('out.txt','w').write('artifact')\""
    resp = _start(client, project_id, repo_id, cmd, collect="out.txt")
    assert resp.status_code == 200
    run_id = resp.get_json()["run_id"]

    run = _wait_for_status(app, run_id, ("COMPLETED", "FAILED"))
    app.extensions["run_manager"].join(run_id, 5)
    assert run.status == "COMPLETED"
    step = run.steps[0]
    assert step.status == "PASSED" and step.exit_code == 0
    assert step.started_at and step.completed_at and step.duration >= 0
    assert "hello" in step.reply

    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    stored = models.list_artifacts(conn, run_id)
    assert {a.kind for a in stored} == {"log", "output"}
    root = app.extensions["run_manager"].artifact_root
    log = next(a for a in stored if a.kind == "log")
    assert b"hello" in artifacts.read_artifact(root, log)
    output = next(a for a in stored if a.kind == "output")
    assert artifacts.read_artifact(root, output) == b"artifact"
    assert [e.event_type for e in models.list_events(conn, run_id)][:2] == [
        "RunCreated",
        "RunStarted",
    ]
    conn.close()

    page = client.get(f"/projects/{project_id}/runs/{run_id}")
    assert page.status_code == 200 and b"hello" in page.data
    assert client.get(f"/projects/{project_id}/runs").status_code == 200
    download = client.get(
        f"/projects/{project_id}/runs/{run_id}/artifacts/{output.id}"
    )
    assert download.data == b"artifact"


def test_failing_step_stops_run_and_skips_rest(client, app, project):
    project_id, repo_id = project
    cmds = f"{sys.executable} -c \"import sys; sys.exit(3)\"\necho never"
    run_id = _start(client, project_id, repo_id, cmds).get_json()["run_id"]
    run = _wait_for_status(app, run_id, ("FAILED", "COMPLETED"))
    assert run.status == "FAILED"
    assert [s.status for s in run.steps] == ["FAILED", "SKIPPED"]
    assert run.steps[0].exit_code == 3


def test_pause_resume_and_stop(client, app, project):
    project_id, repo_id = project
    run_id = _start(client, project_id, repo_id, "sleep 30").get_json()["run_id"]
    _wait_for_status(app, run_id, ("RUNNING",))
    time.sleep(0.3)  # let the process start

    assert client.post(f"/projects/{project_id}/runs/{run_id}/pause", headers=AJAX).status_code == 200
    assert _wait_for_status(app, run_id, ("PAUSED",)).status == "PAUSED"
    assert client.post(f"/projects/{project_id}/runs/{run_id}/pause", headers=AJAX).status_code == 409
    assert client.post(f"/projects/{project_id}/runs/{run_id}/resume", headers=AJAX).status_code == 200
    assert _wait_for_status(app, run_id, ("RUNNING",)).status == "RUNNING"

    assert client.post(f"/projects/{project_id}/runs/{run_id}/stop", headers=AJAX).status_code == 200
    run = _wait_for_status(app, run_id, ("CANCELLED",))
    assert run.steps[0].status == "CANCELLED"

    # A finished run can be restarted as a new run.
    resp = client.post(f"/projects/{project_id}/runs/{run_id}/restart", headers=AJAX)
    new_id = resp.get_json()["run_id"]
    assert new_id != run_id
    client.post(f"/projects/{project_id}/runs/{new_id}/stop", headers=AJAX)
    _wait_for_status(app, new_id, ("CANCELLED",))


def test_restart_rejected_while_running(client, app, project):
    project_id, repo_id = project
    run_id = _start(client, project_id, repo_id, "sleep 30").get_json()["run_id"]
    _wait_for_status(app, run_id, ("RUNNING",))
    assert client.post(f"/projects/{project_id}/runs/{run_id}/restart", headers=AJAX).status_code == 409
    client.post(f"/projects/{project_id}/runs/{run_id}/stop", headers=AJAX)
    _wait_for_status(app, run_id, ("CANCELLED",))


def test_unknown_run_and_validation(client, app, project):
    project_id, repo_id = project
    assert client.get(f"/projects/{project_id}/runs/999").status_code == 404
    assert client.post(f"/projects/{project_id}/runs/999/stop", headers=AJAX).status_code == 404
    assert _start(client, project_id, repo_id, "   \n# c").status_code == 400
    assert _start(client, project_id, repo_id, "echo 'unterminated").status_code == 400
    assert _start(client, project_id, repo_id, "echo hi", timeout="-1").status_code == 400
    assert client.get("/projects/999/runs").status_code == 404


def test_restart_reconciliation_blocks_run_with_missing_process(app, project):
    project_id, repo_id = project
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    run_id = models.create_run(conn, project_id, repo_id, "orphan", [{"command": "sleep 1"}])
    models.set_run_status(conn, run_id, "RUNNING")
    models.mark_step_started(conn, models.list_steps(conn, run_id)[0].id)
    conn.close()

    blocked = app.extensions["run_manager"].reconcile()
    assert blocked == [run_id]
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    run = models.get_run(conn, run_id)
    assert run.status == "BLOCKED"
    assert "missing" in run.status_reason
    assert run.steps[0].status == "BLOCKED"
    conn.close()


def test_reconciliation_watches_live_orphan_process(app, project):
    import subprocess

    project_id, repo_id = project
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        manager = app.extensions["run_manager"]
        conn = sqlite3.connect(app.config["DATABASE_PATH"])
        conn.row_factory = sqlite3.Row
        from app.execution import models as exec_models

        ctx = exec_models.create_execution_context(conn, "host", "/tmp", {})
        pid = exec_models.create_process(conn, ctx, "host", "sleep 30", "/tmp")
        exec_models.mark_process_started(conn, pid, proc.pid)
        run_id = models.create_run(conn, project_id, repo_id, "live", [{"command": "sleep 30"}])
        models.set_run_status(conn, run_id, "RUNNING")
        step = models.list_steps(conn, run_id)[0]
        models.mark_step_started(conn, step.id)
        models.set_step_process(conn, step.id, pid)
        conn.close()

        assert manager.reconcile() == []  # still alive: left running, watched
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        run = _wait_for_status(app, run_id, ("BLOCKED",), timeout=10)
        assert "result unknown" in run.status_reason
    finally:
        if proc.poll() is None:
            proc.kill()


def test_pid_alive_ignores_zombies():
    import subprocess

    proc = subprocess.Popen(["true"])
    time.sleep(0.3)
    assert not pid_alive(proc.pid)  # exited, unreaped
    proc.wait()
    assert not pid_alive(None)


# -- redaction -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "API_KEY=abcd1234efgh",
        'export TOKEN="s3cr3t value"',
        "Authorization: Bearer abcdef1234567890",
        '{"password": "hunter2"}',
        "key sk-abcdefghijklmnopqrstuvwx",
        "ghp_" + "a" * 30,
        "AKIAABCDEFGHIJKLMNOP",
        "postgres://user:pa55word@host/db",
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_redact_masks_secrets(text):
    clean, was_redacted = redact(text)
    assert was_redacted
    assert "[REDACTED]" in clean
    for leaked in ("abcd1234efgh", "s3cr3t", "abcdef1234567890", "hunter2", "pa55word", "AKIAABC"):
        assert leaked not in clean


def test_redact_preserves_plain_text_and_supports_custom_patterns():
    assert redact("all tests passed: 12 ok") == ("all tests passed: 12 ok", False)
    clean, hit = redact("ticket ACME-1234 done", (r"ACME-\d+",))
    assert hit and "ACME-1234" not in clean
    assert redact("") == ("", False)
    assert parse_extra_patterns(None) == ()
    assert parse_extra_patterns('["a+"]') == ("a+",)
    with pytest.raises(RuntimeError):
        parse_extra_patterns("not json")
    with pytest.raises(RuntimeError):
        parse_extra_patterns('["("]')


def test_persisted_log_and_command_are_redacted(client, app, project):
    project_id, repo_id = project
    cmd = f"{sys.executable} -c \"print('API_KEY=supersecretvalue1')\" --token=abc123456"
    run_id = _start(client, project_id, repo_id, cmd).get_json()["run_id"]
    run = _wait_for_status(app, run_id, ("COMPLETED", "FAILED"))
    app.extensions["run_manager"].join(run_id, 5)
    step = run.steps[0]
    assert step.command_redacted and "abc123456" not in step.command
    assert step.status == "PASSED"
    assert "supersecretvalue1" not in (step.reply or "")

    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    log = models.list_artifacts(conn, run_id, kind="log")[0]
    conn.close()
    content = artifacts.read_artifact(app.extensions["run_manager"].artifact_root, log)
    # The real command ran (the secret reached stdout) but never persisted.
    assert b"API_KEY=[REDACTED]" in content
    assert b"supersecretvalue1" not in content and log.redacted

    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    summaries = [r[0] for r in conn.execute("SELECT command_summary FROM processes")]
    events = [r[0] for r in conn.execute("SELECT data FROM process_events")]
    conn.close()
    assert not any("abc123456" in t or "supersecretvalue1" in t for t in summaries + events)

    # A redacted command cannot be replayed.
    resp = client.post(f"/projects/{project_id}/runs/{run_id}/restart", headers=AJAX)
    assert resp.status_code == 409


# -- artifacts -----------------------------------------------------------


def test_artifact_paths_cannot_escape(tmp_path):
    root = str(tmp_path / "root")
    os.makedirs(root)
    with pytest.raises(artifacts.ArtifactPathError):
        artifacts._resolve_within(root, "../outside")
    bad = models.Artifact(1, 1, 1, "log", "x", "../../etc/passwd", 0, False, "")
    with pytest.raises(artifacts.ArtifactPathError):
        artifacts.read_artifact(root, bad)
    assert artifacts._safe_name("../../evil name.txt") == "evil_name.txt"


def test_collect_skips_files_outside_working_directory(client, app, project, tmp_path):
    project_id, repo_id = project
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    cmd = f"{sys.executable} -c pass"
    run_id = _start(client, project_id, repo_id, cmd, collect="../../secret.txt\nmissing.txt").get_json()["run_id"]
    _wait_for_status(app, run_id, ("COMPLETED", "FAILED"))
    app.extensions["run_manager"].join(run_id, 5)
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    assert [a.kind for a in models.list_artifacts(conn, run_id)] == ["log"]
    skipped = [e for e in models.list_events(conn, run_id) if e.event_type == "ArtifactSkipped"]
    conn.close()
    assert len(skipped) == 2


# -- models --------------------------------------------------------------


def test_list_runs_filters_and_paginates(app, project):
    project_id, repo_id = project
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    ids = [models.create_run(conn, project_id, repo_id, f"r{i}", [{"command": "true"}]) for i in range(3)]
    models.set_run_status(conn, ids[0], "FAILED")
    assert models.count_runs(conn, project_id) == 3
    assert [r.id for r in models.list_runs(conn, project_id, limit=2)] == [ids[2], ids[1]]
    assert [r.id for r in models.list_runs(conn, project_id, status="FAILED")] == [ids[0]]
    assert [r.id for r in models.list_runs(conn, project_id, limit=2, offset=2)] == [ids[0]]
    conn.close()


# -- unsafe bind ---------------------------------------------------------


def test_unsafe_bind_requires_explicit_opt_in(monkeypatch, caplog):
    monkeypatch.setenv("AGENTFLOW_HOST", "0.0.0.0")
    monkeypatch.delenv("AGENTFLOW_ALLOW_UNSAFE_BIND", raising=False)
    with pytest.raises(RuntimeError, match="AGENTFLOW_ALLOW_UNSAFE_BIND"):
        Config.from_env()

    monkeypatch.setenv("AGENTFLOW_ALLOW_UNSAFE_BIND", "1")
    with caplog.at_level("WARNING"):
        assert Config.from_env().HOST == "0.0.0.0"
    assert "beyond loopback" in caplog.text

    monkeypatch.delenv("AGENTFLOW_HOST")
    monkeypatch.delenv("AGENTFLOW_ALLOW_UNSAFE_BIND")
    caplog.clear()
    assert Config.from_env().HOST == "127.0.0.1"
    assert caplog.text == ""


def test_config_reads_redaction_patterns(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_REDACT_PATTERNS", '["ACME-\\\\d+"]')
    assert Config.from_env().REDACT_PATTERNS == ("ACME-\\d+",)


@pytest.mark.parametrize(
    "viewport", [{"width": 375, "height": 700}, {"width": 1280, "height": 800}]
)
def test_runs_pages_fit_viewport(app, client, live_server, project, viewport):
    from tests.test_workspace_integration import _new_page

    sync_api = pytest.importorskip("playwright.sync_api")
    project_id, repo_id = project
    long_cmd = f"{sys.executable} -c \"print('x' * 300)\""
    run_id = _start(client, project_id, repo_id, long_cmd).get_json()["run_id"]
    _wait_for_status(app, run_id, ("COMPLETED", "FAILED"))
    app.extensions["run_manager"].join(run_id, 5)

    with sync_api.sync_playwright() as p:
        browser, page = _new_page(p, viewport)
        try:
            for path in (f"/projects/{project_id}/runs", f"/projects/{project_id}/runs/{run_id}"):
                page.goto(f"{live_server}{path}")
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                assert overflow <= 0, f"{path} scrolls horizontally by {overflow}px"
            assert page.query_selector(".run-log") is not None
        finally:
            browser.close()
