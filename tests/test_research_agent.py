"""Coverage for the RESEARCH role (docs/AGENT_ADAPTER.md §23, Phase A: task 45):
`RepositoryContextLoader` path safety, `ResearchAgent` end-to-end through
`FakeAgentAdapter`, unverified-finding flagging, time-limit enforcement, and
that no `app/projects/lock.py` lock is ever taken for a research session.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents import models as agent_models
from app.agents.base import AgentAdapter, AgentContext, AgentSession
from app.agents.fake import FakeAgentAdapter
from app.agents.research_agent import (
    ReportParseError,
    ResearchAgent,
    parse_report,
    scripted_report,
)
from app.agents.research_context import RepositoryContextLoader
from app.agents.research_report import Finding, ResearchReport, Source
from app.db import get_db
from app.knowledge import models as knowledge_models
from app.projects import models as project_models
from app.prompts import models as prompt_models
from app.security import PathNotAllowedError


def _make_project(db, app, name="Research Project"):
    working_directory = str(Path(app.config["allowed_root"]) / "repo")
    Path(working_directory).mkdir(parents=True, exist_ok=True)
    project_id = project_models.create_project(db, name)
    project_models.add_repository(
        db, project_id, "repo", working_directory, app.config["ALLOWED_PROJECT_ROOTS"], is_primary=True,
    )
    return project_id, working_directory


# ---------------------------------------------------------------------------
# RepositoryContextLoader path safety
# ---------------------------------------------------------------------------

def test_loader_requires_allowed_roots():
    with pytest.raises(ValueError):
        RepositoryContextLoader(())


def test_discover_rejects_root_outside_allowed_roots(tmp_path):
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    loader = RepositoryContextLoader((str(allowed_root),))
    with pytest.raises(PathNotAllowedError):
        loader.discover(str(other))


def test_discover_rejects_symlink_escape(tmp_path):
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    repo = allowed_root / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "README.md").write_text("hello\n")
    outside = tmp_path / "secret.md"
    outside.write_text("outside content\n")
    (repo / "docs" / "escape.md").symlink_to(outside)

    loader = RepositoryContextLoader((str(allowed_root),))
    discovered = loader.discover(str(repo))

    assert "docs/escape.md" not in [f.path for f in discovered.files]
    assert any("escape.md" in s and "outside the repository" in s for s in discovered.skipped)
    # The legitimate file is still found.
    assert "README.md" in [f.path for f in discovered.files]


def test_discover_rejects_absolute_pattern(tmp_path):
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    repo = allowed_root / "repo"
    repo.mkdir()

    loader = RepositoryContextLoader((str(allowed_root),))
    discovered = loader.discover(str(repo), extra_patterns=["/etc/passwd"])

    assert discovered.files == []
    assert any("must be relative" in s for s in discovered.skipped)


def test_discover_caps_file_count(tmp_path):
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    repo = allowed_root / "repo"
    (repo / "src").mkdir(parents=True)
    for n in range(10):
        (repo / "src" / f"m{n}.py").write_text("pass\n")

    loader = RepositoryContextLoader((str(allowed_root),))
    discovered = loader.discover(str(repo), max_files=3)

    assert len(discovered.files) <= 3
    assert any("file limit" in s for s in discovered.skipped)


def test_discover_caps_total_size(tmp_path):
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    repo = allowed_root / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("x" * 800)
    (repo / "CHANGELOG.md").write_text("y" * 800)

    loader = RepositoryContextLoader((str(allowed_root),))
    discovered = loader.discover(str(repo), max_total_bytes=1000)

    assert discovered.total_bytes <= 1000
    assert any("size limit" in s for s in discovered.skipped)


def test_render_inlines_docs_and_lists_source_only(tmp_path):
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    repo = allowed_root / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "README.md").write_text("# Demo project\n")
    (repo / "src" / "main.py").write_text("print('hi')\n")

    loader = RepositoryContextLoader((str(allowed_root),))
    discovered = loader.discover(str(repo))
    text = loader.render(discovered)

    assert "README.md" in text
    assert "# Demo project" in text  # inlined content
    assert "src/main.py" in text  # listed, not inlined
    assert "print('hi')" not in text


# ---------------------------------------------------------------------------
# Report parsing / unverified flagging
# ---------------------------------------------------------------------------

def test_parse_report_flags_finding_without_source_as_unverified():
    text = (
        '```json\n{"summary": "s", "findings": [{"text": "a", "source_ids": []}], '
        '"sources": []}\n```'
    )
    report = parse_report(text)
    assert report.findings[0].confidence == "unverified"
    assert report.unverified == report.findings


def test_parse_report_rejects_invented_source_id():
    text = json.dumps({"summary": "s", "findings": [{"text": "a", "source_ids": ["S9"]}], "sources": []})
    report = parse_report(text)
    assert report.findings[0].source_ids == []
    assert report.findings[0].confidence == "unverified"


def test_parse_report_with_valid_source_is_verified():
    text = json.dumps(
        {
            "summary": "s",
            "findings": [{"text": "a", "source_ids": ["S1"]}],
            "sources": [{"id": "S1", "file_path": "README.md", "excerpt": "hi"}],
        }
    )
    report = parse_report(text)
    assert report.findings[0].confidence == "verified"
    assert report.unverified == []


def test_parse_report_missing_summary_raises():
    with pytest.raises(ReportParseError):
        parse_report("not json at all")


def test_report_round_trips_through_json():
    report = ResearchReport(
        summary="s",
        findings=[Finding(text="a", source_ids=["S1"])],
        sources=[Source(id="S1", file_path="f.py", line_range="1-2", excerpt="x")],
    )
    restored = ResearchReport.from_json(report.to_json())
    assert restored.summary == report.summary
    assert restored.findings[0].text == "a"
    assert restored.findings[0].confidence == "verified"
    assert restored.sources[0].file_path == "f.py"


# ---------------------------------------------------------------------------
# ResearchAgent end-to-end through FakeAgentAdapter
# ---------------------------------------------------------------------------

def test_research_end_to_end_with_fake_adapter(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)
        (Path(working_directory) / "README.md").write_text("# Demo\nThis project does X.\n")

        report = ResearchReport(
            summary="The project does X per the README.",
            findings=[
                Finding(text="The project does X", source_ids=["S1"], category="behavior"),
                Finding(text="An unverified claim", source_ids=[]),
            ],
            sources=[Source(id="S1", file_path="README.md", line_range="1-2", excerpt="# Demo")],
        )

        adapter = FakeAgentAdapter(db)
        loader = RepositoryContextLoader(app.config["ALLOWED_PROJECT_ROOTS"])
        agent = ResearchAgent(adapter, db=db, context_loader=loader)
        context = AgentContext(project_id=project_id, working_directory=working_directory)

        research_session_id, outcome = agent.research(
            context,
            "What does this project do?",
            repo_root=working_directory,
            options={"script": scripted_report(report)},
        )

        assert outcome.state == "COMPLETE"
        assert outcome.report.summary == report.summary
        assert len(outcome.report.unverified) == 1
        assert outcome.report.unverified[0].text == "An unverified claim"
        assert outcome.report.findings[0].confidence == "verified"

        assert research_session_id is not None
        stored = agent_models.get_research_session(db, research_session_id)
        assert stored.status == "COMPLETED"
        assert stored.findings_summary == report.summary
        assert stored.agent_session_id is not None
        assert json.loads(stored.report_json)["summary"] == report.summary
        assert len(stored.source_references) == 1
        assert stored.duration_seconds is not None

        # The underlying agent session actually received the repository context
        # (README content) in its prompt.
        agent_session = agent_models.get_agent_session(db, stored.agent_session_id)
        prompt_event = next(
            e for e in agent_models.list_agent_events(db, agent_session.id)
            if e.event_type == "PromptSubmitted"
        )
        assert "This project does X" in prompt_event.data

        # No exclusive project lock is ever taken for a research session.
        assert db.execute("SELECT COUNT(*) FROM project_locks").fetchone()[0] == 0


def test_research_includes_research_role_skills(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)
        prompt_models.create_fragment(
            db, "research-skill", "Always cite the exact file and line.",
            skill_status="active", skill_roles=["RESEARCH"],
        )

        report = ResearchReport(summary="done", findings=[], sources=[])
        adapter = FakeAgentAdapter(db)
        agent = ResearchAgent(adapter, db=db)
        context = AgentContext(project_id=project_id, working_directory=working_directory)

        _, outcome = agent.research(
            context, "Q?", options={"script": scripted_report(report)},
        )
        assert outcome.state == "COMPLETE"

        sessions = agent_models.list_agent_sessions_for_project(db, project_id)
        prompt_event = next(
            e for e in agent_models.list_agent_events(db, sessions[0].id)
            if e.event_type == "PromptSubmitted"
        )
        assert "Always cite the exact file and line." in prompt_event.data


def test_research_session_failed_when_reply_is_not_parseable(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)
        adapter = FakeAgentAdapter(db)
        agent = ResearchAgent(adapter, db=db)
        context = AgentContext(project_id=project_id, working_directory=working_directory)

        script = [{"action": "message", "text": "I refuse to reply in JSON."}, {"action": "complete"}]
        research_session_id, outcome = agent.research(context, "Q?", options={"script": script})

        assert outcome.state == "FAILED"
        stored = agent_models.get_research_session(db, research_session_id)
        assert stored.status == "FAILED"
        assert stored.error


# ---------------------------------------------------------------------------
# Time-limit enforcement (Ralph's max_runtime_seconds shape)
# ---------------------------------------------------------------------------

class _NeverFinishesAdapter(AgentAdapter):
    """A minimal AgentAdapter double whose session never leaves RUNNING, to
    exercise the time_limit_seconds/TIMED_OUT path deterministically. Real
    adapters (Codex/Claude/Fake) all resolve synchronously within `start()`
    (Ralph's orchestrator assumes the same), so this path only matters once an
    adapter streams asynchronously -- exercised here with a stub rather than
    left untested."""

    def __init__(self):
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
        return self.status(1)

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


def test_research_times_out_when_session_never_completes():
    adapter = _NeverFinishesAdapter()
    tick = {"t": 0.0}

    def fake_clock():
        tick["t"] += 1.0
        return tick["t"]

    agent = ResearchAgent(adapter, db=None, clock=fake_clock, poll_interval=0)
    context = AgentContext(project_id=1, working_directory="/tmp")

    research_session_id, outcome = agent.research(context, "Q?", time_limit_seconds=2.0)

    assert research_session_id is None  # no db given: persistence is optional
    assert outcome.state == "TIMED_OUT"
    assert adapter.stopped is True


# ---------------------------------------------------------------------------
# Cost-limit enforcement (docs/AGENT_ADAPTER.md §23 "Cost limits")
# ---------------------------------------------------------------------------


class _RunningWithCostAdapter(AgentAdapter):
    """Stays RUNNING forever while reporting a `cost_usd` in its usage
    metadata, to exercise the cost check inside the poll loop itself (the
    async-adapter path -- distinct from the synchronous-adapter path covered
    by `test_research_cost_limit_exceeded_stops_session_and_fails` below,
    where the session is already COMPLETED by the time cost is checked)."""

    def __init__(self, cost_usd: float):
        self._cost_usd = cost_usd
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
        return self.status(1)

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
            status="RUNNING", metadata={"usage": {"cost_usd": self._cost_usd}},
            started_at="", last_activity_at="",
        )

    def stream(self, session_id, after_id=None):
        return []


def test_research_cost_limit_exceeded_stops_running_session():
    adapter = _RunningWithCostAdapter(cost_usd=2.0)
    agent = ResearchAgent(adapter, db=None, poll_interval=0)
    context = AgentContext(project_id=1, working_directory="/tmp")

    research_session_id, outcome = agent.research(
        context, "Q?", cost_limit_usd=1.0, time_limit_seconds=100.0,
    )

    assert research_session_id is None  # no db given: persistence is optional
    assert outcome.state == "COST_LIMIT_EXCEEDED"
    assert adapter.stopped is True


class _CostReportingFakeAdapter(FakeAgentAdapter):
    """FakeAgentAdapter (so sessions are real, FK-valid DB rows, and
    completion happens synchronously exactly like Codex/Claude/Fake today)
    but with `status()` reporting a fixed `cost_usd` in its session usage
    metadata, to exercise `cost_limit_usd` enforcement deterministically --
    FakeAgentAdapter itself never reports usage/cost."""

    def __init__(self, db, cost_usd: float):
        super().__init__(db)
        self._cost_usd = cost_usd
        self.stopped = False

    def status(self, session_id):
        session = super().status(session_id)
        session.metadata = {**(session.metadata or {}), "usage": {"cost_usd": self._cost_usd}}
        return session

    def stop(self, session_id) -> None:
        self.stopped = True
        super().stop(session_id)


def test_research_cost_limit_exceeded_stops_session_and_fails(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)
        report = ResearchReport(summary="expensive finding", findings=[], sources=[])
        adapter = _CostReportingFakeAdapter(db, cost_usd=1.0)
        agent = ResearchAgent(adapter, db=db)
        context = AgentContext(project_id=project_id, working_directory=working_directory)

        research_session_id, outcome = agent.research(
            context, "Q?", cost_limit_usd=0.5, options={"script": scripted_report(report)},
        )

        assert outcome.state == "COST_LIMIT_EXCEEDED"
        assert outcome.report is None
        # The adapter already completed synchronously (like every real
        # adapter today) by the time the over-budget check runs, so there is
        # nothing left to stop() -- the limit is enforced by refusing to
        # treat the finished session as a trustworthy completion, not by an
        # explicit stop call (that only fires for the RUNNING-loop path,
        # covered separately below).
        assert adapter.stopped is False

        stored = agent_models.get_research_session(db, research_session_id)
        assert stored.status == "FAILED"
        assert "cost limit" in stored.error.lower()
        assert stored.cost_usd == 1.0


def test_research_within_cost_limit_completes_normally(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)
        report = ResearchReport(summary="cheap finding", findings=[], sources=[])
        adapter = _CostReportingFakeAdapter(db, cost_usd=0.1)
        agent = ResearchAgent(adapter, db=db)
        context = AgentContext(project_id=project_id, working_directory=working_directory)

        research_session_id, outcome = agent.research(
            context, "Q?", cost_limit_usd=0.5, options={"script": scripted_report(report)},
        )

        assert outcome.state == "COMPLETE"
        assert adapter.stopped is False
        stored = agent_models.get_research_session(db, research_session_id)
        assert stored.status == "COMPLETED"
        assert stored.cost_usd == 0.1


# ---------------------------------------------------------------------------
# Research report redaction (app/runs/security.py)
# ---------------------------------------------------------------------------


def test_research_report_secrets_redacted_before_storage(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        secret = "sk-abcdef0123456789abcdef"
        report = ResearchReport(
            summary=f"The API key is {secret} per the config.",
            findings=[Finding(text=f"Found leaked key {secret}", source_ids=["S1"])],
            sources=[Source(id="S1", file_path="config.py", excerpt=f"API_KEY={secret}")],
        )

        adapter = FakeAgentAdapter(db)
        agent = ResearchAgent(adapter, db=db)
        context = AgentContext(project_id=project_id, working_directory=working_directory)

        research_session_id, outcome = agent.research(
            context, "What's in the config?", options={"script": scripted_report(report)},
        )

        assert outcome.state == "COMPLETE"
        assert secret not in outcome.report.summary
        assert secret not in outcome.report.findings[0].text
        assert secret not in outcome.report.sources[0].excerpt

        stored = agent_models.get_research_session(db, research_session_id)
        assert secret not in stored.findings_summary
        assert secret not in stored.report_json
        assert secret not in json.dumps(stored.source_references)
        assert "[REDACTED]" in stored.report_json


def test_research_report_custom_redaction_pattern_applied(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        report = ResearchReport(summary="Internal codeword: PROJECT-PHOENIX-42", findings=[], sources=[])
        adapter = FakeAgentAdapter(db)
        agent = ResearchAgent(adapter, db=db, extra_patterns=(r"PROJECT-PHOENIX-\d+",))
        context = AgentContext(project_id=project_id, working_directory=working_directory)

        research_session_id, outcome = agent.research(
            context, "Q?", options={"script": scripted_report(report)},
        )

        assert outcome.state == "COMPLETE"
        assert "PROJECT-PHOENIX-42" not in outcome.report.summary
        stored = agent_models.get_research_session(db, research_session_id)
        assert "PROJECT-PHOENIX-42" not in stored.findings_summary


def test_research_prompt_includes_relevant_reviewed_kb_entries(app):
    """Task 50.5: a reviewed knowledge base entry relevant to the question is
    surfaced in the prompt before repository context."""
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)
        knowledge_models.create_entry(
            db, "note", "JWT auth pattern", "Use PyJWT with HS256 for token signing.",
            confidence="reviewed", slug="jwt-auth-pattern",
        )

        report = ResearchReport(summary="done", findings=[], sources=[])
        adapter = FakeAgentAdapter(db)
        agent = ResearchAgent(adapter, db=db)
        context = AgentContext(project_id=project_id, working_directory=working_directory)

        _, outcome = agent.research(
            context, "How should JWT auth be implemented?", options={"script": scripted_report(report)},
        )
        assert outcome.state == "COMPLETE"

        sessions = agent_models.list_agent_sessions_for_project(db, project_id)
        prompt_event = next(
            e for e in agent_models.list_agent_events(db, sessions[0].id)
            if e.event_type == "PromptSubmitted"
        )
        assert "PyJWT" in prompt_event.data
        assert "shared knowledge base" in prompt_event.data


def test_research_completion_ingests_sourced_findings_into_knowledge_base(app):
    """Task 50.5: a completed session's sourced findings become suggested
    (unverified) knowledge base entries, tagged from the finding's category."""
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        report = ResearchReport(
            summary="done",
            findings=[
                Finding(text="Sessions expire after 30 minutes", source_ids=["S1"], category="auth"),
                Finding(text="Unsourced speculation", source_ids=[]),
            ],
            sources=[Source(id="S1", file_path="app/sessions.py", line_range="10-12", excerpt="expiry")],
        )
        adapter = FakeAgentAdapter(db)
        agent = ResearchAgent(adapter, db=db)
        context = AgentContext(project_id=project_id, working_directory=working_directory)

        research_session_id, outcome = agent.research(
            context, "How long do sessions last?", options={"script": scripted_report(report)},
        )
        assert outcome.state == "COMPLETE"

        entries = knowledge_models.list_entries(db, confidence="unverified")
        assert len(entries) == 1
        assert "Sessions expire after 30 minutes" in entries[0].content
        assert entries[0].source_project_id == project_id
        assert entries[0].source_session_id == research_session_id
        assert entries[0].tags == ["auth"]
