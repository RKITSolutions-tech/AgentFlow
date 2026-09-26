import csv
import io
import sys
import time

import pytest

from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.pipelines import executions, persistence, visualization
from app.pipelines.engine import PipelineEngine
from tests.conftest import create_project_with_repo

PY = sys.executable
AJAX = {"X-Requested-With": "XMLHttpRequest"}


def _cmd(name, code, **extra):
    return {"name": name, "type": "COMMAND", "config": {"command": f'{PY} -c "{code}"'}, **extra}


@pytest.fixture
def setup(app, client, tmp_path):
    app.config["PLANNING_AGENT"] = "fake"
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        engine = PipelineEngine(get_db(), provider, str(tmp_path / "artifacts"), lambda c: FakeAgentAdapter(c), sleep=lambda s: None)
        yield type("S", (), {"app": app, "client": client, "project_id": project_id, "engine": engine, "db": get_db(), "root": str(tmp_path / "artifacts")})


def _run(setup, *elements, name="v"):
    persistence.create_pipeline(setup.db, {"name": name, "elements": list(elements)}, setup.project_id)
    eid = setup.engine.create(name, setup.project_id, 1)
    setup.engine.run(eid)
    return eid


def _graph(setup, eid):
    return visualization.build_graph(setup.db, executions.get_execution(setup.db, eid))


def test_graph_states_layers_and_edges(setup):
    eid = _run(
        setup,
        _cmd("build", "print(1)"),
        _cmd("flaky", "import sys; sys.exit(1)", compensation={"action": "CONTINUE"}),
        _cmd("after", "print(2)", depends_on=["flaky"]),
        _cmd("off", "print(3)", enabled="DISABLED", depends_on=[]),
        {"name": "gate", "type": "MANUAL_APPROVAL", "config": {"prompt": "?"}, "depends_on": ["build"]},
    )
    g = _graph(setup, eid)
    by = {n["id"]: n for n in g["nodes"]}
    assert by["build"]["state"] == "PASSED" and by["build"]["icon"] and by["build"]["state_label"] == "Passed"
    assert by["flaky"]["state"] == "WARNING"
    assert by["after"]["state"] == "SKIPPED"
    assert by["off"]["state"] == "DISABLED"
    assert by["gate"]["state"] == "WAITING" and g["execution"]["status"] == "PAUSED"
    assert g["execution"]["waiting_step_id"]
    assert [by[n]["layer"] for n in ("build", "flaky", "after")] == [0, 1, 2]
    assert by["off"]["layer"] == 0 and by["gate"]["layer"] == 1
    assert {"from": "build", "to": "flaky", "kind": "dependency"} in g["edges"]
    assert g["layers"] == 3


def test_rigging_is_a_distinct_category_and_teardown_trails(setup):
    eid = _run(
        setup,
        {"name": "srv", "type": "HEALTHCHECK", "phase": "SETUP", "config": {"command": f'{PY} -c "pass"'}},
        _cmd("work", "print(1)"),
        _cmd("clean", "print(2)", phase="TEARDOWN"),
    )
    by = {n["id"]: n for n in _graph(setup, eid)["nodes"]}
    assert by["srv"]["category"] == "RIGGING" and by["work"]["category"] == "DEVELOPMENT"
    assert by["clean"]["layer"] > by["work"]["layer"]


def test_compensation_edges_and_retry_state(setup):
    eid = _run(
        setup,
        _cmd("fix", "print(1)"),
        _cmd("check", "import sys; sys.exit(1)", compensation={"action": "LOOP", "step": "fix", "max_loops": 2}),
    )
    g = _graph(setup, eid)
    comp = [e for e in g["edges"] if e["kind"] == "compensation"]
    assert comp == [{"from": "check", "to": "fix", "kind": "compensation", "action": "LOOP", "label": "loop (max 2)"}]
    assert {n["id"]: n["attempts"] for n in g["nodes"]}["check"] >= 2


def test_step_detail_redacts_configuration_and_lists_attempts(setup):
    eid = _run(
        setup,
        {"name": "leak", "type": "COMMAND",
         "config": {"command": f'{PY} -c "print(1)"', "token": "API_KEY=supersecretvalue123"}},
    )
    ex = executions.get_execution(setup.db, eid)
    detail = visualization.step_detail(setup.db, setup.root, ex, "leak")
    assert "supersecretvalue123" not in str(detail["configuration"])
    assert detail["attempts"][0]["status"] == "PASSED" and detail["attempts"][0]["output"].strip() == "1"
    assert visualization.step_detail(setup.db, setup.root, ex, "nope") is None


def test_execution_pages_and_json(setup):
    eid = _run(setup, _cmd("a", "print('hi')"))
    base = f"/projects/{setup.project_id}/pipelines"
    page = setup.client.get(f"{base}/executions/{eid}").get_data(as_text=True)
    assert "data-pipeline-view" in page and 'data-node="a"' in page and "Passed" in page
    graph = setup.client.get(f"{base}/executions/{eid}/graph.json").get_json()
    assert graph["nodes"][0]["state"] == "PASSED"
    node = setup.client.get(f"{base}/executions/{eid}/nodes/a.json").get_json()
    assert node["attempts"][0]["output"].strip() == "hi"
    assert setup.client.get(f"{base}/executions/{eid}/nodes/zzz.json").status_code == 404
    step_id = node["attempts"][0]["id"]
    log = setup.client.get(f"{base}/executions/{eid}/steps/{step_id}/log")
    assert log.status_code == 200 and b"hi" in log.data
    assert "Pipelines" in setup.client.get(base).get_data(as_text=True)


def test_execution_scoped_to_project(setup):
    eid = _run(setup, _cmd("a", "print(1)"))
    other = int(setup.client.post("/projects/new", data={"name": "Other"}).headers["Location"].rsplit("/", 1)[-1])
    for suffix in ("", "/graph.json", "/nodes/a.json", "/export.csv"):
        assert setup.client.get(f"/projects/{other}/pipelines/executions/{eid}{suffix}").status_code == 404


def test_start_execution_from_form_and_cancel(setup):
    base = f"/projects/{setup.project_id}/pipelines"
    persistence.create_pipeline(
        setup.db, {"name": "slow", "elements": [_cmd("s", "import time; time.sleep(30)")]}, setup.project_id
    )
    resp = setup.client.post(f"{base}/executions", data={"pipeline": "slow", "repository_id": "1", "variables": "a=b"}, headers=AJAX)
    assert resp.status_code == 200
    eid = resp.get_json()["execution_id"]
    assert setup.client.post(f"{base}/executions", data={"pipeline": "ghost", "repository_id": "1"}, headers=AJAX).status_code == 400
    assert setup.client.post(f"{base}/executions", data={"pipeline": "slow"}, headers=AJAX).status_code == 400
    deadline = time.time() + 10
    while time.time() < deadline and executions.get_execution(setup.db, eid).status != "RUNNING":
        time.sleep(0.05)
    time.sleep(0.5)
    assert setup.client.post(f"{base}/executions/{eid}/cancel", headers=AJAX).status_code == 200
    manager = setup.app.extensions["pipeline_manager"]
    assert manager.join(eid, 15)
    assert executions.get_execution(setup.db, eid).status == "CANCELLED"
    assert setup.client.post(f"{base}/executions/{eid}/cancel", headers=AJAX).status_code == 409


def test_manual_decision_over_http_resumes(setup):
    eid = _run(setup, {"name": "gate", "type": "MANUAL_APPROVAL", "config": {"prompt": "ok?"}}, _cmd("after", "print(1)"))
    base = f"/projects/{setup.project_id}/pipelines/executions/{eid}"
    step_id = executions.get_execution(setup.db, eid).waiting_step_id
    bad = setup.client.post(f"{base}/steps/{step_id}/decision", data={"decision": "APPROVED", "by": ""}, headers=AJAX)
    assert bad.status_code == 409
    ok = setup.client.post(f"{base}/steps/{step_id}/decision", data={"decision": "APPROVED", "by": "Sam"}, headers=AJAX)
    assert ok.status_code == 200
    setup.app.extensions["pipeline_manager"].join(eid, 15)
    assert executions.get_execution(setup.db, eid).status == "COMPLETED"


def test_export_json_and_csv_neutralise_formulas(setup):
    eid = _run(setup, _cmd("leak", "print('=HYPERLINK(1)')"))
    base = f"/projects/{setup.project_id}/pipelines/executions/{eid}"
    data = setup.client.get(f"{base}/export.json").get_json()
    assert data["execution"]["status"] == "COMPLETED" and data["steps"][0]["element_name"] == "leak"
    rows = list(csv.reader(io.StringIO(setup.client.get(f"{base}/export.csv").get_data(as_text=True))))
    assert rows[0][0] == "step" and rows[1][-1].startswith("'=")
    assert setup.client.get(f"{base}/export.xml").status_code == 404
