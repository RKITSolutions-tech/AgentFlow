"""Task 36: repository-scoped locks, wait-instead-of-fail queue, advisory pages."""
import sys
import threading
import time

import pytest

from app.db import get_db
from app.projects import lock
from app.projects import models as project_models
from app.runs import models as run_models
from app.sprints import queue
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}
SLOW = f'{sys.executable} -c "import time; time.sleep(1.2)"'
FAST = f"{sys.executable} -c pass"


@pytest.fixture(autouse=True)
def quick_polling(monkeypatch):
    monkeypatch.setattr(lock, "POLL_SECONDS", 0.05)


@pytest.fixture
def env(app, client, tmp_path):
    project_id, repo1 = create_project_with_repo(client, app.config["allowed_root"])
    repo2_dir = tmp_path / "second"
    repo2_dir.mkdir()
    with app.app_context():
        db = get_db()
        # The allowed root is the fixture's tmp root; put the second repository under it.
        import os

        second = os.path.join(app.config["allowed_root"], "second-repo")
        os.makedirs(second, exist_ok=True)
        project_models.add_repository(db, project_id, "second", second, allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])
        repos = [r[0] for r in db.execute("SELECT id FROM repositories ORDER BY id")]
        yield type("Env", (), {"db": db, "pid": project_id, "app": app, "client": client, "repos": repos})


def _scoped(env):
    project_models.update_project(env.db, env.pid, "P", "", "repository")


def _post_run(env, command, repo, **extra):
    return env.client.post(f"/projects/{env.pid}/runs", data={"repository_id": repo, "commands": command, **extra}, headers=AJAX)


# -- scope overlap -------------------------------------------------------------------------


def test_repositories_lock_independently_but_conflict_with_the_whole_project(env):
    a, b = env.repos
    lock.acquire(env.db, env.pid, "ralph_run", 1, repository_id=a)
    lock.acquire(env.db, env.pid, "pipeline_run", 2, repository_id=b)  # different repository: fine
    assert len(lock.active_locks(env.db, env.pid)) == 2
    with pytest.raises(lock.LockConflict) as err:
        lock.acquire(env.db, env.pid, "manual_run", 3, repository_id=a)
    assert err.value.holder.owner_id == 1 and "repository" in str(err.value)
    with pytest.raises(lock.LockConflict):
        lock.acquire(env.db, env.pid, "manual_run", 4)  # whole project overlaps both
    assert lock.current(env.db, env.pid, a).owner_id == 1 and lock.current(env.db, env.pid, b).owner_id == 2
    lock.release(env.db, env.pid, "ralph_run", 1)
    lock.release(env.db, env.pid, "pipeline_run", 2)
    lock.acquire(env.db, env.pid, "manual_run", 5)  # whole project now holds everything
    for repo in (a, b):
        with pytest.raises(lock.LockConflict):
            lock.acquire(env.db, env.pid, "ralph_run", 6, repository_id=repo)


def test_stale_repository_lock_is_taken_over_without_touching_the_other(env):
    a, b = env.repos
    lock.acquire(env.db, env.pid, "ralph_run", 1, repository_id=a)
    lock.acquire(env.db, env.pid, "ralph_run", 2, repository_id=b)
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(seconds=lock.STALE_SECONDS + 10)).isoformat(timespec="milliseconds")
    env.db.execute("UPDATE project_locks SET heartbeat_at = ? WHERE repository_id = ?", (old, a))
    env.db.commit()
    taken = lock.acquire(env.db, env.pid, "manual_run", 3, repository_id=a)
    assert taken.owner_id == 3
    assert lock.current(env.db, env.pid, b).owner_id == 2 and not lock.is_stale(lock.current(env.db, env.pid, b))


def test_the_database_still_refuses_two_locks_on_one_scope(env):
    a = env.repos[0]
    lock.acquire(env.db, env.pid, "ralph_run", 1, repository_id=a)
    with pytest.raises(Exception) as err:
        env.db.execute(
            "INSERT INTO project_locks (project_id, owner_type, owner_id, repository_id, acquired_at, heartbeat_at) "
            "VALUES (?, 'manual_run', 9, ?, 'x', 'x')", (env.pid, a))
    assert "UNIQUE" in str(err.value)
    env.db.rollback()


def test_scope_for_follows_the_project_setting(env):
    a = env.repos[0]
    assert lock.scope_for(env.db, env.pid, a) is None  # default: whole project
    _scoped(env)
    assert lock.scope_for(env.db, env.pid, a) == a and lock.scope_for(env.db, env.pid, None) is None
    with pytest.raises(ValueError):
        project_models.update_project(env.db, env.pid, "P", "", "bogus")


def test_queue_busy_check_is_scoped_to_the_repository(env):
    a, b = env.repos
    _scoped(env)
    lock.acquire(env.db, env.pid, "ralph_run", 1, repository_id=a)
    assert "ralph run #1" in queue.project_busy(env.db, env.pid, a)
    assert queue.project_busy(env.db, env.pid, b) is None
    assert queue.project_busy(env.db, env.pid) is not None  # whole-project question: something holds it


# -- manual runs on different repositories --------------------------------------------------------


def test_runs_on_different_repositories_execute_together_only_when_scoped(env):
    a, b = env.repos
    manager = env.app.extensions["run_manager"]
    # Default (whole project): the second run is refused.
    first = _post_run(env, SLOW, a).get_json()["run_id"]
    assert _post_run(env, FAST, b).status_code == 409
    assert manager.join(first, 30)

    _scoped(env)
    r1 = _post_run(env, SLOW, a).get_json()["run_id"]
    r2 = _post_run(env, SLOW, b)
    assert r2.status_code == 200, r2.get_json()
    assert {l.repository_id for l in lock.active_locks(env.db, env.pid)} == {a, b}  # both hold at once
    assert _post_run(env, FAST, a).status_code == 409  # same repository still excluded
    assert manager.join(r1, 30) and manager.join(r2.get_json()["run_id"], 30)
    assert lock.active_locks(env.db, env.pid) == []


# -- waiting -----------------------------------------------------------------------------------------


def test_a_waiting_run_starts_after_the_holder_finishes(env):
    a = env.repos[0]
    manager = env.app.extensions["run_manager"]
    first = _post_run(env, SLOW, a).get_json()["run_id"]
    resp = _post_run(env, FAST, a, wait_for_project="on")
    assert resp.status_code == 200 and "Queued" in resp.get_json()["message"]
    second = run_models.list_runs(env.db, env.pid)[0]
    waiting = lock.waiting(env.db, env.pid)
    assert [w["owner"] for w in waiting] == [f"manual run #{second.id}"] and waiting[0]["position"] == 1
    page = env.client.get(f"/projects/{env.pid}/runs").get_data(as_text=True)
    assert "Queued #1" in page and f"manual run #{second.id}" in page
    assert manager.join(first, 30) and manager.join(second.id, 30)
    assert run_models.get_run(env.db, second.id, with_steps=False).status == "COMPLETED"
    assert lock.waiting(env.db, env.pid) == [] and lock.active_locks(env.db, env.pid) == []


def test_stopping_a_queued_run_cancels_it_and_leaves_the_queue(env):
    a = env.repos[0]
    manager = env.app.extensions["run_manager"]
    first = _post_run(env, SLOW, a).get_json()["run_id"]
    _post_run(env, FAST, a, wait_for_project="on")
    second = run_models.list_runs(env.db, env.pid)[0].id
    manager.stop(second)
    assert manager.join(second, 30)
    row = run_models.get_run(env.db, second, with_steps=False)
    assert row.status == "CANCELLED" and "waiting" in row.status_reason
    assert lock.waiting(env.db, env.pid) == []
    assert manager.join(first, 30)


def test_the_queue_is_first_come_first_served(env):
    a, b = env.repos
    lock.acquire(env.db, env.pid, "ralph_run", 1)  # whole project held
    t1 = lock.enqueue(env.db, env.pid, "manual_run", 10, a)
    t2 = lock.enqueue(env.db, env.pid, "manual_run", 11, b)
    t3 = lock.enqueue(env.db, env.pid, "manual_run", 12, None)
    assert lock.enqueue(env.db, env.pid, "manual_run", 10, a) == t1  # once per owner
    assert [w["position"] for w in lock.waiting(env.db, env.pid)] == [1, 2, 3]
    lock.release(env.db, env.pid, "ralph_run", 1)
    with pytest.raises(lock.LockConflict) as err:  # a newcomer may not jump the queue
        lock.acquire(env.db, env.pid, "pipeline_run", 99, repository_id=a)
    assert "queued" in str(err.value) and err.value.holder is None
    with pytest.raises(lock.LockConflict):  # nor may the last in line
        lock.acquire(env.db, env.pid, "manual_run", 12, ticket_id=t3)
    lock.acquire(env.db, env.pid, "manual_run", 10, repository_id=a, ticket_id=t1)
    lock.acquire(env.db, env.pid, "manual_run", 11, repository_id=b, ticket_id=t2)  # different repository: no need to wait
    with pytest.raises(lock.LockConflict):  # the whole-project waiter needs both to finish
        lock.acquire(env.db, env.pid, "manual_run", 12, ticket_id=t3)
    lock.release(env.db, env.pid, "manual_run", 10)
    lock.release(env.db, env.pid, "manual_run", 11)
    assert lock.acquire(env.db, env.pid, "manual_run", 12, ticket_id=t3).repository_id is None
    assert lock.waiting(env.db, env.pid) == []


def test_ticket_wait_acquires_when_free_and_times_out_otherwise(env, app):
    path = app.config["DATABASE_PATH"]
    lock.acquire(env.db, env.pid, "ralph_run", 1)
    heartbeat, ticket = lock.begin(env.db, path, env.pid, "manual_run", 2, wait=True)
    assert heartbeat is None and ticket is not None
    with pytest.raises(lock.LockConflict) as err:
        ticket.wait(timeout=0.2, poll=0.02)
    assert "Gave up waiting" in str(err.value) and lock.waiting(env.db, env.pid) == []
    assert env.db.execute("SELECT status FROM project_lock_queue").fetchone()[0] == "EXPIRED"

    heartbeat, ticket = lock.begin(env.db, path, env.pid, "manual_run", 3, wait=True)
    got = []
    worker = threading.Thread(target=lambda: got.append(ticket.wait(timeout=10, poll=0.02)))
    worker.start()
    time.sleep(0.15)
    assert not got
    lock.release(env.db, env.pid, "ralph_run", 1)
    worker.join(10)
    assert got and lock.current(env.db, env.pid).owner_id == 3
    got[0].stop()
    assert lock.current(env.db, env.pid) is None


def test_begin_without_wait_still_fails_fast(env, app):
    lock.acquire(env.db, env.pid, "ralph_run", 1)
    with pytest.raises(lock.LockConflict):
        lock.begin(env.db, app.config["DATABASE_PATH"], env.pid, "manual_run", 2)
    assert lock.waiting(env.db, env.pid) == []


def test_dead_waiters_do_not_block_the_queue(env):
    lock.enqueue(env.db, env.pid, "manual_run", 5)
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(seconds=lock.WAITER_STALE_SECONDS + 5)).isoformat(timespec="milliseconds")
    env.db.execute("UPDATE project_lock_queue SET polled_at = ?", (old,))
    env.db.commit()
    assert lock.acquire(env.db, env.pid, "ralph_run", 1).owner_id == 1  # the silent waiter expired
    assert lock.waiting(env.db, env.pid) == []

    lock.release(env.db, env.pid, "ralph_run", 1)
    lock.enqueue(env.db, env.pid, "manual_run", 6)
    lock.release_all_active(env.db)  # a restart: no thread survives to poll
    assert lock.waiting(env.db, env.pid) == []


# -- interactive pages ------------------------------------------------------------------------------------


def test_chat_and_terminal_never_take_the_lock_but_warn_while_it_is_held(env):
    a = env.repos[0]
    project = project_models.get_project(env.db, env.pid)
    terminal = f"/projects/{env.pid}/repos/{a}/terminal"
    page = env.client.get(terminal)
    assert page.status_code == 200 and "is executing here" not in page.get_data(as_text=True)
    assert lock.active_locks(env.db, env.pid) == []  # looking did not lock anything
    lock.acquire(env.db, env.pid, "ralph_run", 7, repository_id=a)
    warned = env.client.get(terminal).get_data(as_text=True)
    assert "ralph run #7" in warned and "is executing here" in warned


def test_project_edit_page_sets_the_lock_scope(env):
    page = env.client.get(f"/projects/{env.pid}/edit").get_data(as_text=True)
    assert "Per repository" in page and 'value="project" selected' in page
    assert env.client.post(f"/projects/{env.pid}/edit", data={"name": "P", "lock_scope": "repository"}).status_code == 302
    assert project_models.get_project(env.db, env.pid).lock_scope == "repository"
    assert env.client.post(f"/projects/{env.pid}/edit", data={"name": "P", "lock_scope": "nope"}).status_code == 400


def test_start_forms_offer_waiting(env):
    for path in ("runs", "pipelines", "ralph"):
        assert "wait_for_project" in env.client.get(f"/projects/{env.pid}/{path}").get_data(as_text=True)


# -- pipelines and Ralph queue behind a held lock -----------------------------------------------------------


def _verify_pipeline(env):
    from app.pipelines import persistence

    persistence.create_pipeline(
        env.db, {"name": "v", "elements": [{"name": "t", "type": "TEST", "config": {"command": FAST}}]}, env.pid)


def test_a_queued_pipeline_runs_when_the_holder_releases_and_can_be_cancelled(env):
    from app.pipelines import executions
    from app.pipelines.engine import PipelineEngine

    _verify_pipeline(env)
    manager = env.app.extensions["pipeline_manager"]
    engine = PipelineEngine(env.db, manager.provider, manager._root)
    lock.acquire(env.db, env.pid, "ralph_run", 900)
    eid = engine.create("v", env.pid, env.repos[0])
    assert manager.start(eid, wait=True) is True
    assert executions.get_execution(env.db, eid).status == "PENDING" and len(lock.waiting(env.db, env.pid)) == 1
    lock.release(env.db, env.pid, "ralph_run", 900)
    assert manager.join(eid, 30)
    assert executions.get_execution(env.db, eid).status == "COMPLETED"

    lock.acquire(env.db, env.pid, "ralph_run", 901)
    second = engine.create("v", env.pid, env.repos[0])
    assert manager.start(second, wait=True) is True
    manager.cancel(second)
    assert manager.join(second, 30)
    assert executions.get_execution(env.db, second).status == "CANCELLED"
    assert lock.waiting(env.db, env.pid) == []

    third = engine.create("v", env.pid, env.repos[0])
    with pytest.raises(lock.LockConflict):
        manager.start(third)  # without wait it still fails fast
    assert executions.get_execution(env.db, third).status == "CANCELLED"


def test_a_queued_ralph_run_is_cancelled_or_gives_up(env, monkeypatch):
    from app.ralph import models as ralph

    _verify_pipeline(env)
    manager = env.app.extensions["ralph_manager"]
    lock.acquire(env.db, env.pid, "manual_run", 902)

    run_id = ralph.create_run(env.db, env.pid, env.repos[0], "T", "task", "v")
    assert manager.start(run_id, wait=True) is True
    manager.cancel(run_id)  # flags it; the waiting worker closes it
    assert manager.join(run_id, 30)
    run = ralph.get_run(env.db, run_id)
    assert run.status == "CANCELLED" and "waiting" in run.reason and lock.waiting(env.db, env.pid) == []

    monkeypatch.setattr(lock, "WAIT_SECONDS", 0.3)
    other = ralph.create_run(env.db, env.pid, env.repos[0], "T2", "task", "v")
    assert manager.start(other, wait=True) is True
    assert manager.join(other, 30)
    run = ralph.get_run(env.db, other)
    assert run.status == "CREATED" and run.needs_attention and "Gave up waiting" in run.reason  # resumable
