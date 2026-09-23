from __future__ import annotations

import subprocess
from pathlib import Path

from app.agents import models as agent_models
from app.agents.base import AgentContext
from app.agents.fake import FakeAgentAdapter
from app.agents.verification import capture_git_diff, run_configured_test
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.projects import models as project_models


def _init_fixture_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "fixture.txt").write_text("initial content\n")
    subprocess.run(["git", "add", "fixture.txt"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True
    )


def test_fake_agent_vertical_slice(app):
    """Project -> FakeAgent -> diff -> test -> persisted result, with a
    scripted failure and retry, and no external model involved.
    """
    with app.app_context():
        db = get_db()

        repo_path = Path(app.config["allowed_root"]) / "fixture-repo"
        repo_path.mkdir()
        _init_fixture_repo(repo_path)

        project_id = project_models.create_project(db, "Vertical Slice Project")
        project_models.add_repository(
            db,
            project_id,
            "repo",
            str(repo_path),
            app.config["ALLOWED_PROJECT_ROOTS"],
            is_primary=True,
        )

        provider = HostExecutionProvider(
            app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"]
        )
        context = provider.create_context({"working_directory": str(repo_path)})

        adapter = FakeAgentAdapter(db)
        agent_context = AgentContext(
            project_id=project_id,
            working_directory=str(repo_path),
            execution_provider="host",
            execution_target=str(context.id),
        )

        script = [
            {"action": "message", "text": "Reading fixture.txt"},
            {"action": "write_file", "path": "fixture.txt", "content": "attempted content\n"},
            {"action": "fail", "error": "simulated transient failure"},
            {"action": "message", "text": "Retrying after failure"},
            {"action": "write_file", "path": "fixture.txt", "content": "final content\n"},
            {"action": "complete"},
        ]

        # start() runs until the scripted failure and stops there.
        session = adapter.start(agent_context, "Update fixture.txt", {"script": script})
        assert session.status == "FAILED"
        assert session.metadata["cursor"] == 3

        events = adapter.stream(session.id)
        assert [e.event_type for e in events] == [
            "PromptSubmitted",
            "AgentText",
            "AgentToolCall",
            "AgentToolResult",
            "AgentError",
        ]
        assert events[0].data == "Update fixture.txt"
        assert events[-1].data == "simulated transient failure"

        # resume() continues the same script from the persisted cursor.
        session = adapter.resume(session.id, prompt=None)
        assert session.status == "COMPLETED"
        assert session.metadata["cursor"] == len(script)

        events = adapter.stream(session.id)
        assert [e.event_type for e in events][-4:] == [
            "AgentText",
            "AgentToolCall",
            "AgentToolResult",
            "AgentComplete",
        ]

        assert (repo_path / "fixture.txt").read_text() == "final content\n"

        diff = capture_git_diff(provider, context.id)
        assert "-initial content" in diff
        assert "+final content" in diff

        test_status, test_exit_code, test_output = run_configured_test(
            provider, context.id, ["sh", "-c", "grep -q 'final content' fixture.txt"]
        )
        assert test_status == "PASSED"
        assert test_exit_code == 0

        agent_models.record_result(
            db,
            session.id,
            status=session.status,
            git_diff=diff,
            test_status=test_status,
            test_exit_code=test_exit_code,
            test_output=test_output,
        )

        persisted = agent_models.get_result(db, session.id)
        assert persisted is not None
        assert persisted.status == "COMPLETED"
        assert "+final content" in persisted.git_diff
        assert persisted.test_status == "PASSED"
        assert persisted.test_exit_code == 0

        persisted_session = agent_models.get_agent_session(db, session.id)
        assert persisted_session.project_id == project_id
        assert persisted_session.agent_type == "fake"


def test_fake_agent_stop_then_resume(app):
    """stop() must not break the scripted resume() flow used by tests."""
    with app.app_context():
        db = get_db()

        working_directory = str(Path(app.config["allowed_root"]))
        project_id = project_models.create_project(db, "Stop Vertical Slice Project")
        project_models.add_repository(
            db,
            project_id,
            "repo",
            working_directory,
            app.config["ALLOWED_PROJECT_ROOTS"],
            is_primary=True,
        )

        adapter = FakeAgentAdapter(db)
        agent_context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="",
        )

        script = [{"action": "message", "text": "Reading fixture.txt"}]

        session = adapter.start(agent_context, "Update fixture.txt", {"script": script})
        assert session.status == "COMPLETED"

        adapter.stop(session.id)
        stopped = agent_models.get_agent_session(db, session.id)
        assert stopped.status == "STOPPED"
        assert agent_models.list_agent_events(db, session.id)[-1].event_type == "AgentStatus"

        resumed = adapter.resume(session.id, prompt=None)
        assert resumed.status == "COMPLETED"
