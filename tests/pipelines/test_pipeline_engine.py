import os
import sys

import pytest

from app.agents.base import AgentAdapter, AgentSession
from app.agents.fake import FakeAgentAdapter
from app.agents.models import AgentEvent
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.pipelines import executions, persistence
from app.pipelines.engine import PipelineEngine, VariableError
from app.pipelines.manager import PipelineManager, default_agent_factory
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


def test_agent_step_injects_matching_skill(env):
    """docs/AGENT_ADAPTER.md §23.2/23.3: AGENT steps pull role/adapter-scoped
    skills from the prompt library the same way they already resolve templates."""
    from app.prompts import models as prompt_models

    prompt_models.create_fragment(
        env.db, "double-check", "Re-read the diff before finishing.", skill_status="active",
        skill_roles=["implementation"], skill_adapter_types=["fake"],
    )
    script = [{"action": "message", "text": "did it"}, {"action": "complete"}]
    eid = _pipeline(env, {"name": "impl", "type": "AGENT", "config": {"prompt": "Do the work", "script": script}})
    env.engine.run(eid)
    step = executions.list_steps(env.db, eid)[0]
    assert "Re-read the diff before finishing." in step.input_reference
    # Compliance/audit trail (task 53): the skill manifest used for this step's
    # prompt is persisted on its execution_prompts row, not just baked into the
    # prompt text.
    recorded = prompt_models.get_execution_prompt(env.db, step.execution_prompt_id)
    assert recorded.skills_included == ["double-check"]


def test_failed_agent_session_fails_step(env):
    eid = _pipeline(
        env,
        {"name": "impl", "type": "AGENT",
         "config": {"prompt": "x", "script": [{"action": "fail", "error": "boom"}]}},
    )
    ex = env.engine.run(eid)
    assert ex.status == "FAILED"


# ---------------------------------------------------------------------------
# RESEARCH steps (docs/PIPELINE_ENGINE.md §23, docs/AGENT_ADAPTER.md §23, task 43):
# a dedicated element type driving app/agents/research_agent.ResearchAgent
# (a sibling of _h_agent, not an AGENT step with config.role) -- effective
# prompt recorded the same way AGENT steps record theirs, plus a
# research_report Artifact, and no app/projects/lock.py lock acquired.
# ---------------------------------------------------------------------------


def test_research_step_completes_and_produces_artifact_and_prompt(env):
    from app.agents.research_agent import scripted_report
    from app.agents.research_report import Finding, ResearchReport, Source
    from app.artifacts import models as artifact_models
    from app.prompts import models as research_prompt_models

    report = ResearchReport(
        summary="The project does X per the README.",
        findings=[
            Finding(text="Does X", source_ids=["S1"], category="behavior"),
            Finding(text="An unverified claim", source_ids=[]),
        ],
        sources=[Source(id="S1", file_path="README.md", excerpt="does X")],
    )
    eid = _pipeline(
        env,
        {"name": "r", "type": "RESEARCH",
         "config": {"prompt": "What does ${project.path} do?", "script": scripted_report(report)}},
    )
    ex = env.engine.run(eid)
    assert ex.status == "COMPLETED"

    step = executions.list_steps(env.db, eid)[0]
    assert step.status == "PASSED" and step.session_id
    assert step.result_summary == report.summary
    assert env.repo in step.input_reference  # ${project.path} resolved into the question

    assert step.execution_prompt_id
    recorded = research_prompt_models.get_execution_prompt(env.db, step.execution_prompt_id)
    assert recorded.effective_prompt == step.input_reference
    assert recorded.source_type == "pipeline_step" and recorded.source_id == step.id

    artifacts, total = artifact_models.search(
        env.db, env.project_id, kinds=["research_report"], execution_id=eid
    )
    assert total == 1
    artifact = artifacts[0]
    assert artifact.step_name == "r" and artifact.step_execution_id == step.id
    assert artifact.metadata["finding_count"] == 2
    assert artifact.metadata["unverified_finding_count"] == 1
    assert artifact.metadata["source_count"] == 1
    assert artifact.metadata["summary_length"] == len(report.summary)
    assert artifact.metadata["cost_usd"] == 0.0
    assert artifact.metadata["duration_seconds"] is not None


def test_research_step_injects_matching_skill(env):
    from app.agents.research_agent import scripted_report
    from app.agents.research_report import ResearchReport
    from app.prompts import models as research_prompt_models

    research_prompt_models.create_fragment(
        env.db, "cite-sources", "Always cite a source for every finding.",
        skill_status="active", skill_roles=["RESEARCH"],
    )
    report = ResearchReport(summary="done", findings=[], sources=[])
    eid = _pipeline(env, {"name": "r", "type": "RESEARCH", "config": {"prompt": "Q?", "script": scripted_report(report)}})
    env.engine.run(eid)

    step = executions.list_steps(env.db, eid)[0]
    assert "Always cite a source for every finding." in step.input_reference
    recorded = research_prompt_models.get_execution_prompt(env.db, step.execution_prompt_id)
    assert recorded.skills_included == ["cite-sources"]


def test_research_step_uses_prompt_template_id(env):
    from app.agents.research_agent import scripted_report
    from app.agents.research_report import ResearchReport
    from app.prompts import models as research_prompt_models

    research_prompt_models.create_template(env.db, "research-tmpl", body="Investigate: ${vars.q}")
    report = ResearchReport(summary="done", findings=[], sources=[])
    eid = _pipeline(
        env,
        {"name": "r", "type": "RESEARCH",
         "config": {"prompt_template_id": "research-tmpl", "script": scripted_report(report)}},
    )
    executions.update_execution(env.db, eid, variables={"q": "the auth flow"})
    ex = env.engine.run(eid)
    assert ex.status == "COMPLETED"
    step = executions.list_steps(env.db, eid)[0]
    assert "Investigate: the auth flow" in step.input_reference


def test_research_step_resolves_context_dict_into_question(env):
    from app.agents.research_agent import scripted_report
    from app.agents.research_report import ResearchReport

    report = ResearchReport(summary="done", findings=[], sources=[])
    eid = _pipeline(
        env,
        {"name": "r", "type": "RESEARCH",
         "config": {"prompt": "Q?", "context": {"execution": "${execution.id}"},
                     "script": scripted_report(report)}},
    )
    env.engine.run(eid)
    step = executions.list_steps(env.db, eid)[0]
    assert f"execution: {eid}" in step.input_reference


def test_failed_research_session_fails_step(env):
    eid = _pipeline(
        env,
        {"name": "r", "type": "RESEARCH",
         "config": {"prompt": "Q?", "script": [{"action": "message", "text": "not json"}, {"action": "complete"}]}},
    )
    ex = env.engine.run(eid)
    assert ex.status == "FAILED"
    step = executions.list_steps(env.db, eid)[0]
    assert step.status == "FAILED" and step.error_summary


def test_research_step_does_not_acquire_project_lock(env):
    """docs/AGENT_ADAPTER.md §23 'Locking': research holds no exclusive
    project lock, so it can run alongside other locked work. Simulate a
    rival already holding the project's lock and confirm the RESEARCH step
    still runs to completion and never adds a lock row of its own."""
    from app.agents.research_agent import scripted_report
    from app.agents.research_report import ResearchReport
    from app.projects import lock

    lock.acquire(env.db, env.project_id, "manual_run", 999)

    report = ResearchReport(summary="ok", findings=[], sources=[])
    eid = _pipeline(env, {"name": "r", "type": "RESEARCH", "config": {"prompt": "Q?", "script": scripted_report(report)}})
    ex = env.engine.run(eid)

    assert ex.status == "COMPLETED"
    held = lock.active_locks(env.db, env.project_id)
    assert [held_lock.owner_id for held_lock in held] == [999]  # only the lock we took ourselves


class _NeverFinishesResearchAdapter(AgentAdapter):
    """Stays RUNNING forever, to exercise the TIMED_OUT -> StepResult mapping
    deterministically (every real adapter today resolves synchronously). Unlike
    tests/test_research_agent.py's `_NeverFinishesAdapter` (used with `db=None`
    there), `_h_research` always passes a real db, so `start()` here creates a
    genuine `agent_sessions` row -- `research_sessions.agent_session_id` has a
    foreign key onto it."""

    def __init__(self, db):
        self._db = db
        self.stopped = False

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "stub-1.0"

    def capabilities(self) -> frozenset[str]:
        return frozenset()

    def discover_sessions(self, project_id):
        return []

    def start(self, context, prompt, options=None):
        from app.agents import models as agent_models

        session_id = agent_models.create_agent_session(
            self._db, project_id=context.project_id, agent_type="stub",
            role=(options or {}).get("role", "RESEARCH"),
            execution_provider=context.execution_provider, execution_target=context.execution_target,
        )
        agent_models.set_session_status(self._db, session_id, "RUNNING")
        return self.status(session_id)

    def resume(self, session_id, prompt, options=None):
        raise NotImplementedError

    def send(self, session_id, content, options=None):
        raise NotImplementedError

    def stop(self, session_id) -> None:
        self.stopped = True

    def status(self, session_id) -> AgentSession:
        return AgentSession(
            id=session_id, project_id=1, agent_type="stub", role="RESEARCH",
            external_session_id=None, execution_provider="host", execution_target="",
            status="RUNNING", metadata={}, started_at="", last_activity_at="",
        )

    def stream(self, session_id, after_id=None):
        return []


def test_research_step_timeout_maps_to_timed_out_status(env):
    adapter = _NeverFinishesResearchAdapter(env.db)
    engine = PipelineEngine(env.db, env.provider, env.root, agent_factory=lambda conn: adapter)
    eid = _pipeline(env, {"name": "r", "type": "RESEARCH", "config": {"prompt": "Q?", "time_limit_seconds": 0.1}})

    ex = engine.run(eid)

    assert ex.status == "FAILED"
    step = executions.list_steps(env.db, eid)[0]
    assert step.status == "TIMED_OUT"
    assert adapter.stopped is True


class _CostReportingResearchAdapter(FakeAgentAdapter):
    """FakeAgentAdapter (so completion is synchronous and the session is a
    real, FK-valid row) but `status()` reports a fixed `cost_usd`, to
    exercise cost_limit_usd deterministically -- FakeAgentAdapter itself
    never reports usage/cost."""

    def __init__(self, db, cost_usd: float):
        super().__init__(db)
        self._cost_usd = cost_usd

    def status(self, session_id):
        session = super().status(session_id)
        session.metadata = {**(session.metadata or {}), "usage": {"cost_usd": self._cost_usd}}
        return session


def test_research_step_cost_limit_exceeded_maps_to_failed(env):
    from app.agents.research_agent import scripted_report
    from app.agents.research_report import ResearchReport

    report = ResearchReport(summary="expensive finding", findings=[], sources=[])
    adapter = _CostReportingResearchAdapter(env.db, cost_usd=1.0)
    engine = PipelineEngine(env.db, env.provider, env.root, agent_factory=lambda conn: adapter)
    eid = _pipeline(
        env,
        {"name": "r", "type": "RESEARCH",
         "config": {"prompt": "Q?", "cost_limit_usd": 0.5, "script": scripted_report(report)}},
    )

    ex = engine.run(eid)

    assert ex.status == "FAILED"
    step = executions.list_steps(env.db, eid)[0]
    # No dedicated step_executions status exists for a cost overrun (§14/§22
    # already enumerate the terminal statuses); mapped onto FAILED with the
    # limit named in the error text rather than inventing a new one.
    assert step.status == "FAILED"
    assert "cost limit" in step.error_summary.lower()


# ---------------------------------------------------------------------------
# Local model support in AGENT steps (docs/AGENT_ADAPTER.md §18, task 56):
# a step's config["model"] resolves through the same Settings `local` catalog
# lookup sprint planning uses (app/sprints/planning_agent.py, task 55), and
# is threaded onto the AgentContext the step builds.
# ---------------------------------------------------------------------------


class _ModelCapturingAdapter(AgentAdapter):
    """Records the AgentContext `_h_agent` builds, then completes
    synchronously (like every real adapter today) so the engine can finish
    the step -- this is not a CLI-driving adapter, only a probe for what
    `PipelineEngine._h_agent` passes to `adapter.start()`."""

    def __init__(self):
        self.captured_context = None

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "stub-1.0"

    def capabilities(self) -> frozenset[str]:
        return frozenset()

    def discover_sessions(self, project_id):
        return []

    def start(self, context, prompt, options=None):
        self.captured_context = context
        return self.status(1)

    def resume(self, session_id, prompt, options=None):
        raise NotImplementedError

    def send(self, session_id, content, options=None):
        raise NotImplementedError

    def stop(self, session_id) -> None:
        pass

    def status(self, session_id) -> AgentSession:
        return AgentSession(
            id=session_id, project_id=1, agent_type="stub", role="IMPLEMENTATION",
            external_session_id=None, execution_provider="host", execution_target="",
            status="COMPLETED", metadata={}, started_at="", last_activity_at="",
        )

    def stream(self, session_id, after_id=None):
        return [AgentEvent(id=1, session_id=session_id, event_type="AgentText", data="done", created_at="")]


def test_agent_step_config_model_threads_local_entry_onto_context(env):
    from app.settings import models as settings_models

    settings_models.add_model(
        env.db, "local", "self-hosted-openai", base_url="http://localhost:1234/v1", api_key="secret-key",
    )
    eid = _pipeline(
        env,
        {"name": "impl", "type": "AGENT", "config": {"prompt": "Do it", "model": "self-hosted-openai"}},
    )
    capture = _ModelCapturingAdapter()
    engine = PipelineEngine(env.db, env.provider, env.root, agent_factory=lambda conn: capture)

    ex = engine.run(eid)

    assert ex.status == "COMPLETED"
    assert capture.captured_context.model == "self-hosted-openai"


def test_agent_step_without_model_config_leaves_context_model_none(env):
    eid = _pipeline(env, {"name": "impl", "type": "AGENT", "config": {"prompt": "Do it"}})
    capture = _ModelCapturingAdapter()
    engine = PipelineEngine(env.db, env.provider, env.root, agent_factory=lambda conn: capture)

    ex = engine.run(eid)

    assert ex.status == "COMPLETED"
    assert capture.captured_context.model is None


def test_agent_step_unknown_model_fails_step(env):
    eid = _pipeline(
        env,
        {"name": "impl", "type": "AGENT", "config": {"prompt": "Do it", "model": "does-not-exist"}},
    )
    ex = env.engine.run(eid)
    assert ex.status == "FAILED"
    assert "does-not-exist" in executions.list_steps(env.db, eid)[0].error_summary


def test_default_agent_factory_local_resolves_configured_adapter(app):
    """Mirrors tests/sprints/test_planning_agent_build.py's coverage of
    PLANNING_AGENT=local, but for the pipeline engine's own factory
    (app/pipelines/manager.py) -- both share the same config knobs."""
    from app.agents.claude import ClaudeAdapter
    from app.agents.codex import CodexAdapter
    from app.settings import models as settings_models

    with app.app_context():
        db = get_db()
        settings_models.add_model(db, "local", "self-hosted-openai", base_url="http://localhost:1234/v1")

        config = {"PLANNING_AGENT": "local", "PLANNING_MODEL": "self-hosted-openai"}
        factory = default_agent_factory(config, provider=object())
        assert isinstance(factory(db), CodexAdapter)

        config["PLANNING_LOCAL_ADAPTER"] = "claude"
        factory = default_agent_factory(config, provider=object())
        assert isinstance(factory(db), ClaudeAdapter)


def test_default_agent_factory_local_without_model_raises_config_error(app):
    from app.settings.models import ModelCatalogConfigError

    with app.app_context():
        db = get_db()
        factory = default_agent_factory({"PLANNING_AGENT": "local"}, provider=object())
        with pytest.raises(ModelCatalogConfigError, match="AGENTFLOW_PLANNING_MODEL"):
            factory(db)


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
