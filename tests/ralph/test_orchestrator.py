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
from app.runs import artifacts as run_artifacts
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
        # Same root the app's own routes resolve artifacts against
        # (app/runs/artifacts.artifact_root), so a research report stored here
        # is also readable through the web view (tests that POST to the
        # ralph.use_research/dismiss_research routes need this to line up).
        engine = PipelineEngine(db, provider, run_artifacts.artifact_root(app.config), factory, sleep=lambda s: None)
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


def _research_report_script():
    """A FakeAgent script that replies with a fixed, parseable research report
    (task 47's `research_on_failure` trigger), plus the report itself for
    assertions."""
    from app.agents.research_agent import scripted_report
    from app.agents.research_report import Finding, ResearchReport, Source

    report = ResearchReport(
        summary="The check script requires good.txt at the repo root.",
        findings=[Finding(text="good.txt is never written by the earlier attempts", source_ids=["S1"])],
        sources=[Source(id="S1", file_path="verify.py", excerpt="check good.txt")],
    )
    return scripted_report(report), report


def test_research_on_failure_off_never_triggers(env):
    script = _script(("a.txt", "1"), ("b.txt", "2"), ("c.txt", "3"))
    rid = _run(env, script, identical_failure_limit=5, no_change_limit=5, max_iterations=3,
               research_on_failure=False, failure_threshold=2)
    run = env.ralph.run(rid)
    assert run.status == "FAILED"
    assert env.db.execute("SELECT COUNT(*) FROM research_sessions").fetchone()[0] == 0
    assert all(i.research_session_id is None for i in models.list_iterations(env.db, rid))


def test_research_triggers_once_at_failure_threshold(env):
    """docs/RUN_AND_RALPH.md §22: fires exactly once when the trailing failure
    streak first reaches `failure_threshold`, not on every failing iteration
    after it, and stores a `research_report` Artifact tagged for Ralph."""
    from app.artifacts import collector as artifact_collector
    from app.artifacts import models as artifact_models
    from app.agents.research_report import ResearchReport

    research_script, report = _research_report_script()
    script = _script(("a.txt", "1"), ("b.txt", "2"), ("c.txt", "3"))
    rid = _run(env, script, identical_failure_limit=5, no_change_limit=5, max_iterations=3,
               research_on_failure=True, failure_threshold=2, research_script=research_script)
    run = env.ralph.run(rid)
    assert run.status == "FAILED" and run.current_iteration == 3

    its = models.list_iterations(env.db, rid)
    assert its[0].research_session_id is None and its[0].research_artifact_id is None
    assert its[1].research_session_id is not None and its[1].research_artifact_id is not None
    assert its[1].research_report_presented is False
    assert its[2].research_session_id is None  # streak passed the threshold: not re-triggered

    assert env.db.execute("SELECT COUNT(*) FROM research_sessions").fetchone()[0] == 1
    # the main iteration session (resumed) plus one standalone research session
    assert env.db.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0] == 2

    artifact = artifact_models.get_artifact(env.db, its[1].research_artifact_id)
    assert artifact.kind == "research_report"
    assert set(artifact.tags) == {"ralph_research", "iteration_2", "failure_analysis"}
    assert artifact.ralph_run_id == rid and artifact.iteration_number == 2

    with open(artifact_collector.file_path(env.ralph._engine._root, artifact), encoding="utf-8") as fh:
        stored = ResearchReport.from_json(fh.read())
    assert stored.summary == report.summary


def test_research_triggers_alongside_no_progress_block(env):
    """When `failure_threshold` and `identical_failure_limit` both trip on the
    same iteration boundary, research fires (it runs first) and the run still
    ends up BLOCKED -- research is advisory and never changes that outcome."""
    research_script, report = _research_report_script()
    script = _script(("a.txt", "1"), ("b.txt", "2"), ("good.txt", "ok"))
    rid = _run(env, script, identical_failure_limit=2, no_change_limit=5,
               research_on_failure=True, failure_threshold=2, research_script=research_script)
    run = env.ralph.run(rid)
    assert run.status == "BLOCKED" and run.needs_attention
    its = models.list_iterations(env.db, rid)
    assert its[-1].status == "NO_PROGRESS"
    assert its[1].research_session_id is not None and its[1].research_artifact_id is not None


def test_use_research_as_steering_injects_into_next_prompt(env, client):
    """"Use findings as steering" reuses the existing `ralph_steering`
    mechanism verbatim: the findings text is injected into the *next*
    iteration's prompt exactly once, the same as manual steering."""
    research_script, report = _research_report_script()
    script = _script(("a.txt", "1"), ("b.txt", "2"), ("good.txt", "ok"))
    rid = _run(env, script, identical_failure_limit=2, no_change_limit=5,
               research_on_failure=True, failure_threshold=2, research_script=research_script)
    run = env.ralph.run(rid)
    assert run.status == "BLOCKED"
    it = models.list_iterations(env.db, rid)[1]
    assert it.research_report_presented is False

    resp = client.post(
        f"/projects/{env.project_id}/ralph/{rid}/research/{it.id}/use",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200

    assert models.get_iteration(env.db, it.id).research_report_presented is True
    steering = models.list_steering(env.db, rid)
    assert len(steering) == 1 and report.summary in steering[0].message

    env.ralph.unblock(rid)
    run = env.ralph.run(rid)
    assert run.status == "COMPLETED"
    final_prompt = models.list_iterations(env.db, rid)[-1].prompt
    assert report.summary in final_prompt
    # injected exactly once: not repeated on an even-later iteration were there one
    assert steering[0].id in [s.id for s in models.list_steering(env.db, rid) if s.consumed_iteration]


def test_dismiss_research_marks_presented_without_steering(env, client):
    research_script, report = _research_report_script()
    script = _script(("a.txt", "1"), ("b.txt", "2"), ("good.txt", "ok"))
    rid = _run(env, script, identical_failure_limit=2, no_change_limit=5,
               research_on_failure=True, failure_threshold=2, research_script=research_script)
    run = env.ralph.run(rid)
    assert run.status == "BLOCKED"
    it = models.list_iterations(env.db, rid)[1]

    resp = client.post(
        f"/projects/{env.project_id}/ralph/{rid}/research/{it.id}/dismiss",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert models.get_iteration(env.db, it.id).research_report_presented is True
    assert models.list_steering(env.db, rid) == []

    env.ralph.unblock(rid)
    run = env.ralph.run(rid)
    assert run.status == "COMPLETED"
    assert report.summary not in models.list_iterations(env.db, rid)[-1].prompt


def test_research_trigger_does_not_acquire_project_lock(env):
    """Mirrors the pipeline RESEARCH-step lock test
    (tests/pipelines/test_pipeline_engine.py test_research_step_does_not_acquire_project_lock):
    a rival already holds the project's lock; a research-triggering Ralph run
    must still complete and never add a lock row of its own. `RalphOrchestrator.run()`
    itself never touches `app/projects/lock.py` (that happens once, above it,
    in `RalphManager.start()`) -- the research trigger must not change that."""
    from app.projects import lock

    lock.acquire(env.db, env.project_id, "manual_run", 999)
    research_script, _ = _research_report_script()
    script = _script(("a.txt", "1"), ("b.txt", "2"), ("c.txt", "3"))
    rid = _run(env, script, identical_failure_limit=5, no_change_limit=5, max_iterations=3,
               research_on_failure=True, failure_threshold=2, research_script=research_script)
    run = env.ralph.run(rid)
    assert run.status == "FAILED"
    held = lock.active_locks(env.db, env.project_id)
    assert [h.owner_id for h in held] == [999]


def test_active_skill_is_injected_into_the_iteration_prompt(env):
    """docs/AGENT_ADAPTER.md §23.2/23.3: Ralph iterations pull role/adapter-scoped
    skills from the prompt library the same way they already pull instruction blocks."""
    from app.prompts import models as prompt_models

    prompt_models.create_fragment(
        env.db, "write-tests", "Always add a test for new behaviour.", skill_status="active",
        skill_roles=["implementation"], skill_adapter_types=["fake"],
    )
    rid = _run(env, _script(("good.txt", "ok")))
    env.ralph.run(rid)
    it = models.list_iterations(env.db, rid)[0]
    assert "Always add a test for new behaviour." in it.prompt
    # Compliance/audit trail (task 53): the skill manifest used for this
    # iteration's prompt is persisted on its execution_prompts row.
    recorded = prompt_models.get_execution_prompt(env.db, it.execution_prompt_id)
    assert recorded.skills_included == ["write-tests"]
