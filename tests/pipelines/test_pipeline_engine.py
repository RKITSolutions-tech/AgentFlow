import os
import sys

import pytest

from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.pipelines import executions, persistence
from app.pipelines.engine import PipelineEngine, VariableError
from app.pipelines.manager import PipelineManager
from tests.conftest import create_project_with_repo

PY = sys.executable


def _py(code):
    return f'{PY} -c "{code}"'


@pytest.fixture
def env(app, client, tmp_path):
    project_id, repo_path = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        sleeps = []
        engine = PipelineEngine(
            db, provider, str(tmp_path / "artifacts"),
            agent_factory=lambda conn: FakeAgentAdapter(conn), sleep=sleeps.append,
        )
        yield type("Env", (), {
            "db": db, "engine": engine, "project_id": project_id, "repo": repo_path,
            "sleeps": sleeps, "provider": provider, "root": str(tmp_path / "artifacts"),
        })


def _pipeline(env, *elements, name="t"):
    definition = {"name": name, "type": "CUSTOM", "elements": list(elements)}
    persistence.create_pipeline(env.db, definition, env.project_id)
    return env.engine.create(name, env.project_id, repository_id=1)


def _cmd(name, code, **extra):
    return {"name": name, "type": "COMMAND", "config": {"command": _py(code)}, **extra}


def _statuses(env, eid):
    return [(s.element_name, s.status) for s in executions.list_steps(env.db, eid)]


def test_sequential_steps_persist_output(env):
    eid = _pipeline(env, _cmd("a", "print('hello')"), _cmd("b", "print('world')"))
    ex = env.engine.run(eid)
    assert ex.status == "COMPLETED" and ex.started_at and ex.completed_at
    steps = executions.list_steps(env.db, eid)
    assert [(s.element_name, s.status, s.exit_code) for s in steps] == [("a", "PASSED", 0), ("b", "PASSED", 0)]
    assert "hello" in steps[0].result_summary
    with open(os.path.join(env.root, steps[0].raw_data_reference)) as fh:
        assert "hello" in fh.read()
    types = [e.event_type for e in executions.list_events(env.db, eid)]
    assert types[0] == "PipelineStarted" and types[-1] == "PipelineCompleted"


def test_failure_stops_and_skips_dependants(env):
    eid = _pipeline(env, _cmd("a", "import sys; sys.exit(3)"), _cmd("b", "print(1)"))
    ex = env.engine.run(eid)
    assert ex.status == "FAILED" and "'a' failed" in ex.reason
    assert _statuses(env, eid) == [("a", "FAILED")]  # b never started


def test_continue_records_warning_and_skips_dependant(env):
    eid = _pipeline(
        env,
        _cmd("a", "import sys; sys.exit(1)", compensation={"action": "CONTINUE"}),
        _cmd("b", "print(1)"),
        _cmd("c", "print(2)", depends_on=[]),
    )
    ex = env.engine.run(eid)
    assert ex.status == "COMPLETED" and ex.warnings == 1 and "warning" in ex.reason
    assert _statuses(env, eid) == [("a", "FAILED"), ("b", "SKIPPED"), ("c", "PASSED")]


def test_retry_with_exponential_backoff(env):
    marker = os.path.join(env.repo, "tries")
    code = (
        f"import os,sys; p={marker!r}; n=int(open(p).read()) if os.path.exists(p) else 0; "
        "open(p,'w').write(str(n+1)); sys.exit(0 if n>=2 else 1)"
    )
    eid = _pipeline(
        env, _cmd("flaky", code, compensation={"attempts": 4, "delay_seconds": 1, "backoff": "exponential"})
    )
    assert env.engine.run(eid).status == "COMPLETED"
    assert [s.attempt for s in executions.list_steps(env.db, eid)] == [1, 2, 3]
    assert env.sleeps == [1, 2]


def test_loop_returns_to_step_and_respects_limit(env):
    counter = os.path.join(env.repo, "n")
    fix = f"open({counter!r},'a').write('x')"
    check = (
        f"import sys; sys.exit(0 if len(open({counter!r}).read())>=2 else 1) "
    )
    eid = _pipeline(
        env, _cmd("fix", fix), _cmd("check", check + "# a", compensation={"action": "LOOP", "step": "fix", "max_loops": 3})
    )
    assert env.engine.run(eid).status == "COMPLETED"
    names = [s.element_name for s in executions.list_steps(env.db, eid)]
    assert names == ["fix", "check", "fix", "check"]
    assert any(e.event_type == "LoopBack" for e in executions.list_events(env.db, eid))

    varying = f"import sys,os; open({counter!r}+'2','a').write('y'); print(os.path.getsize({counter!r}+'2')); sys.exit(1)"
    eid = _pipeline(
        env, _cmd("fix", "pass"), _cmd("check", varying, compensation={"action": "LOOP", "step": "fix", "max_loops": 2}),
        name="limited",
    )
    ex = env.engine.run(eid)
    assert ex.status == "FAILED" and "gave up after 2 loops" in ex.reason


def test_loop_detects_no_progress(env):
    eid = _pipeline(
        env, _cmd("fix", "pass"),
        _cmd("check", "import sys; print('same'); sys.exit(1)",
             compensation={"action": "LOOP", "step": "fix", "max_loops": 10}),
    )
    ex = env.engine.run(eid)
    assert ex.status == "FAILED" and "no progress" in ex.reason


def test_setup_and_teardown_run_around_failures(env):
    order = os.path.join(env.repo, "order")
    mark = lambda tag: f"open({order!r},'a').write({tag!r})"
    eid = _pipeline(
        env,
        _cmd("clean", mark("T"), phase="TEARDOWN"),
        _cmd("main", "import sys; sys.exit(1)"),
        _cmd("prep", mark("S"), phase="SETUP"),
    )
    ex = env.engine.run(eid)
    assert ex.status == "FAILED"
    assert open(order).read() == "ST"  # setup first, teardown despite the failure
    assert [n for n, _ in _statuses(env, eid)] == ["prep", "main", "clean"]


def test_registered_process_is_cleaned_up(env):
    eid = _pipeline(
        env,
        {"name": "srv", "type": "PROCESS_START", "phase": "SETUP",
         "config": {"command": _py("import time; time.sleep(30)"), "process": "app"}},
        _cmd("work", "print(1)"),
    )
    ex = env.engine.run(eid)
    assert ex.status == "COMPLETED" and ex.resources == []
    pid = executions.list_steps(env.db, eid)[0].process_id
    assert env.provider.wait(pid).status in ("STOPPED", "COMPLETED", "FAILED")
    assert any(e.event_type == "ResourceCleanup" for e in executions.list_events(env.db, eid))


def test_manual_approval_pauses_and_resumes(env):
    eid = _pipeline(
        env,
        _cmd("build", "print('built')"),
        {"name": "gate", "type": "MANUAL_APPROVAL", "config": {"prompt": "Ship ${execution.id}?"}},
        _cmd("deploy", "print('deployed')"),
    )
    ex = env.engine.run(eid)
    assert ex.status == "PAUSED" and ex.waiting_step_id
    assert _statuses(env, eid) == [("build", "PASSED"), ("gate", "WAITING")]
    assert env.engine.run(eid).status == "PAUSED"  # still waiting: idempotent
    with pytest.raises(ValueError, match="name"):
        env.engine.resolve_manual(ex.waiting_step_id, "APPROVED", " ")
    env.engine.resolve_manual(ex.waiting_step_id, "APPROVED", "Sam", "ok")
    ex = env.engine.run(eid)
    assert ex.status == "COMPLETED"
    assert _statuses(env, eid)[-1] == ("deploy", "PASSED")
    assert "Ship" in executions.list_steps(env.db, eid)[1].input_reference
    with pytest.raises(ValueError):
        env.engine.resolve_manual(ex.id, "APPROVED", "Sam")


def test_rejected_review_loops_back(env):
    eid = _pipeline(
        env,
        _cmd("draft", "print('v')"),
        {"name": "review", "type": "MANUAL_REVIEW", "config": {"prompt": "ok?"},
         "compensation": {"action": "LOOP", "step": "draft", "max_loops": 2}},
    )
    ex = env.engine.run(eid)
    env.engine.resolve_manual(ex.waiting_step_id, "REJECTED", "Sam", "change it")
    ex = env.engine.run(eid)
    assert ex.status == "PAUSED"  # went back to draft, then waits at review again
    assert [n for n, _ in _statuses(env, eid)] == ["draft", "review", "draft", "review"]
    env.engine.resolve_manual(ex.waiting_step_id, "APPROVED", "Sam")
    assert env.engine.run(eid).status == "COMPLETED"


def test_manual_input_becomes_variable(env):
    eid = _pipeline(
        env,
        {"name": "ask", "type": "MANUAL_INPUT", "config": {"prompt": "name?"}},
        _cmd("use", "print('${steps.ask.output}')"),
    )
    ex = env.engine.run(eid)
    with pytest.raises(ValueError, match="input"):
        env.engine.resolve_manual(ex.waiting_step_id, "APPROVED", "Sam")
    env.engine.resolve_manual(ex.waiting_step_id, "APPROVED", "Sam", value="widgets")
    assert env.engine.run(eid).status == "COMPLETED"
    steps = executions.list_steps(env.db, eid)
    assert steps[0].result_summary == "widgets" and "widgets" in steps[1].result_summary


def test_cancel_paused_execution_runs_teardown(env):
    marker = os.path.join(env.repo, "torn")
    eid = _pipeline(
        env,
        {"name": "gate", "type": "MANUAL_APPROVAL", "config": {"prompt": "?"}},
        _cmd("clean", f"open({marker!r},'w')", phase="TEARDOWN"),
    )
    env.engine.run(eid)
    env.engine.cancel(eid)
    ex = env.engine.run(eid)
    assert ex.status == "CANCELLED" and os.path.exists(marker)
    assert executions.list_steps(env.db, eid)[0].status == "CANCELLED"
    with pytest.raises(ValueError):
        env.engine.cancel(eid)


def test_agent_step_with_fake_adapter_and_variables(env):
    script = [{"action": "message", "text": "did it"}, {"action": "complete"}]
    eid = _pipeline(
        env,
        {"name": "impl", "type": "AGENT",
         "config": {"prompt": "Work in ${project.path} for ${vars.task}", "script": script}},
    )
    executions.update_execution(env.db, eid, variables={"task": "T-1"})
    ex = env.engine.run(eid)
    step = executions.list_steps(env.db, eid)[0]
    assert ex.status == "COMPLETED" and step.session_id and "did it" in step.result_summary
    assert env.repo in step.input_reference and "T-1" in step.input_reference  # effective prompt stored


def test_failed_agent_session_fails_step(env):
    eid = _pipeline(
        env,
        {"name": "impl", "type": "AGENT",
         "config": {"prompt": "x", "script": [{"action": "fail", "error": "boom"}]}},
    )
    ex = env.engine.run(eid)
    assert ex.status == "FAILED"


def test_secrets_are_redacted_before_storage(env):
    eid = _pipeline(env, _cmd("leak", "print('API_KEY=supersecretvalue123')"))
    env.engine.run(eid)
    step = executions.list_steps(env.db, eid)[0]
    assert "supersecretvalue123" not in step.result_summary and step.redacted
    with open(os.path.join(env.root, step.raw_data_reference)) as fh:
        assert "supersecretvalue123" not in fh.read()


def test_unknown_variable_fails_step(env):
    eid = _pipeline(env, _cmd("bad", "print('${nope.x}')"))
    ex = env.engine.run(eid)
    assert ex.status == "FAILED"
    assert "Unknown variable" in executions.list_steps(env.db, eid)[0].error_summary


def test_timeout_fails_step(env):
    eid = _pipeline(
        env,
        {"name": "slow", "type": "COMMAND", "config": {"command": _py("import time; time.sleep(5)"), "timeout": 0.3}},
    )
    assert env.engine.run(eid).status == "FAILED"
    assert executions.list_steps(env.db, eid)[0].status == "TIMED_OUT"


def test_disabled_step_is_skipped_but_usable_as_compensation(env):
    marker = os.path.join(env.repo, "evidence")
    eid = _pipeline(
        env,
        _cmd("test", "import sys; sys.exit(1)", compensation={"action": "RUN_STEP", "step": "capture"}),
        _cmd("capture", f"open({marker!r},'w')", enabled="DISABLED", depends_on=["test"]),
    )
    ex = env.engine.run(eid)
    assert ex.status == "FAILED" and os.path.exists(marker)
    types = [e.event_type for e in executions.list_events(env.db, eid)]
    assert "CompensationStarted" in types and "CompensationCompleted" in types


def test_start_pipeline_compensation_records_parent_link(env):
    persistence.create_pipeline(
        env.db, {"name": "rescue", "elements": [_cmd("fix", "print('rescued')")]}, env.project_id
    )
    eid = _pipeline(
        env, _cmd("main", "import sys; sys.exit(1)", compensation={"action": "START_PIPELINE", "pipeline": "rescue"})
    )
    ex = env.engine.run(eid)
    assert ex.status == "FAILED"
    children = [e for e in executions.list_executions(env.db, env.project_id) if e.parent_execution_id == eid]
    assert len(children) == 1 and children[0].status == "COMPLETED"


def test_stop_and_message_flags_attention(env):
    eid = _pipeline(env, _cmd("main", "import sys; sys.exit(1)", compensation={"action": "STOP_AND_MESSAGE"}))
    ex = env.engine.run(eid)
    assert ex.needs_attention
    assert any(e.event_type == "InterventionRequested" for e in executions.list_events(env.db, eid))


def test_file_check_and_wait_and_http_health(env):
    open(os.path.join(env.repo, "present.txt"), "w").close()
    eid = _pipeline(
        env,
        {"name": "wait", "type": "WAIT", "config": {"seconds": 5}},
        {"name": "there", "type": "FILE_CHECK", "config": {"path": "present.txt"}},
        {"name": "escape", "type": "FILE_CHECK", "config": {"path": "../../etc/passwd"},
         "compensation": {"action": "CONTINUE"}},
        {"name": "down", "type": "HEALTHCHECK", "config": {"url": "http://127.0.0.1:9/health", "timeout": 1},
         "depends_on": [], "compensation": {"action": "CONTINUE"}},
    )
    ex = env.engine.run(eid)
    assert env.sleeps == [5.0] and ex.warnings == 2
    assert dict(_statuses(env, eid)) == {"wait": "PASSED", "there": "PASSED", "escape": "FAILED", "down": "FAILED"}


def test_execution_is_frozen_against_later_edits(env):
    eid = _pipeline(env, _cmd("a", "print(1)"), name="frozen")
    pid = persistence.find_pipeline(env.db, "frozen", env.project_id).id
    persistence.new_version(env.db, pid, {"name": "frozen", "elements": [_cmd("z", "print(2)")]})
    env.engine.run(eid)
    assert [n for n, _ in _statuses(env, eid)] == ["a"]
    assert executions.get_execution(env.db, eid).pipeline_version == 1


def test_no_repository_fails_cleanly(env):
    persistence.create_pipeline(env.db, {"name": "norepo", "elements": [_cmd("a", "print(1)")]}, env.project_id)
    eid = env.engine.create("norepo", env.project_id)
    ex = env.engine.run(eid)
    assert ex.status == "FAILED" and "repository" in ex.reason


def test_disabled_pipeline_cannot_start(env):
    _pipeline(env, _cmd("a", "print(1)"), name="off")
    persistence.set_enabled(env.db, persistence.find_pipeline(env.db, "off", env.project_id).id, False)
    with pytest.raises(ValueError, match="disabled"):
        env.engine.create("off", env.project_id, 1)


def test_manager_runs_in_background_and_cancels(app, client, tmp_path):
    project_id, _ = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        manager = PipelineManager(app.config)
        engine = PipelineEngine(db, manager.provider, str(tmp_path))
        persistence.create_pipeline(
            db, {"name": "bg", "elements": [_cmd("slow", "import time; time.sleep(30)")]}, project_id
        )
        eid = engine.create("bg", project_id, 1)
        manager.start(eid)
        import time as _t

        deadline = _t.time() + 10
        while _t.time() < deadline and executions.get_execution(db, eid).status != "RUNNING":
            _t.sleep(0.05)
        _t.sleep(0.5)
        manager.cancel(eid)
        assert manager.join(eid, 15)
        assert executions.get_execution(db, eid).status == "CANCELLED"
