import sqlite3
import sys
import time

import pytest

from app.db import get_db
from app.pipelines import persistence as pipeline_store
from app.projects import lock
from app.ralph import models as ralph
from app.runs import models as run_models
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def env(app, client):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        yield type("Env", (), {"db": get_db(), "project_id": project_id, "app": app})


def _age(env, seconds):
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="milliseconds")
    env.db.execute("UPDATE project_locks SET heartbeat_at = ?", (old,))
    env.db.commit()


def test_acquire_and_conflict(env):
    held = lock.acquire(env.db, env.project_id, "ralph_run", 1)
    assert held.status == "ACTIVE" and held.owner == "ralph run #1"
    with pytest.raises(lock.LockConflict) as err:
        lock.acquire(env.db, env.project_id, "manual_run", 2)
    assert err.value.holder.owner_id == 1 and "ralph run #1" in str(err.value)


def test_same_owner_refreshes_instead_of_reacquiring(env):
    first = lock.acquire(env.db, env.project_id, "ralph_run", 1)
    time.sleep(0.01)
    again = lock.acquire(env.db, env.project_id, "ralph_run", 1)
    assert again.id == first.id and again.acquired_at == first.acquired_at
    assert again.heartbeat_at > first.heartbeat_at


def test_release_sets_released_at_and_frees_project(env):
    lock.acquire(env.db, env.project_id, "pipeline_run", 5)
    assert not lock.release(env.db, env.project_id, "pipeline_run", 6)  # not the owner
    assert lock.current(env.db, env.project_id) is not None
    assert lock.release(env.db, env.project_id, "pipeline_run", 5)
    assert lock.current(env.db, env.project_id) is None
    row = lock.history(env.db, env.project_id)[0]
    assert row.status == "RELEASED" and row.released_at
    lock.acquire(env.db, env.project_id, "manual_run", 7)  # free again


def test_heartbeat_updates_only_heartbeat(env):
    first = lock.acquire(env.db, env.project_id, "ralph_run", 1)
    time.sleep(0.01)
    assert lock.refresh_heartbeat(env.db, env.project_id, "ralph_run", 1)
    after = lock.current(env.db, env.project_id)
    assert after.acquired_at == first.acquired_at and after.heartbeat_at > first.heartbeat_at
    assert not lock.refresh_heartbeat(env.db, env.project_id, "ralph_run", 2)


def test_stale_detection_and_takeover(env):
    lock.acquire(env.db, env.project_id, "ralph_run", 1)
    assert not lock.is_lock_stale(env.db, env.project_id)
    _age(env, lock.STALE_SECONDS + 5)
    assert lock.is_lock_stale(env.db, env.project_id)
    taken = lock.acquire(env.db, env.project_id, "manual_run", 2)  # dead owner is displaced
    assert taken.owner_id == 2
    assert [l.status for l in lock.history(env.db, env.project_id)] == ["ACTIVE", "STALE"]
    # The displaced owner's late heartbeat must not revive anything.
    assert not lock.refresh_heartbeat(env.db, env.project_id, "ralph_run", 1)


def test_sweep_releases_only_stale_locks(env):
    lock.acquire(env.db, env.project_id, "ralph_run", 1)
    assert lock.detect_and_release_stale_locks(env.db) == []
    _age(env, 1000)
    swept = lock.detect_and_release_stale_locks(env.db)
    assert [l.owner_id for l in swept] == [1]
    assert lock.current(env.db, env.project_id) is None


def test_startup_releases_orphans(env):
    lock.acquire(env.db, env.project_id, "ralph_run", 1)
    assert lock.release_all_active(env.db) == 1
    assert lock.current(env.db, env.project_id) is None


def test_unknown_owner_type_rejected(env):
    with pytest.raises(ValueError):
        lock.acquire(env.db, env.project_id, "cron", 1)


def test_heartbeat_thread_keeps_lock_alive_and_releases(env, app):
    path = app.config["DATABASE_PATH"]
    beat = lock.acquire_for_worker(env.db, path, env.project_id, "ralph_run", 1, interval=0.05)
    first = lock.current(env.db, env.project_id).heartbeat_at
    time.sleep(0.3)
    assert lock.current(env.db, env.project_id).heartbeat_at > first
    beat.stop()
    assert lock.current(env.db, env.project_id) is None


def test_two_connections_cannot_both_hold(env, app):
    other = sqlite3.connect(app.config["DATABASE_PATH"])
    other.row_factory = sqlite3.Row
    try:
        lock.acquire(env.db, env.project_id, "ralph_run", 1)
        with pytest.raises(lock.LockConflict):
            lock.acquire(other, env.project_id, "pipeline_run", 2)
    finally:
        other.close()


def test_manual_run_fails_while_project_locked(client, env):
    lock.acquire(env.db, env.project_id, "ralph_run", 9)
    repo_id = env.db.execute("SELECT id FROM repositories").fetchone()[0]
    resp = client.post(
        f"/projects/{env.project_id}/runs",
        data={"repository_id": repo_id, "commands": f"{sys.executable} -c pass"}, headers=AJAX,
    )
    assert resp.status_code == 409 and "ralph run #9" in resp.get_json()["error"]
    run = run_models.list_runs(env.db, env.project_id)[0]
    assert run.status == "FAILED"


def test_manual_run_holds_lock_then_releases(client, app, env):
    repo_id = env.db.execute("SELECT id FROM repositories").fetchone()[0]
    resp = client.post(
        f"/projects/{env.project_id}/runs",
        data={"repository_id": repo_id, "commands": f"{sys.executable} -c pass"}, headers=AJAX,
    )
    assert resp.status_code == 200
    run_id = resp.get_json()["run_id"]
    assert app.extensions["run_manager"].join(run_id, 30)
    assert lock.current(env.db, env.project_id) is None
    assert lock.history(env.db, env.project_id)[0].owner_type == "manual_run"


def test_ralph_and_pipeline_start_refused_while_locked(client, env, app):
    lock.acquire(env.db, env.project_id, "manual_run", 3)
    repo_id = env.db.execute("SELECT id FROM repositories").fetchone()[0]
    resp = client.post(
        f"/projects/{env.project_id}/pipelines/executions",
        data={"pipeline": "backend-only", "repository_id": repo_id}, headers=AJAX,
    )
    assert resp.status_code == 409 and "manual run #3" in resp.get_json()["error"]
    pipeline_store.create_pipeline(
        env.db, {"name": "v", "elements": [{"name": "t", "type": "TEST", "config": {"command": f"{sys.executable} -c pass"}}]},
        env.project_id,
    )
    resp = client.post(
        f"/projects/{env.project_id}/ralph",
        data={"repository_id": repo_id, "task": "do it", "verification_pipeline": "v"}, headers=AJAX,
    )
    assert resp.status_code == 409
    run = ralph.list_runs(env.db, env.project_id)[0]
    assert run.status == "CREATED"  # never started; can be resumed once the lock frees


def test_lock_holder_shown_in_ui(client, env):
    page = client.get(f"/projects/{env.project_id}").get_data(as_text=True)
    assert "Not locked" in page
    lock.acquire(env.db, env.project_id, "ralph_run", 4)
    page = client.get(f"/projects/{env.project_id}").get_data(as_text=True)
    assert "ralph run #4" in page and "Locked" in page
    assert "ralph run #4" in client.get(f"/projects/{env.project_id}/ralph").get_data(as_text=True)
    _age(env, 1000)
    assert "Lock stale" in client.get(f"/projects/{env.project_id}").get_data(as_text=True)


def test_queue_treats_lock_as_busy(env):
    from app.sprints import queue

    assert queue.project_busy(env.db, env.project_id) is None
    lock.acquire(env.db, env.project_id, "pipeline_run", 2)
    assert "pipeline run #2" in queue.project_busy(env.db, env.project_id)


def test_lock_of_a_finished_owner_is_replaced_without_waiting_for_stale(env):
    """A worker writes its final status just before releasing; a request landing
    in that gap must not see a conflict (restart right after stop)."""
    repo_id = env.db.execute("SELECT id FROM repositories").fetchone()[0]
    run_id = ralph.create_run(env.db, env.project_id, repo_id, "t", "task", "verify")
    lock.acquire(env.db, env.project_id, "ralph_run", run_id)
    ralph.update_run(env.db, run_id, status="RUNNING")
    with pytest.raises(lock.LockConflict):
        lock.acquire(env.db, env.project_id, "manual_run", 1)
    ralph.update_run(env.db, run_id, status="COMPLETED")
    taken = lock.acquire(env.db, env.project_id, "manual_run", 1)
    assert taken.owner_type == "manual_run"
    assert lock.history(env.db, env.project_id)[1].status == "RELEASED"
