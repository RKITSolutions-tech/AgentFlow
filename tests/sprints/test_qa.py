"""Sprint QA checklist (docs/SPRINT_PLANNING_AND_BACKLOG.md §51)."""
import os

import pytest

from app.acceptance import models as acceptance_models
from app.db import get_db
from app.pipelines.manager import PipelineManager
from app.sprints import persistence as sprints
from app.sprints import qa
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def env(app, client):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with open(os.path.join(repo, "test_qa_dummy.py"), "w") as fh:
        fh.write("def test_pass():\n    assert True\n\ndef test_fail():\n    assert False\n")
    with app.app_context():
        db = get_db()
        sprint_id = sprints.create_sprint(db, project_id, "S1")
        yield type("E", (), {"app": app, "client": client, "db": db, "project_id": project_id,
                             "repo": repo, "sprint_id": sprint_id, "sprint": sprints.get_sprint(db, sprint_id)})


def _base(pid, sid):
    return f"/projects/{pid}/sprints/{sid}/qa"


# -- default_checklist / waive / include -------------------------------------


def test_default_checklist_seeds_once_and_is_approved(env):
    created = qa.default_checklist(env.db, env.project_id, env.sprint_id)
    assert created == len(acceptance_models.list_criteria(env.db, env.project_id, sprint_id=env.sprint_id))
    checks = acceptance_models.list_criteria(env.db, env.project_id, sprint_id=env.sprint_id)
    assert checks and all(c.status == "APPROVED" for c in checks)

    # Idempotent: calling again does not duplicate titles already present.
    assert qa.default_checklist(env.db, env.project_id, env.sprint_id) == 0
    assert len(acceptance_models.list_criteria(env.db, env.project_id, sprint_id=env.sprint_id)) == len(checks)


def test_qa_view_renders_checklist(env):
    html = env.client.get(_base(env.project_id, env.sprint_id)).get_data(as_text=True)
    assert "Login" in html or "login" in html.lower()
    assert "Run QA suite" in html


def test_waive_and_include_round_trip(env):
    qa.default_checklist(env.db, env.project_id, env.sprint_id)
    c = acceptance_models.list_criteria(env.db, env.project_id, sprint_id=env.sprint_id)[0]

    resp = env.client.post(
        f"{_base(env.project_id, env.sprint_id)}/{c.id}/toggle", data={"included": "0", "by": "dev"}, headers=AJAX
    )
    assert resp.status_code == 200, resp.get_json()
    assert acceptance_models.get(env.db, c.id).status == "WAIVED"

    resp = env.client.post(
        f"{_base(env.project_id, env.sprint_id)}/{c.id}/toggle", data={"included": "1", "by": "dev"}, headers=AJAX
    )
    assert resp.status_code == 200, resp.get_json()
    assert acceptance_models.get(env.db, c.id).status == "APPROVED"


# -- run_suite -----------------------------------------------------------------


def test_run_suite_resolves_pass_and_fail_and_re_run_flips_result(env):
    passing = acceptance_models.create(
        env.db, env.project_id, "Dummy passes", sprint_id=env.sprint_id, status="APPROVED",
        created_by="dev", pytest_node_id="test_qa_dummy.py::test_pass",
    )
    failing = acceptance_models.create(
        env.db, env.project_id, "Dummy fails", sprint_id=env.sprint_id, status="APPROVED",
        created_by="dev", pytest_node_id="test_qa_dummy.py::test_fail",
    )
    manager = PipelineManager(env.app.config)
    execution_id = qa.run_suite(env.db, manager, env.project_id, env.sprint, repository_id=_repo_id(env))

    assert acceptance_models.get(env.db, passing).status == "VERIFIED"
    assert acceptance_models.get(env.db, failing).status == "FAILED"
    ev_pass = acceptance_models.list_evidence(env.db, passing)
    assert ev_pass and ev_pass[0].evidence_type == "TEST_RESULT" and ev_pass[0].state == "LINKED"

    # Fix the failing test on disk and re-run: FAILED -> VERIFIED without a
    # manual re-approve in between (service.sync_qa_results re-opens first).
    with open(os.path.join(env.repo, "test_qa_dummy.py"), "w") as fh:
        fh.write("def test_pass():\n    assert True\n\ndef test_fail():\n    assert True\n")
    qa.run_suite(env.db, manager, env.project_id, env.sprint, repository_id=_repo_id(env))
    assert acceptance_models.get(env.db, failing).status == "VERIFIED"


def test_run_suite_skips_waived_and_nodeless_checks(env):
    waived = acceptance_models.create(
        env.db, env.project_id, "Waived check", sprint_id=env.sprint_id, status="APPROVED",
        created_by="dev", pytest_node_id="test_qa_dummy.py::test_pass",
    )
    from app.acceptance import service

    service.waive(env.db, waived, "dev", "not needed")
    no_node = acceptance_models.create(
        env.db, env.project_id, "No node id yet", sprint_id=env.sprint_id, status="APPROVED", created_by="dev",
    )
    manager = PipelineManager(env.app.config)
    with pytest.raises(qa.QAError):
        qa.run_suite(env.db, manager, env.project_id, env.sprint, repository_id=_repo_id(env))
    assert acceptance_models.get(env.db, waived).status == "WAIVED"
    assert acceptance_models.get(env.db, no_node).status == "APPROVED"


def test_qa_run_route(env):
    acceptance_models.create(
        env.db, env.project_id, "Dummy passes", sprint_id=env.sprint_id, status="APPROVED",
        created_by="dev", pytest_node_id="test_qa_dummy.py::test_pass",
    )
    resp = env.client.post(f"{_base(env.project_id, env.sprint_id)}/run", headers=AJAX)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["execution_id"]


def _repo_id(env):
    with env.app.app_context():
        from app.projects import models as project_models

        project = project_models.get_project(env.db, env.project_id)
        return project.repositories[0].id
