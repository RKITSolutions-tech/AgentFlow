from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.agents import models as agent_models
from app.agents.base import AgentContext
from app.agents.claude import ClaudeAdapter
from app.db import get_db
from app.execution import models as exec_models
from app.execution.base import ExecutionProvider
from app.projects import models as project_models
from app.settings import models as settings_models


class FakeExecutionProvider(ExecutionProvider):
    """Minimal ExecutionProvider test double, mirroring the one in
    tests/test_codex_adapter.py: each queued turn supplies the canned
    stdout/stderr lines a real `claude -p --output-format stream-json` run
    would have produced, so tests never invoke the real CLI."""

    def __init__(self):
        self.commands: list[list[str]] = []
        self.options: list[dict[str, Any]] = []
        self._turns: list[dict[str, Any]] = []
        self._events: dict[int, list[exec_models.ProcessEvent]] = {}
        self._processes: dict[int, exec_models.Process] = {}
        self._pending_terminal: dict[int, tuple[str, int]] = {}
        self._next_process_id = 1
        self._fail_next_launch: OSError | None = None

    def queue_turn(
        self, stdout_lines: list[str], exit_code: int = 0, stderr_lines: list[str] | None = None
    ) -> None:
        self._turns.append(
            {"stdout": stdout_lines, "stderr": stderr_lines or [], "exit_code": exit_code}
        )

    def fail_next_launch(self, exc: OSError) -> None:
        self._fail_next_launch = exc

    def finish_process(self, process_id: int) -> exec_models.Process:
        status, exit_code = self._pending_terminal[process_id]
        process = replace(self._processes[process_id], status=status, exit_code=exit_code)
        self._processes[process_id] = process
        return process

    def append_output(self, process_id: int, stream: str, data: str) -> None:
        events = self._events.setdefault(process_id, [])
        events.append(
            exec_models.ProcessEvent(
                id=len(events) + 1,
                context_id=0,
                process_id=process_id,
                event_type="ProcessOutput",
                stream=stream,
                data=data,
                created_at="",
            )
        )

    def available(self) -> bool:
        return True

    def create_context(self, configuration):
        raise NotImplementedError

    def execute(self, command, options=None):
        process = self.start_process(command, options)
        return self.finish_process(process.id)

    def start_process(self, command, options=None):
        if self._fail_next_launch is not None:
            exc, self._fail_next_launch = self._fail_next_launch, None
            raise exc

        self.commands.append(command)
        self.options.append(options or {})
        turn = self._turns.pop(0)

        process_id = self._next_process_id
        self._next_process_id += 1

        events = []
        for line in turn["stdout"]:
            events.append(
                exec_models.ProcessEvent(
                    id=len(events) + 1,
                    context_id=0,
                    process_id=process_id,
                    event_type="ProcessOutput",
                    stream="stdout",
                    data=line,
                    created_at="",
                )
            )
        for line in turn["stderr"]:
            events.append(
                exec_models.ProcessEvent(
                    id=len(events) + 1,
                    context_id=0,
                    process_id=process_id,
                    event_type="ProcessOutput",
                    stream="stderr",
                    data=line,
                    created_at="",
                )
            )
        self._events[process_id] = events

        process = exec_models.Process(
            id=process_id,
            context_id=0,
            provider="host",
            external_process_id=None,
            command_summary=" ".join(command),
            working_directory="",
            status="RUNNING",
            started_at="",
            completed_at="",
            exit_code=None,
        )
        self._processes[process_id] = process
        self._pending_terminal[process_id] = (
            "COMPLETED" if turn["exit_code"] == 0 else "FAILED",
            turn["exit_code"],
        )
        return process

    def stop_process(self, process_id):
        process = self._processes.get(process_id)
        if process is None:
            return
        self._processes[process_id] = replace(process, status="STOPPED", exit_code=None)
        self._pending_terminal.pop(process_id, None)

    def signal_process(self, process_id, sig):
        raise NotImplementedError

    def stream_output(self, process_id, after_id=None):
        events = self._events.get(process_id, [])
        if after_id is None:
            return list(events)
        return [e for e in events if e.id > after_id]

    def process_status(self, process_id):
        return self._processes.get(process_id)

    def destroy_context(self, context_id):
        raise NotImplementedError


def _init_line(session_id: str) -> str:
    """First line of a `claude -p --output-format stream-json` run."""
    return json.dumps({"type": "system", "subtype": "init", "session_id": session_id})


def _assistant_text_line(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    )


def _assistant_question_line(question_id: str, question: str, options: list[str]) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": question_id,
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "header": "Choice",
                                    "question": question,
                                    "options": [{"label": o} for o in options],
                                    "multiSelect": False,
                                }
                            ]
                        },
                    }
                ],
            },
        }
    )


def _result_line(text: str, is_error: bool = False) -> str:
    payload: dict[str, Any] = {"type": "result", "is_error": is_error, "usage": {}}
    payload["result"] = text
    return json.dumps(payload)


def _task_complete_lines(text: str) -> list[str]:
    return [_assistant_text_line(text), _result_line(text)]


def test_available_false_when_binary_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda binary: None)
    adapter = ClaudeAdapter()

    assert adapter.available() is False
    assert adapter.version() == ""


def test_available_true_and_version_parsed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda binary: "/usr/bin/claude")

    def fake_run(command, **kwargs):
        assert command == ["/usr/bin/claude", "--version"]
        return subprocess.CompletedProcess(command, 0, stdout="1.2.3 (Claude Code)\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = ClaudeAdapter()

    assert adapter.available() is True
    assert adapter.version() == "1.2.3"


def test_capabilities_include_resume_discovery_and_structured_events():
    adapter = ClaudeAdapter()

    assert adapter.capabilities() == frozenset(
        {
            "resume", "session_discovery", "structured_events", "model_selection",
            "token_usage", "permission_modes",
        }
    )


def _make_project(db, app) -> tuple[int, str]:
    working_directory = str(Path(app.config["allowed_root"]) / "repo")
    Path(working_directory).mkdir(parents=True, exist_ok=True)
    project_id = project_models.create_project(db, "Claude Project")
    project_models.add_repository(
        db,
        project_id,
        "repo",
        working_directory,
        app.config["ALLOWED_PROJECT_ROOTS"],
        is_primary=True,
    )
    return project_id, working_directory


def test_start_happy_path(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn([_init_line("ext-session-1"), *_task_complete_lines("all done")])
        adapter = ClaudeAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )

        session = adapter.start(context, "start it")

        assert provider.commands[0] == [
            "claude", "-p", "--output-format", "stream-json", "--verbose", "start it",
        ]
        assert session.external_session_id == "ext-session-1"
        assert session.status == "COMPLETED"

        events = agent_models.list_agent_events(db, session.id)
        assert [e.event_type for e in events] == ["PromptSubmitted", "AgentText", "AgentComplete"]
        assert events[-1].data == "all done"


def test_start_passes_model_flag_and_persists_it_for_later_turns(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn([_init_line("ext-session-model"), *_task_complete_lines("first turn done")])
        adapter = ClaudeAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
            model="claude-sonnet-5",
        )
        session = adapter.start(context, "start it")

        assert provider.commands[0] == [
            "claude", "-p", "--output-format", "stream-json", "--verbose",
            "--model", "claude-sonnet-5", "start it",
        ]
        assert session.metadata["model"] == "claude-sonnet-5"

        provider.queue_turn([_init_line("ext-session-model"), *_task_complete_lines("second turn done")])
        adapter.resume(session.id, prompt="keep going")
        assert provider.commands[1] == [
            "claude", "--resume", "ext-session-model", "-p", "--output-format", "stream-json",
            "--verbose", "--model", "claude-sonnet-5", "keep going",
        ]


def test_start_targets_local_model_via_env_vars(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)
        settings_models.add_model(
            db, "local", "llama-3.1-8b-instruct", base_url="http://localhost:1234/v1", api_key="s3cret"
        )

        provider = FakeExecutionProvider()
        provider.queue_turn([_init_line("ext-session-local"), *_task_complete_lines("done")])
        adapter = ClaudeAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
            model="llama-3.1-8b-instruct",
        )
        adapter.start(context, "start it")

        assert provider.commands[0] == [
            "claude", "-p", "--output-format", "stream-json", "--verbose",
            "--model", "llama-3.1-8b-instruct", "start it",
        ]
        assert provider.options[0]["environment"] == {
            "ANTHROPIC_BASE_URL": "http://localhost:1234/v1",
            "ANTHROPIC_AUTH_TOKEN": "s3cret",
        }


def test_resume_without_external_session_id_raises(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        session_id = agent_models.create_agent_session(
            db,
            project_id=project_id,
            agent_type="claude",
            execution_provider="host",
            execution_target="1",
            metadata={"working_directory": working_directory},
        )

        adapter = ClaudeAdapter(db, FakeExecutionProvider())

        with pytest.raises(ValueError):
            adapter.resume(session_id, prompt="anything")


def test_ask_user_question_tool_use_becomes_clarifying_question(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn(
            [
                _init_line("ext-session-q"),
                _assistant_question_line("toolu_1", "Which database?", ["Postgres", "SQLite"]),
            ]
        )
        adapter = ClaudeAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )
        session = adapter.start(context, "start it")

        questions = agent_models.list_clarifying_questions(db, session.id)
        assert len(questions) == 1
        assert questions[0].question == "Which database?"
        assert questions[0].external_id == "toolu_1"
        assert [o["label"] for o in questions[0].options] == ["Postgres", "SQLite"]


def test_send_launches_turn_without_blocking_and_stream_reports_progress(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn([_init_line("ext-session-4"), *_task_complete_lines("first turn done")])
        adapter = ClaudeAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )
        session = adapter.start(context, "start it")
        assert session.external_session_id == "ext-session-4"

        provider.queue_turn([_assistant_text_line("thinking...")])
        result = adapter.send(session.id, "keep going")
        assert result is None

        running_session = agent_models.get_agent_session(db, session.id)
        assert running_session.status == "RUNNING"
        process_id = running_session.metadata["process_id"]
        assert provider.process_status(process_id).status == "RUNNING"

        with pytest.raises(ValueError):
            adapter.send(session.id, "another one")

        partial_events = adapter.stream(session.id)
        assert [e.event_type for e in partial_events] == [
            "PromptSubmitted", "AgentText", "AgentComplete", "PromptSubmitted", "AgentText",
        ]

        provider.append_output(process_id, "stdout", _result_line("second turn done"))
        provider.finish_process(process_id)

        final_events = adapter.stream(session.id, after_id=partial_events[-1].id)
        assert [e.event_type for e in final_events] == ["AgentComplete"]
        assert final_events[-1].data == "second turn done"
        assert agent_models.get_agent_session(db, session.id).status == "COMPLETED"


def test_stream_reports_error_once_process_exits_without_result(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn([_init_line("ext-session-5"), *_task_complete_lines("first turn done")])
        adapter = ClaudeAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )
        session = adapter.start(context, "start it")

        provider.queue_turn([], exit_code=1, stderr_lines=["boom"])
        adapter.send(session.id, "keep going")
        process_id = agent_models.get_agent_session(db, session.id).metadata["process_id"]

        provider.finish_process(process_id)

        events = adapter.stream(session.id)
        assert events[-1].event_type == "AgentError"
        assert events[-1].data == "boom"
        assert agent_models.get_agent_session(db, session.id).status == "FAILED"


def test_result_with_is_error_marks_session_failed(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn([_init_line("ext-session-err"), _result_line("hit max turns", is_error=True)])
        adapter = ClaudeAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )
        session = adapter.start(context, "start it")

        assert session.status == "FAILED"
        events = agent_models.list_agent_events(db, session.id)
        assert events[-1].event_type == "AgentError"
        assert events[-1].data == "hit max turns"


def test_stop_terminates_running_turn_and_blocks_further_input(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn([_init_line("ext-session-6"), *_task_complete_lines("first turn done")])
        adapter = ClaudeAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )
        session = adapter.start(context, "start it")

        provider.queue_turn([_assistant_text_line("thinking...")])
        adapter.send(session.id, "keep going")
        process_id = agent_models.get_agent_session(db, session.id).metadata["process_id"]

        adapter.stop(session.id)

        assert provider.process_status(process_id).status == "STOPPED"
        stopped_session = agent_models.get_agent_session(db, session.id)
        assert stopped_session.status == "STOPPED"

        with pytest.raises(ValueError):
            adapter.send(session.id, "too late")


def test_discover_sessions_imports_matching_native_sessions_and_is_idempotent(app, monkeypatch, tmp_path):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        claude_home = tmp_path / "claude-home"
        project_dir = claude_home / "projects" / "-some-encoded-path"
        project_dir.mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

        matching = project_dir / "native-match.jsonl"
        matching.write_text(json.dumps({"cwd": working_directory, "sessionId": "native-match"}) + "\n")

        other_cwd = project_dir / "native-other.jsonl"
        other_cwd.write_text(json.dumps({"cwd": "/somewhere/else", "sessionId": "native-other"}) + "\n")

        adapter = ClaudeAdapter(db, FakeExecutionProvider())

        sessions = adapter.discover_sessions(project_id)
        assert {s.external_session_id for s in sessions} == {"native-match"}
        assert sessions[0].status == "COMPLETED"

        sessions_again = adapter.discover_sessions(project_id)
        assert len(sessions_again) == 1


def test_claude_adapter_implements_full_agent_adapter_contract():
    from app.agents.base import AgentAdapter

    for name in AgentAdapter.__abstractmethods__:
        assert hasattr(ClaudeAdapter, name)
