"""Sprint eligibility in the UI (task 59) and the release/queue section of the
sprint page (task 60). The gating rules themselves (workflow.select_items,
queue.release_sprint) are unchanged and covered by test_queue/test_sprint_views."""
import sys

import pytest

from app.backlog import persistence as backlog
from app.db import get_db
from app.pipelines import persistence as pipeline_store
from app.sprints import persistence as sprints
from app.sprints import queue, workflow
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def env(app, client):
    project_id, _ = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        pipeline_store.create_pipeline(
            db, {"name": "verify", "elements": [{"name": "t", "type": "TEST", "config": {"command": f"{sys.executable} -c pass"}}]},
            project_id,
        )
        pipeline_store.create_pipeline(
            db, {"name": "special", "elements": [{"name": "t", "type": "TEST", "config": {"command": f"{sys.executable} -c pass"}}]},
            project_id,
        )
        sprint_id = sprints.create_sprint(db, project_id, "Sprint One", goal="g")
        a = sprints.add_work_item(db, sprint_id, "Task A", acceptance=["x"])
        b = sprints.add_work_item(db, sprint_id, "Task B", acceptance=["y"])
        sprints.set_dependencies(db, b, [a])
        db.execute("UPDATE planned_work_items SET verification_pipeline = 'special' WHERE id = ?", (b,))
        item = backlog.create_item(db, project_id, text="loose item")
        db.commit()
        yield type("Env", (), {"db": db, "pid": project_id, "sid": sprint_id, "a": a, "b": b, "item": item})


def _set_status(env, status):
    env.db.execute("UPDATE sprints SET status = ? WHERE id = ?", (status, env.sid))
    env.db.execute("UPDATE planned_work_items SET status = 'APPROVED', task_state = 'READY'")
    env.db.commit()


# -- task 59 ----------------------------------------------------------------------------


@pytest.mark.parametrize("status,editable", [
    ("DRAFT", True), ("PLANNING", True), ("REVIEW", True),
    ("READY", False), ("EXECUTING", False), ("VERIFYING", False), ("COMPLETE", False), ("CANCELLED", False),
])
def test_is_sprint_editable(env, status, editable):
    _set_status(env, status)
    sprint = sprints.get_sprint(env.db, env.sid)
    assert workflow.is_sprint_editable(sprint) is editable


def test_closed_message_names_status_and_editable_states(env):
    _set_status(env, "READY")
    message = workflow.membership_closed_message(sprints.get_sprint(env.db, env.sid))
    assert "Sprint One" in message and "Ready" in message
    assert "Draft, Planning and Review" in message


def test_ajax_add_to_closed_sprint_is_structured_error(client, env):
    _set_status(env, "READY")
    resp = client.post(f"/projects/{env.pid}/sprints/{env.sid}/items", data={"item_ids": str(env.item)}, headers=AJAX)
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["status"] == "error" and body["sprint_status"] == "READY"
    assert "Draft, Planning and Review" in body["message"] and body["error"] == body["message"]
    assert body["editable_statuses"] == ["DRAFT", "PLANNING", "REVIEW"]
    assert backlog.get_item(env.db, env.item).sprint_id is None


def test_add_to_editable_sprint_still_works(client, env):
    resp = client.post(f"/projects/{env.pid}/sprints/{env.sid}/items", data={"item_ids": str(env.item)}, headers=AJAX)
    assert resp.status_code == 200
    assert backlog.get_item(env.db, env.item).sprint_id == env.sid


def test_sprint_page_banner_and_disabled_add(client, env):
    html = client.get(f"/projects/{env.pid}/sprints/{env.sid}").get_data(as_text=True)
    assert "sprint-locked-banner" not in html and '<button type="submit">Add to sprint</button>' in html
    _set_status(env, "EXECUTING")
    html = client.get(f"/projects/{env.pid}/sprints/{env.sid}").get_data(as_text=True)
    assert "sprint-locked-banner" in html and "Executing:" in html
    assert 'disabled aria-disabled="true" aria-describedby="addItemsLocked"' in html
    assert "cannot accept new items" in html


def test_backlog_item_sprint_picker_marks_closed_sprints(client, env):
    open_id = sprints.create_sprint(env.db, env.pid, "Open sprint", goal="g")
    _set_status(env, "READY")
    html = client.get(f"/projects/{env.pid}/backlog/items/{env.item}").get_data(as_text=True)
    assert "data-sprint-picker" in html
    assert f"/sprints/{open_id}/items" in html
    closed = html[html.index(f"/sprints/{env.sid}/items"):]
    assert closed.index("disabled") < closed.index("</option>") and "not accepting items" in closed[:400]
    assert "Sprint One is past that stage" in html


def test_backlog_item_in_sprint_shows_badge(client, env):
    workflow.select_items(env.db, env.sid, [env.item])
    html = client.get(f"/projects/{env.pid}/backlog/items/{env.item}").get_data(as_text=True)
    assert f'href="/projects/{env.pid}/sprints/{env.sid}">Sprint One</a>' in html and "run-status" in html
    inbox = client.get(f"/projects/{env.pid}/backlog/inbox?status=SELECTED").get_data(as_text=True)
    assert "Sprint One</a>" in inbox


# -- task 60 ----------------------------------------------------------------------------


def test_queue_section_before_approval(client, env):
    html = client.get(f"/projects/{env.pid}/sprints/{env.sid}").get_data(as_text=True)
    assert "Release &amp; execution queue" in html and "Not released yet" in html
    assert "Release sprint</button>" not in html
    default = queue.default_pipeline(env.db, env.pid)
    assert "Task A" in html and f"{default} (project default)" in html and "special" in html


def test_release_button_and_ajax_release(client, env):
    _set_status(env, "READY")
    html = client.get(f"/projects/{env.pid}/sprints/{env.sid}").get_data(as_text=True)
    assert "Approved and ready to release: 2 task(s) waiting." in html
    assert 'data-confirm="Release this sprint and start executing tasks?"' in html
    assert f'data-ajax-reload="/projects/{env.pid}/sprints/{env.sid}/release"' in html

    resp = client.post(f"/projects/{env.pid}/sprints/{env.sid}/release", headers=AJAX)
    body = resp.get_json()
    assert resp.status_code == 200 and body["status"] == "released" and body["count"] == 2
    assert body["next_task"] == {"id": env.a, "seq": 1, "title": "Task A", "pipeline": queue.default_pipeline(env.db, env.pid)}

    html = client.get(f"/projects/{env.pid}/sprints/{env.sid}").get_data(as_text=True)
    assert "Released and executing." in html
    assert '<span class="badge-next">Next</span> 1. Task A' in html
    assert ">Released</button>" in html and "(waiting)" in html
    # Releasing again is a conflict with a readable error.
    again = client.post(f"/projects/{env.pid}/sprints/{env.sid}/release", headers=AJAX)
    assert again.status_code == 409 and "No ready tasks" in again.get_json()["error"]


def test_release_requires_ready_sprint(client, env):
    resp = client.post(f"/projects/{env.pid}/sprints/{env.sid}/release", headers=AJAX)
    assert resp.status_code == 409 and "approved" in resp.get_json()["error"]


def test_release_non_ajax_redirects_with_flash(client, env):
    _set_status(env, "READY")
    detail = f"http://localhost/projects/{env.pid}/sprints/{env.sid}"
    resp = client.post(f"/projects/{env.pid}/sprints/{env.sid}/release", headers={"Referer": detail})
    assert resp.status_code == 302 and resp.headers["Location"] == detail
