import os
import subprocess
import sys

import pytest

from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.pipelines import executions, persistence
from app.pipelines.engine import PipelineEngine
from app.ralph import models
from app.ralph.manager import RalphManager
from app.ralph.orchestrator import RalphOrchestrator
from app.pipelines.manager import PipelineManager
from tests.conftest import create_project_with_repo

PY = sys.executable


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def env(app, client, tmp_path):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    with open(os.path.join(repo, "README"), "w") as fh:
        fh.write("hi")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    with app.app_context():
        db = get_db()
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        factory = lambda conn: FakeAgentAdapter(conn)
        engine = PipelineEngine(db, provider, str(tmp_path / "art"), factory, sleep=lambda s: None)
        ralph = RalphOrchestrator(db, provider, engine, factory)
        # Verification: a test that passes only when good.txt exists.
        check = f"import os,sys; sys.exit(0 if os.path.exists('good.txt') else 1)"
        persistence.create_pipeline(
            db,
            {"name": "verify", "elements": [
                {"name": "unit", "type": "TEST", "config": {"command": f'{PY} -c "{check}"'}}]},
            project_id,
        )
        yield type("Env", (), {
            "db": db, "ralph": ralph, "project_id": project_id, "repo": repo,
            "provider": provider, "tmp": tmp_path,
        })


def _script(*writes):
    steps = []
    for path, content in writes:
        steps += [{"action": "message", "text": f"writing {path}"}, {"action": "write_file", "path": path, "content": content}, {"action": "complete"}]
    return steps


def _run(env, script, **kw):
    return models.create_run(
        env.db, env.project_id, 1, "Add good file", "Create good.txt",
        "verify", acceptance=["good.txt exists"], script=script, **kw,
    )


def _head(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


def test_passes_first_iteration_and_commits(env):
    rid = _run(env, _script(("good.txt", "ok")))
    before = _head(env.repo)
    run = env.ralph.run(rid)
    assert run.status == "COMPLETED" and run.current_iteration == 1
    assert run.commit_sha and run.commit_sha != before == _head(env.repo) or run.commit_sha == _head(env.repo)
    message = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=env.repo, capture_output=True, text=True).stdout
    assert message.startswith("[AUTO] Phase 2 pipeline:") and "(iteration 1)" in message
    it = models.list_iterations(env.db, rid)[0]
    assert it.status == "PASSED" and it.changed_files == ["good.txt"] and it.commit_sha == run.commit_sha
    assert "good.txt exists" in it.prompt and "Create good.txt" in it.prompt
    assert "writing good.txt" in it.reply


def test_failure_feeds_back_and_second_iteration_corrects(env):
    script = _script(("wrong.txt", "x"), ("good.txt", "ok"))
    rid = _run(env, script)
    run = env.ralph.run(rid)
    assert run.status == "COMPLETED" and run.current_iteration == 2
    first, second = models.list_iterations(env.db, rid)
    assert first.status == "FAILED" and first.next_action == "retry_with_feedback"
    assert "TEST step 'unit' failed" in first.analysis and first.failure_signature
    # steering feedback: iteration 2's prompt carries the failure evidence
    assert "Iteration 1 failed verification" in second.prompt and "Exit code 1" in second.prompt
    assert "wrong.txt" in second.prompt
    assert second.status == "PASSED"
    # the same agent session was resumed, not restarted
    assert env.db.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0] == 1
    # each iteration links to its verification pipeline execution
    assert executions.get_execution(env.db, first.verification_execution_id).status == "FAILED"


def test_identical_failures_block_for_no_progress(env):
    # The agent keeps producing different files but the failure never changes.
    script = _script(("a.txt", "1"), ("b.txt", "2"), ("c.txt", "3"))
    rid = _run(env, script, identical_failure_limit=2)
    run = env.ralph.run(rid)
    assert run.status == "BLOCKED" and run.needs_attention and "same failure" in run.reason
    its = models.list_iterations(env.db, rid)
    assert [i.status for i in its] == ["FAILED", "NO_PROGRESS"] and its[-1].next_action == "manual_review"


def test_no_change_detection(env):
    script = [{"action": "message", "text": "thinking"}, {"action": "complete"},
              {"action": "message", "text": "still thinking"}, {"action": "complete"},
              {"action": "message", "text": "nothing"}, {"action": "complete"}]
    rid = _run(env, script, identical_failure_limit=9, no_change_limit=2)
    run = env.ralph.run(rid)
    assert run.status == "BLOCKED" and "did not change" in run.reason
    assert run.current_iteration == 3


def test_max_iterations_fails_the_run(env):
    script = _script(("a.txt", "1"), ("b.txt", "2"))
    rid = _run(env, script, max_iterations=2, identical_failure_limit=5, no_change_limit=5)
    run = env.ralph.run(rid)
    assert run.status == "FAILED" and "2 iteration" in run.reason and run.current_iteration == 2


def test_agent_claiming_success_is_not_enough(env):
    rid = _run(env, [{"action": "message", "text": "All done, everything passes!"}, {"action": "complete"}] * 2,
               max_iterations=1)
    assert env.ralph.run(rid).status == "FAILED"


def test_steering_is_injected_once_and_persisted(env):
    script = _script(("wrong.txt", "x"), ("good.txt", "ok"))
    rid = _run(env, script)
    with pytest.raises(ValueError, match="empty"):
        env.ralph.steer(rid, " ")
    sid = env.ralph.steer(rid, "Do not replace the current uploader.")
    env.ralph.run(rid)
    first, second = models.list_iterations(env.db, rid)
    assert "Do not replace the current uploader." in first.prompt
    assert "Do not replace the current uploader." not in second.prompt
    steering = models.list_steering(env.db, rid)
    assert [(s.id, s.consumed_iteration) for s in steering] == [(sid, 1)]
    with pytest.raises(ValueError, match="finished"):
        env.ralph.steer(rid, "late")


def test_pause_resume_and_cancel(env):
    script = _script(("wrong.txt", "x"), ("good.txt", "ok"))
    rid = _run(env, script)
    env.ralph.pause(rid)
    assert models.get_run(env.db, rid).pause_requested  # a flag; run() clears it on (re)start

    rid = _run(env, script)
    orchestrator = env.ralph
    original = orchestrator._iterate

    def iterate_then_pause(run, ctx):
        outcome = original(run, ctx)
        models.update_run(env.db, run.id, pause_requested=1)
        return outcome

    orchestrator._iterate = iterate_then_pause
    run = orchestrator.run(rid)
    assert run.status == "PAUSED" and run.current_iteration == 1 and run.elapsed_seconds > 0
    orchestrator._iterate = original
    run = orchestrator.run(rid)  # resume reconstructs from the database
    assert run.status == "COMPLETED" and run.current_iteration == 2

    rid = _run(env, script)
    orchestrator._iterate = lambda r, c: (models.update_run(env.db, r.id, cancel_requested=1), ("CONTINUE", ""))[1]
    assert orchestrator.run(rid).status == "CANCELLED"
    with pytest.raises(ValueError):
        orchestrator.cancel(rid)


def test_unblock_with_steering_continues_after_no_progress(env):
    script = _script(("a.txt", "1"), ("b.txt", "2"), ("good.txt", "ok"))
    rid = _run(env, script, identical_failure_limit=2)
    assert env.ralph.run(rid).status == "BLOCKED"
    with pytest.raises(ValueError, match="unblock"):
        env.ralph.run(rid)
    env.ralph.unblock(rid, "Create good.txt, nothing else")
    run = env.ralph.run(rid)
    assert run.status == "COMPLETED" and run.current_iteration == 3
    assert "Create good.txt, nothing else" in models.list_iterations(env.db, rid)[-1].prompt


def test_runtime_limit_times_out(env):
    ticks = iter(range(0, 1000, 100))
    env.ralph._clock = lambda: next(ticks)
    rid = _run(env, _script(("a.txt", "1"), ("b.txt", "2")), max_runtime_seconds=50,
               identical_failure_limit=5, no_change_limit=5)
    assert env.ralph.run(rid).status == "TIMED_OUT"


def test_auto_commit_off_leaves_changes_uncommitted(env):
    before = _head(env.repo)
    rid = _run(env, _script(("good.txt", "ok")), auto_commit=False)
    run = env.ralph.run(rid)
    assert run.status == "COMPLETED" and _head(env.repo) == before and not run.commit_sha


def test_commit_failure_blocks_run(env):
    hook = os.path.join(env.repo, ".git", "hooks", "pre-commit")
    with open(hook, "w") as fh:
        fh.write("#!/bin/sh\necho rejected by hook\nexit 1\n")
    os.chmod(hook, 0o755)
    rid = _run(env, _script(("good.txt", "ok")))
    run = env.ralph.run(rid)
    assert run.status == "BLOCKED" and "commit failed" in run.reason


def test_secrets_in_prompts_and_replies_are_redacted(env):
    script = [{"action": "message", "text": "found API_KEY=supersecretvalue123"},
              {"action": "write_file", "path": "good.txt", "content": "x"}, {"action": "complete"}]
    rid = models.create_run(env.db, env.project_id, 1, "t", "Use API_KEY=hunter2hunter2 carefully",
                            "verify", script=script)
    env.ralph.run(rid)
    it = models.list_iterations(env.db, rid)[0]
    assert "supersecretvalue123" not in it.reply and "hunter2hunter2" not in it.prompt and it.redacted


def test_creation_validation(env):
    with pytest.raises(ValueError, match="verification"):
        models.create_run(env.db, env.project_id, 1, "t", "x", "")
    with pytest.raises(ValueError):
        models.create_run(env.db, env.project_id, 1, "t", "x", "verify", max_iterations=0)


def test_manager_background_run_and_reconcile(app, client, tmp_path):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"], "r2")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    app.config["PLANNING_AGENT"] = "fake"
    with app.app_context():
        db = get_db()
        manager = RalphManager(PipelineManager(app.config))
        persistence.create_pipeline(
            db, {"name": "ok", "elements": [{"name": "t", "type": "COMMAND", "config": {"command": f'{PY} -c "pass"'}}]},
            project_id,
        )
        rid = models.create_run(
            db, project_id, 1, "bg", "do", "ok",
            script=[{"action": "write_file", "path": "f.txt", "content": "x"}, {"action": "complete"}],
        )
        manager.start(rid)
        assert manager.join(rid, 30)
        assert models.get_run(db, rid).status == "COMPLETED"

        stuck = models.create_run(db, project_id, 1, "stuck", "do", "ok")
        models.update_run(db, stuck, status="RUNNING")
        assert manager.reconcile() == [stuck]
        assert models.get_run(db, stuck).status == "BLOCKED"
        manager.cancel(stuck)
        assert models.get_run(db, stuck).status == "CANCELLED"
