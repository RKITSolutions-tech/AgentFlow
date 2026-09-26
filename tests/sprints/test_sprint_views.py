import pytest

from app.backlog import persistence as backlog
from app.db import get_db
from app.sprints import persistence as sprints

AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def fake_planner(app):
    app.config["PLANNING_AGENT"] = "fake"


@pytest.fixture
def project_id(client, fake_planner):
    resp = client.post("/projects/new", data={"name": "Proj", "description": ""})
    return int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])


def _base(pid):
    return f"/projects/{pid}/sprints"


def _new_items(app, pid, n=2):
    with app.app_context():
        return [backlog.create_item(get_db(), pid, text=f"item {i}") for i in range(n)]


def _create(client, pid, ids, **extra):
    data = {"name": "S1", "goal": "Ship", "item_ids": ids, **extra}
    resp = client.post(_base(pid), data=data, headers=AJAX)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["sprint_id"]


def test_create_sprint_selects_items_and_lists(client, app, project_id):
    ids = _new_items(app, project_id)
    sprint_id = _create(client, project_id, ids, documents="docs/a.md\ndocs/b.md")
    html = client.get(_base(project_id)).get_data(as_text=True)
    assert "S1" in html
    with app.app_context():
        assert [backlog.get_item(get_db(), i).status for i in ids] == ["SELECTED"] * 2
        assert len(sprints.list_document_refs(get_db(), sprint_id)) == 2
    assert "item 0" in client.get(f"{_base(project_id)}/{sprint_id}").get_data(as_text=True)
    assert client.get(f"{_base(project_id)}/new").status_code == 200


def test_create_requires_name(client, project_id):
    resp = client.post(_base(project_id), data={"name": " "}, headers=AJAX)
    assert resp.status_code == 400


def test_plan_review_and_approve_flow(client, app, project_id):
    ids = _new_items(app, project_id)
    sid = _create(client, project_id, ids)
    url = f"{_base(project_id)}/{sid}"

    resp = client.post(f"{url}/plan", headers=AJAX)
    assert resp.get_json()["state"] == "COMPLETE"
    page = client.get(url).get_data(as_text=True)
    assert "Proposed tasks" in page and "Task graph" in page and "Approve sprint" in page

    # Unreviewed agent output blocks approval.
    resp = client.post(f"{url}/approve", data={"approved_by": "Sam"}, headers=AJAX)
    assert resp.status_code == 409 and "acceptance_reviewed" in resp.get_json()["error"]

    with app.app_context():
        work = sprints.list_work_items(get_db(), sid)
    for w in work:
        r = client.post(f"{url}/tasks/{w.id}", data={"title": w.title, "status": "REVIEWED"}, headers=AJAX)
        assert r.status_code == 200
    # Unsigned approval is refused.
    assert client.post(f"{url}/approve", data={"approved_by": ""}, headers=AJAX).status_code == 409
    assert client.post(f"{url}/approve", data={"approved_by": "Sam", "comment": "ok"}, headers=AJAX).status_code == 200
    page = client.get(url).get_data(as_text=True)
    assert "approved by Sam" in page and "Approval history" in page
    assert client.post(f"{url}/revoke", data={"approved_by": "Sam"}, headers=AJAX).status_code == 200


def test_plan_status_endpoint(client, app, project_id):
    sid = _create(client, project_id, _new_items(app, project_id))
    url = f"{_base(project_id)}/{sid}/plan/status"
    assert client.get(url).get_json()["state"] == "IDLE"
    client.post(f"{_base(project_id)}/{sid}/plan", headers=AJAX)
    assert client.get(url).get_json()["state"] == "COMPLETE"


def test_planning_needs_items(client, project_id):
    sid = _create(client, project_id, [])
    resp = client.post(f"{_base(project_id)}/{sid}/plan", headers=AJAX)
    assert resp.status_code == 409 and "at least one" in resp.get_json()["error"]


def test_task_dependencies_reject_cycles(client, app, project_id):
    sid = _create(client, project_id, _new_items(app, project_id))
    client.post(f"{_base(project_id)}/{sid}/plan", headers=AJAX)
    with app.app_context():
        a, b = [w.id for w in sprints.list_work_items(get_db(), sid)]
    url = f"{_base(project_id)}/{sid}/tasks"
    ok = client.post(f"{url}/{b}", data={"set_dependencies": "1", "depends_on": [a]}, headers=AJAX)
    assert ok.status_code == 200
    bad = client.post(f"{url}/{a}", data={"set_dependencies": "1", "depends_on": [b]}, headers=AJAX)
    assert bad.status_code == 409 and "cycle" in bad.get_json()["error"]
    assert "after" in client.get(f"{_base(project_id)}/{sid}").get_data(as_text=True)


def test_sprints_scoped_to_project(client, app, project_id):
    sid = _create(client, project_id, [])
    other = int(client.post("/projects/new", data={"name": "Other"}).headers["Location"].rsplit("/", 1)[-1])
    assert client.get(f"{_base(other)}/{sid}").status_code == 404
    assert client.post(f"{_base(other)}/{sid}/approve", data={"approved_by": "x"}, headers=AJAX).status_code == 404


def test_cancel_sprint(client, project_id):
    sid = _create(client, project_id, [])
    assert client.post(f"{_base(project_id)}/{sid}/cancel", headers=AJAX).status_code == 200
    assert client.post(f"{_base(project_id)}/{sid}/cancel", headers=AJAX).status_code == 409
