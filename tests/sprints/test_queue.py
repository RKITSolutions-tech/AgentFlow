import os
import subprocess
import sys

import pytest

from app.db import get_db
from app.pipelines import persistence as pipeline_store
from app.ralph import models as ralph
from app.sprints import persistence as sprints
from app.sprints import queue
from app.sprints.models import InvalidTaskTransitionError
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def env(app, client):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        pipeline_store.create_pipeline(
            db,
            {"name": "verify", "elements": [{"name": "t", "type": "TEST", "config": {"command": f"{sys.executable} -c pass"}}]},
            project_id,
        )
        sprint_id = sprints.create_sprint(db, project_id, "S1", goal="g")
        a = sprints.add_work_item(db, sprint_id, "A", acceptance=["x"])
        b = sprints.add_work_item(db, sprint_id, "B", acceptance=["y"])
        c = sprints.add_work_item(db, sprint_id, "C", acceptance=["z"])
        sprints.set_dependencies(db, b, [a])
        db.execute("UPDATE sprints SET status = 'READY' WHERE id = ?", (sprint_id,))
        db.execute("UPDATE planned_work_items SET status = 'APPROVED', task_state = 'READY'")
        db.commit()
        yield type("Env", (), {"db": db, "project_id": project_id, "sprint_id": sprint_id, "a": a, "b": b, "c": c})


def _state(env, wid):
    return sprints.get_work_item(env.db, wid).task_state


def test_invalid_transitions_rejected(env):
    with pytest.raises(InvalidTaskTransitionError):
        queue.set_state(env.db, env.a, "IN_PROGRESS")  # READY must be released first
    with pytest.raises(InvalidTaskTransitionError):
        queue.set_state(env.db, env.a, "COMPLETE")
    queue.set_state(env.db, env.a, "RELEASED")
    queue.set_state(env.db, env.a, "IN_PROGRESS")
    queue.set_state(env.db, env.a, "COMPLETE")
    with pytest.raises(InvalidTaskTransitionError):
        queue.set_state(env.db, env.a, "RELEASED")  # COMPLETE is terminal


def test_release_sprint_releases_all_ready_and_starts_sprint(env):
    assert queue.release_sprint(env.db, env.sprint_id) == 3
    assert {_state(env, w) for w in (env.a, env.b, env.c)} == {"RELEASED"}
    assert sprints.get_sprint(env.db, env.sprint_id).status == "EXECUTING"
    with pytest.raises(queue.QueueError):  # nothing left to release
        queue.release_sprint(env.db, env.sprint_id)


def test_release_requires_approved_sprint(env):
    env.db.execute("UPDATE sprints SET status = 'REVIEW'")
    env.db.commit()
    with pytest.raises(queue.QueueError):
        queue.release_sprint(env.db, env.sprint_id)


def test_eligible_respects_dependencies_and_plan_order(env):
    assert queue.eligible_task(env.db, env.sprint_id) is None  # sprint not executing
    queue.release_sprint(env.db, env.sprint_id)
    assert queue.eligible_task(env.db, env.sprint_id).id == env.a
    queue.set_state(env.db, env.a, "IN_PROGRESS")
    # B waits on A, so independent C is next.
    assert queue.eligible_task(env.db, env.sprint_id).id == env.c
    queue.set_state(env.db, env.a, "BLOCKED")
    queue.set_state(env.db, env.c, "IN_PROGRESS")
    queue.set_state(env.db, env.c, "COMPLETE")
    assert queue.eligible_task(env.db, env.sprint_id) is None  # B held back by blocked A
    rows = {r["work"].id: r for r in queue.describe(env.db, env.sprint_id)}
    assert rows[env.b]["waiting_on"] == [env.a]


def test_unreleased_task_not_eligible(env):
    queue.release_task(env.db, env.c)
    env.db.execute("UPDATE sprints SET status = 'EXECUTING'")
    env.db.commit()
    assert queue.eligible_task(env.db, env.sprint_id).id == env.c


def test_promote_creates_run_with_task_link_and_criteria(env):
    queue.release_sprint(env.db, env.sprint_id)
    work_id, run_id = queue.promote_next(env.db, env.sprint_id, pipeline="verify")
    run = ralph.get_run(env.db, run_id)
    assert work_id == env.a and run.work_item_id == env.a and run.sprint_id == env.sprint_id
    assert run.acceptance == ["x"] and run.verification_pipeline == "verify"
    assert _state(env, env.a) == "IN_PROGRESS"


def test_promote_refused_while_project_busy(env):
    queue.release_sprint(env.db, env.sprint_id)
    queue.promote_next(env.db, env.sprint_id)
    with pytest.raises(queue.QueueError, match="busy"):
        queue.promote_next(env.db, env.sprint_id)


def test_promote_with_nothing_eligible(env):
    with pytest.raises(queue.QueueError, match="No eligible"):
        queue.promote_next(env.db, env.sprint_id)


def test_run_status_drives_task_and_sprint(env):
    queue.release_sprint(env.db, env.sprint_id)
    ids = {}
    for _ in range(3):
        work_id, run_id = queue.promote_next(env.db, env.sprint_id)
        ids[work_id] = run_id
        ralph.update_run(env.db, run_id, status="RUNNING")
        queue.sync_from_run(env.db, run_id)
        assert _state(env, work_id) == "IN_PROGRESS"
        ralph.update_run(env.db, run_id, status="COMPLETED")
        queue.sync_from_run(env.db, run_id)
        assert _state(env, work_id) == "COMPLETE"
    assert sprints.get_sprint(env.db, env.sprint_id).status == "VERIFYING"
    assert dict(queue.progress(env.db, env.sprint_id))["Complete"] == 3


def test_failed_and_cancelled_runs_map_to_states(env):
    queue.release_sprint(env.db, env.sprint_id)
    work_id, run_id = queue.promote_next(env.db, env.sprint_id)
    ralph.update_run(env.db, run_id, status="BLOCKED")
    queue.sync_from_run(env.db, run_id)
    assert _state(env, work_id) == "BLOCKED"
    ralph.update_run(env.db, run_id, status="CANCELLED")
    queue.sync_from_run(env.db, run_id)
    assert _state(env, work_id) == "RELEASED"  # BLOCKED -> RELEASED: eligible again


def test_queue_views(client, app, env):
    base = f"/projects/{env.project_id}/sprints/{env.sprint_id}"
    page = client.get(f"{base}/queue").get_data(as_text=True)
    assert "Release all ready tasks" in page and "Ready" in page
    assert client.post(f"{base}/next-task", headers=AJAX).status_code == 409  # nothing released
    resp = client.post(f"{base}/release", headers=AJAX)
    assert resp.status_code == 200 and resp.get_json()["count"] == 3
    assert client.post(f"{base}/release", headers=AJAX).status_code == 409
    assert "Run next task" in client.get(f"{base}/queue").get_data(as_text=True)
    assert client.get(f"/projects/{env.project_id + 99}/sprints/{env.sprint_id}/queue").status_code == 404
