import os
import subprocess
import sys

import pytest

from app.acceptance import models, service, templates
from app.acceptance.models import AcceptanceError
from app.agents.fake import FakeAgentAdapter
from app.artifacts import collector
from app.backlog import persistence as backlog
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.pipelines import persistence as pipelines
from app.pipelines.engine import PipelineEngine
from app.ralph import models as ralph
from app.ralph.orchestrator import RalphOrchestrator
from app.sprints import persistence as sprints
from app.sprints import workflow
from app.agents.base import AgentContext
from app.sprints.planning_agent import PlanningAgent
from tests.conftest import create_project_with_repo

PY = sys.executable
AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def env(app, client, tmp_path):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    for args in (["init", "-q"], ["config", "user.email", "t@e.com"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    with app.app_context():
        db = get_db()
        root = str(tmp_path / "art")
        app.config["ARTIFACT_DIR"] = root
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        factory = lambda c: FakeAgentAdapter(c)
        engine = PipelineEngine(db, provider, root, factory, sleep=lambda s: None)
        pipelines.create_pipeline(
            db,
            {"name": "verify", "elements": [
                {"name": "unit_tests", "type": "TEST", "config": {"command": f'{PY} -c "print(1)"', "collect": ["*.png"]}}]},
            project_id,
        )
        yield type("E", (), {"app": app, "client": client, "db": db, "project_id": project_id, "repo": repo,
                             "ralph": RalphOrchestrator(db, provider, engine, factory), "root": root, "engine": engine})


def _run(env, **kw):
    return ralph.create_run(
        env.db, env.project_id, 1, "task", "do it", "verify",
        script=[{"action": "write_file", "path": "shot.png", "content": "x"}, {"action": "complete"}], **kw,
    )


def _criterion(env, run_id=None, **kw):
    return models.create(env.db, env.project_id, kw.pop("title", "Unit tests pass"), ralph_run_id=run_id or _run(env), created_by="dev", **kw)


def test_templates_render_and_validate():
    assert len(templates.list_templates()) == 6
    out = templates.render("performance-threshold", {"metric": "Search latency", "threshold": "200"})
    assert out["title"] == "Search latency is under 200ms"
    with pytest.raises(templates.TemplateError, match="threshold"):
        templates.render("performance-threshold", {"metric": "x"})
    with pytest.raises(templates.TemplateError):
        templates.render("nope")
    assert "{" not in templates.render("unit-tests-green")["title"]


def test_status_transitions_and_approval_independence(env):
    cid = _criterion(env)
    with pytest.raises(AcceptanceError, match="Approve"):
        service.verify(env.db, cid, "rev")
    with pytest.raises(AcceptanceError, match="someone other"):
        service.approve(env.db, cid, "DEV")  # author, case-insensitive
    with pytest.raises(AcceptanceError, match="name"):
        service.approve(env.db, cid, " ")
    service.approve(env.db, cid, "rev")
    c = models.get(env.db, cid)
    assert (c.status, c.approved_by) == ("APPROVED", "rev") and c.approved_at
    with pytest.raises(AcceptanceError, match="evidence"):
        service.verify(env.db, cid, "rev")
    service.verify(env.db, cid, "rev", "Checked by hand")
    c = models.get(env.db, cid)
    assert c.status == "VERIFIED" and c.verified_by == "rev" and c.verified_at
    with pytest.raises(AcceptanceError, match="not allowed"):
        models.set_status(env.db, cid, "FAILED")
    service.reopen(env.db, cid, "rev")
    assert models.get(env.db, cid).status == "APPROVED"
    service.fail(env.db, cid, "rev", "regressed")
    assert models.get(env.db, cid).status == "FAILED"
    service.waive(env.db, cid, "rev", "out of scope")
    assert models.get(env.db, cid).waived_reason == "out of scope"
    with pytest.raises(AcceptanceError):
        service.waive(env.db, cid, "rev", " ")


def test_editing_approved_title_withdraws_approval(env):
    cid = _criterion(env)
    service.approve(env.db, cid, "rev")
    models.update_text(env.db, cid, "Unit tests pass", "more detail", True)
    assert models.get(env.db, cid).status == "APPROVED"
    models.update_text(env.db, cid, "Different wording", "", True)
    c = models.get(env.db, cid)
    assert c.status == "DRAFT" and c.approved_by is None
    service.approve(env.db, cid, "rev")
    service.verify(env.db, cid, "rev", "ok")
    with pytest.raises(AcceptanceError, match="re-open"):
        models.update_text(env.db, cid, "x", "", True)
    with pytest.raises(AcceptanceError, match="draft"):
        models.delete_draft(env.db, cid)


def test_create_needs_owner_and_title(env):
    with pytest.raises(AcceptanceError, match="belongs"):
        models.create(env.db, env.project_id, "x")
    with pytest.raises(AcceptanceError, match="title"):
        models.create(env.db, env.project_id, " ", ralph_run_id=_run(env))


def test_evidence_linking_rules(env):
    run_id = _run(env)
    cid = _criterion(env, run_id)
    other = int(env.client.post("/projects/new", data={"name": "Other"}).headers["Location"].rsplit("/", 1)[-1])
    foreign = collector.store_bytes(env.db, env.root, other, "x.log", b"x", "d")
    with pytest.raises(AcceptanceError, match="not in this project"):
        service.link_evidence(env.db, cid, "ARTIFACT", foreign, "", "rev")
    mine = collector.store_bytes(env.db, env.root, env.project_id, "y.log", b"y", "d")
    eid = service.link_evidence(env.db, cid, "ARTIFACT", mine, "", "rev")
    assert service.link_evidence(env.db, cid, "ARTIFACT", mine, "", "rev") == eid  # idempotent
    with pytest.raises(AcceptanceError, match="note"):
        service.link_evidence(env.db, cid, "MANUAL", None, " ", "rev")
    with pytest.raises(AcceptanceError, match="name"):
        service.link_evidence(env.db, cid, "MANUAL", None, "did it", "")
    with pytest.raises(AcceptanceError, match="step result"):
        service.link_evidence(env.db, cid, "TEST_RESULT", 999, "", "rev")


def test_ralph_run_waits_for_acceptance_then_finalizes(env):
    run_id = _run(env)
    cid = models.create(
        env.db, env.project_id, "Unit tests pass", ralph_run_id=run_id, created_by="dev",
        hints=templates.TEMPLATES["unit-tests-green"]["hints"],
    )
    run = env.ralph.run(run_id)
    assert run.status == "WAITING_FOR_HUMAN" and run.awaiting_acceptance and run.needs_attention
    assert "Unit tests pass (draft)" in run.reason
    with pytest.raises(ValueError, match="Still required"):
        env.ralph.finalize(run_id)

    # verification produced a passing test step: suggested as evidence, not linked yet
    suggestions = models.list_evidence(env.db, cid, "SUGGESTED")
    types = {e.evidence_type for e in suggestions}
    assert "TEST_RESULT" in types and "ARTIFACT" in types and not models.list_evidence(env.db, cid, "LINKED")
    assert any("unit_tests" in e.note for e in suggestions)

    service.approve(env.db, cid, "rev")
    service.accept_evidence(env.db, next(e.id for e in suggestions if e.evidence_type == "TEST_RESULT"), "rev")
    service.verify(env.db, cid, "rev")
    done = env.ralph.finalize(run_id)
    assert done.status == "COMPLETED" and not done.awaiting_acceptance and done.commit_sha


def test_waived_and_advisory_criteria_do_not_block(env):
    run_id = _run(env)
    waived = _criterion(env, run_id, title="Perf")
    service.approve(env.db, waived, "rev")
    service.waive(env.db, waived, "rev", "not measurable here")
    models.create(env.db, env.project_id, "Nice to have", ralph_run_id=run_id, required=False, created_by="dev")
    assert env.ralph.run(run_id).status == "COMPLETED"


def test_run_without_criteria_completes_as_before(env):
    assert env.ralph.run(_run(env)).status == "COMPLETED"


def test_unblock_from_acceptance_wait_iterates_again(env):
    run_id = _run(env)
    _criterion(env, run_id)
    assert env.ralph.run(run_id).awaiting_acceptance
    env.ralph.unblock(run_id, "please cover the edge case")
    assert not ralph.get_run(env.db, run_id).awaiting_acceptance


def test_suggestions_are_scoped_and_not_resurfaced(env):
    run_id = _run(env)
    cid = _criterion(env, run_id, title="Screenshot matches design", hints=templates.TEMPLATES["screenshot-matches-design"]["hints"])
    env.ralph.run(run_id)
    first = models.list_evidence(env.db, cid, "SUGGESTED")
    assert any(e.evidence_type == "ARTIFACT" and "shot.png" in e.note for e in first)
    service.dismiss_evidence(env.db, first[0].id, "rev")
    service.suggest_evidence(env.db, cid)
    assert first[0].id in {e.id for e in models.list_evidence(env.db, cid, "DISMISSED")}
    assert len(models.list_evidence(env.db, cid)) == len(first)  # nothing duplicated
    # a criterion on an unrelated run finds nothing
    lonely = _criterion(env, _run(env), title="Screenshot matches design")
    assert service.suggest_evidence(env.db, lonely) == []


def test_sprint_approval_creates_approved_criteria_for_tasks(env):
    env.app.config["PLANNING_AGENT"] = "fake"
    db = env.db
    sprint_id = sprints.create_sprint(db, env.project_id, "S", goal="g")
    item = backlog.create_item(db, env.project_id, text="thing")
    workflow.select_items(db, sprint_id, [item])
    agent = PlanningAgent(FakeAgentAdapter(db), fake_script_from_items=True)
    workflow.start_planning(db, sprint_id, agent, AgentContext(project_id=env.project_id, working_directory="."))
    workflow.ingest_proposal(db, sprint_id, agent)
    work = sprints.list_work_items(db, sprint_id)[0]
    sprints.update_work_item(db, work.id, status="REVIEWED")
    workflow.approve(db, sprint_id, "Sam")
    criteria = models.list_criteria(db, env.project_id, work_item_id=work.id)
    assert [(c.origin, c.status, c.approved_by) for c in criteria] == [("PLANNING", "APPROVED", "Sam")]
    assert service.task_status(db, work.id, env.project_id)["complete"] is False
    service.verify(db, criteria[0].id, "Sam", "manually checked")
    assert service.task_status(db, work.id, env.project_id)["complete"] is True
    assert service.sync_from_work_items(db, sprint_id, "Sam") == 0  # idempotent


def test_http_workflow(env):
    base = f"/projects/{env.project_id}/acceptance"
    run_id = _run(env)
    page = env.client.get(base).get_data(as_text=True)
    assert "Acceptance criteria" in page and "Screenshot matches design mock" in page
    assert env.client.get(f"{base}/templates.json").get_json()["templates"][0]["key"] == "api-200"

    bad = env.client.post(base, data={"title": "x", "author": "dev"}, headers=AJAX)
    assert bad.status_code == 400  # no owner
    unknown = env.client.post(base, data={"title": "x", "author": "dev", "ralph_run_id": 999}, headers=AJAX)
    assert unknown.status_code == 400
    missing = env.client.post(base, data={"template": "api-200", "author": "dev", "ralph_run_id": run_id}, headers=AJAX)
    assert missing.status_code == 400 and "endpoint" in missing.get_json()["error"]
    made = env.client.post(base, data={"template": "api-200", "field_endpoint": "GET /jobs", "author": "dev", "ralph_run_id": run_id}, headers=AJAX)
    cid = made.get_json()["criterion_id"]
    assert models.get(env.db, cid).title == "GET /jobs returns 200 with the documented schema"
    detail = env.client.get(f"{base}/{cid}").get_data(as_text=True)
    assert "GET /jobs" in detail and "Approve criterion" in detail

    same = env.client.post(f"{base}/{cid}/approve", data={"by": "dev"}, headers=AJAX)
    assert same.status_code == 409 and "someone other" in same.get_json()["error"]
    assert env.client.post(f"{base}/{cid}/approve", data={"by": "rev"}, headers=AJAX).status_code == 200
    assert env.client.post(f"{base}/{cid}/verify", data={"by": "rev"}, headers=AJAX).status_code == 409
    ok = env.client.post(f"{base}/{cid}/evidence", data={"type": "MANUAL", "note": "curl returned 200", "by": "rev"}, headers=AJAX)
    assert ok.status_code == 200
    assert env.client.post(f"{base}/{cid}/verify", data={"by": "rev"}, headers=AJAX).status_code == 200
    assert "Verified" in env.client.get(f"{base}/{cid}").get_data(as_text=True)
    assert env.client.post(f"{base}/{cid}/delete", headers=AJAX).status_code == 409
    assert env.client.post(f"{base}/{cid}/evidence/9999/accept", data={"by": "x"}, headers=AJAX).status_code == 404
    assert env.client.get(f"{base}?status=VERIFIED").get_data(as_text=True).count("GET /jobs") >= 1


def test_http_scoped_to_project(env):
    cid = _criterion(env)
    other = int(env.client.post("/projects/new", data={"name": "Other"}).headers["Location"].rsplit("/", 1)[-1])
    base = f"/projects/{other}/acceptance"
    assert env.client.get(f"{base}/{cid}").status_code == 404
    assert env.client.post(f"{base}/{cid}/approve", data={"by": "rev"}, headers=AJAX).status_code == 404
    assert env.client.post(base, data={"title": "x", "author": "a", "ralph_run_id": 1}, headers=AJAX).status_code == 400


def test_ralph_complete_route(env):
    run_id = _run(env)
    cid = _criterion(env, run_id)
    env.ralph.run(run_id)
    base = f"/projects/{env.project_id}/ralph/{run_id}"
    assert "Complete run" in env.client.get(base).get_data(as_text=True)
    assert env.client.post(f"{base}/complete", headers=AJAX).status_code == 409
    service.approve(env.db, cid, "rev")
    service.verify(env.db, cid, "rev", "checked")
    assert env.client.post(f"{base}/complete", headers=AJAX).status_code == 200
    assert ralph.get_run(env.db, run_id).status == "COMPLETED"
