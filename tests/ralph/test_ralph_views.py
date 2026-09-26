import os
import subprocess
import sys

import pytest

from app.db import get_db
from app.pipelines import persistence
from app.ralph import models
from tests.conftest import create_project_with_repo

PY = sys.executable
AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def project(app, client):
    app.config["PLANNING_AGENT"] = "fake"
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    for args in (["init", "-q"], ["config", "user.email", "t@e.com"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    with app.app_context():
        persistence.create_pipeline(
            get_db(),
            {"name": "verify", "elements": [{"name": "t", "type": "COMMAND", "config": {"command": f'{PY} -c "pass"'}}]},
            project_id,
        )
    return project_id


def test_list_and_start_validation(client, project):
    base = f"/projects/{project}/ralph"
    assert client.get(base).status_code == 200
    bad = client.post(base, data={"task": " ", "verification_pipeline": "verify", "repository_id": "1"}, headers=AJAX)
    assert bad.status_code == 400
    assert client.post(base, data={"task": "x", "verification_pipeline": "", "repository_id": "1"}, headers=AJAX).status_code == 400
    assert client.post(base, data={"task": "x", "verification_pipeline": "verify"}, headers=AJAX).status_code == 400


def test_start_view_steer_and_controls(app, client, project):
    base = f"/projects/{project}/ralph"
    with app.app_context():
        run_id = models.create_run(
            get_db(), project, 1, "Blocked run", "do it", "verify", acceptance=["it works"],
        )
        models.update_run(get_db(), run_id, status="BLOCKED", needs_attention=1, reason="No progress: x")
        it = models.add_iteration(get_db(), run_id, 1, "the prompt", False)
        models.update_iteration(get_db(), it, reply="the reply", status="NO_PROGRESS", analysis="evidence text")
    page = client.get(f"{base}/{run_id}").get_data(as_text=True)
    for expected in ("Blocked run", "Needs attention", "the prompt", "the reply", "evidence text", "Continue"):
        assert expected in page
    status = client.get(f"{base}/{run_id}/status.json").get_json()
    assert status["status"] == "BLOCKED" and status["iterations"] == [{"number": 1, "status": "NO_PROGRESS"}]

    assert client.post(f"{base}/{run_id}/steer", data={"message": " "}, headers=AJAX).status_code == 409
    assert client.post(f"{base}/{run_id}/steer", data={"message": "use the old uploader"}, headers=AJAX).status_code == 200
    assert client.post(f"{base}/{run_id}/pause", headers=AJAX).status_code == 409  # blocked runs cannot pause
    assert client.post(f"{base}/{run_id}/resume", headers=AJAX).status_code == 409
    assert client.post(f"{base}/{run_id}/cancel", headers=AJAX).status_code == 200
    with app.app_context():
        assert models.get_run(get_db(), run_id).status == "CANCELLED"
    assert client.post(f"{base}/{run_id}/cancel", headers=AJAX).status_code == 409


def test_start_run_end_to_end_with_fake_agent(app, client, project):
    base = f"/projects/{project}/ralph"
    with app.app_context():
        run_id = models.create_run(
            get_db(), project, 1, "e2e", "make a file", "verify",
            script=[{"action": "write_file", "path": "f.txt", "content": "x"}, {"action": "complete"}],
        )
    app.extensions["ralph_manager"].start(run_id)
    assert app.extensions["ralph_manager"].join(run_id, 30)
    page = client.get(f"{base}/{run_id}").get_data(as_text=True)
    assert "Completed" in page and "Verification pipeline run #" in page and "f.txt" in page


def test_runs_scoped_to_project(app, client, project):
    with app.app_context():
        run_id = models.create_run(get_db(), project, 1, "r", "t", "verify")
    other = int(client.post("/projects/new", data={"name": "Other"}).headers["Location"].rsplit("/", 1)[-1])
    assert client.get(f"/projects/{other}/ralph/{run_id}").status_code == 404
    assert client.post(f"/projects/{other}/ralph/{run_id}/cancel", headers=AJAX).status_code == 404
