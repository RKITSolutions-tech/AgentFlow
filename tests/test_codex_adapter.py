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
from app.agents.codex import CodexAdapter
from app.db import get_db
from app.execution import models as exec_models
from app.execution.base import ExecutionProvider
from app.projects import models as project_models


class FakeExecutionProvider(ExecutionProvider):
    """Minimal ExecutionProvider test double.

    CodexAdapter calls `execute()`, `start_process()`, `stream_output()` and
    `process_status()`; each queued turn supplies the canned stdout/stderr
    lines a real `codex exec` run would have produced, so tests never invoke
    the real CLI or network.

    `start_process()` mimics a still-running process: its events are
    immediately available for `stream_output()` to reveal (like the real
    HostExecutionProvider, which drains stdout into events well before the
    process exits), but `process_status()` keeps reporting RUNNING until the
    test calls `finish_process()` to simulate the process actually exiting.
    `execute()` is `start_process()` immediately followed by `finish_process()`,
    matching HostExecutionProvider's own `execute = start_process + wait`.
    """

    def __init__(self):
        self.commands: list[list[str]] = []
        self._turns: list[dict[str, Any]] = []
        self._events: dict[int, list[exec_models.ProcessEvent]] = {}
        self._processes: dict[int, exec_models.Process] = {}
        self._pending_terminal: dict[int, tuple[str, int]] = {}
        self._next_process_id = 1

    def queue_turn(
        self, stdout_lines: list[str], exit_code: int = 0, stderr_lines: list[str] | None = None
    ) -> None:
        self._turns.append(
            {"stdout": stdout_lines, "stderr": stderr_lines or [], "exit_code": exit_code}
        )

    def finish_process(self, process_id: int) -> exec_models.Process:
        status, exit_code = self._pending_terminal[process_id]
        process = replace(self._processes[process_id], status=status, exit_code=exit_code)
        self._processes[process_id] = process
        return process

    def append_output(self, process_id: int, stream: str, data: str) -> None:
        """Simulates more stdout/stderr arriving on an already-started process."""
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
        self.commands.append(command)
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
        raise NotImplementedError

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


def _session_meta_line(session_id: str, cwd: str) -> str:
    return json.dumps({"type": "session_meta", "payload": {"session_id": session_id, "cwd": cwd}})


def _task_complete_line(message: str) -> str:
    return json.dumps(
        {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": message}}
    )


def _agent_message_line(message: str) -> str:
    return json.dumps(
        {"type": "event_msg", "payload": {"type": "agent_message", "message": message}}
    )


def test_available_false_when_binary_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda binary: None)
    adapter = CodexAdapter()

    assert adapter.available() is False
    assert adapter.version() == ""


def test_available_true_and_version_parsed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda binary: "/usr/bin/codex")

    def fake_run(command, **kwargs):
        assert command == ["/usr/bin/codex", "--version"]
        return subprocess.CompletedProcess(command, 0, stdout="codex-cli 0.147.0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = CodexAdapter()

    assert adapter.available() is True
    assert adapter.version() == "0.147.0"


def test_detection_result_is_cached(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda binary: "/usr/bin/codex")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="codex-cli 1.0.0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = CodexAdapter()

    adapter.available()
    adapter.version()
    adapter.available()

    assert len(calls) == 1


def test_available_false_when_version_command_fails(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda binary: "/usr/bin/codex")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = CodexAdapter()

    assert adapter.available() is False
    assert adapter.version() == ""


def test_available_false_when_binary_times_out(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda binary: "/usr/bin/codex")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 5.0))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = CodexAdapter()

    assert adapter.available() is False


def test_stop_raises_not_implemented():
    adapter = CodexAdapter()

    with pytest.raises(NotImplementedError):
        adapter.stop(session_id=1)


def test_capabilities_include_resume_discovery_and_structured_events():
    adapter = CodexAdapter()

    assert adapter.capabilities() == frozenset(
        {"resume", "session_discovery", "structured_events"}
    )


def _make_project(db, app) -> tuple[int, str]:
    working_directory = str(Path(app.config["allowed_root"]) / "repo")
    Path(working_directory).mkdir(parents=True, exist_ok=True)
    project_id = project_models.create_project(db, "Codex Project")
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
        provider.queue_turn(
            [
                _session_meta_line("ext-session-1", working_directory),
                _task_complete_line("all done"),
            ]
        )
        adapter = CodexAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )

        session = adapter.start(context, "do the thing")

        assert session.status == "COMPLETED"
        assert session.external_session_id == "ext-session-1"
        assert provider.commands[0] == ["codex", "exec", "--json", "do the thing"]

        events = agent_models.list_agent_events(db, session.id)
        assert [e.event_type for e in events] == ["PromptSubmitted", "AgentComplete"]
        assert events[0].data == "do the thing"
        assert events[1].data == "all done"


def test_start_failure_path(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn(
            [_session_meta_line("ext-session-2", working_directory)],
            exit_code=1,
            stderr_lines=["boom"],
        )
        adapter = CodexAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )

        session = adapter.start(context, "do the thing")

        assert session.status == "FAILED"
        events = agent_models.list_agent_events(db, session.id)
        assert [e.event_type for e in events] == ["PromptSubmitted", "AgentError"]
        assert events[1].data == "boom"


def test_resume_reuses_external_session_id(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn(
            [
                _session_meta_line("ext-session-3", working_directory),
                _task_complete_line("first turn done"),
            ]
        )
        adapter = CodexAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )
        session = adapter.start(context, "start it")

        provider.queue_turn(
            [
                _session_meta_line("ext-session-3", working_directory),
                _task_complete_line("second turn done"),
            ]
        )
        resumed = adapter.resume(session.id, prompt="keep going")

        assert resumed.status == "COMPLETED"
        assert resumed.external_session_id == "ext-session-3"
        assert provider.commands[1] == [
            "codex",
            "exec",
            "resume",
            "ext-session-3",
            "--json",
            "keep going",
        ]

        events = agent_models.list_agent_events(db, session.id)
        assert [e.event_type for e in events] == [
            "PromptSubmitted",
            "AgentComplete",
            "PromptSubmitted",
            "AgentComplete",
        ]
        assert events[-1].data == "second turn done"


def test_resume_without_external_session_id_raises(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        session_id = agent_models.create_agent_session(
            db,
            project_id=project_id,
            agent_type="codex",
            execution_provider="host",
            execution_target="1",
            metadata={"working_directory": working_directory},
        )

        adapter = CodexAdapter(db, FakeExecutionProvider())

        with pytest.raises(ValueError):
            adapter.resume(session_id, prompt="anything")


def test_discover_sessions_imports_matching_native_sessions_and_is_idempotent(app, monkeypatch, tmp_path):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        codex_home = tmp_path / "codex-home"
        sessions_dir = codex_home / "sessions" / "2026" / "09" / "23"
        sessions_dir.mkdir(parents=True)
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        matching = sessions_dir / "rollout-2026-09-23T00-00-00-matching.jsonl"
        matching.write_text(_session_meta_line("native-match", working_directory) + "\n")

        other_cwd = sessions_dir / "rollout-2026-09-23T00-00-01-other.jsonl"
        other_cwd.write_text(_session_meta_line("native-other", "/somewhere/else") + "\n")

        adapter = CodexAdapter(db, FakeExecutionProvider())

        sessions = adapter.discover_sessions(project_id)
        assert {s.external_session_id for s in sessions} == {"native-match"}
        assert sessions[0].status == "COMPLETED"

        # A second call must not create a duplicate row for the same native session.
        sessions_again = adapter.discover_sessions(project_id)
        assert len(sessions_again) == 1


def test_start_surfaces_agent_message_lines_as_agent_text(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn(
            [
                _session_meta_line("ext-session-text", working_directory),
                _agent_message_line("thinking..."),
                _task_complete_line("all done"),
            ]
        )
        adapter = CodexAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )

        session = adapter.start(context, "do the thing")

        events = agent_models.list_agent_events(db, session.id)
        assert [e.event_type for e in events] == [
            "PromptSubmitted",
            "AgentText",
            "AgentComplete",
        ]
        assert events[1].data == "thinking..."


def test_send_requires_existing_external_session_id(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        session_id = agent_models.create_agent_session(
            db,
            project_id=project_id,
            agent_type="codex",
            execution_provider="host",
            execution_target="1",
            metadata={"working_directory": working_directory},
        )

        adapter = CodexAdapter(db, FakeExecutionProvider())

        with pytest.raises(ValueError):
            adapter.send(session_id, "anything")


def test_send_launches_turn_without_blocking_and_stream_reports_progress(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn(
            [
                _session_meta_line("ext-session-4", working_directory),
                _task_complete_line("first turn done"),
            ]
        )
        adapter = CodexAdapter(db, provider)
        context = AgentContext(
            project_id=project_id,
            working_directory=working_directory,
            execution_provider="host",
            execution_target="1",
        )
        session = adapter.start(context, "start it")
        assert session.external_session_id == "ext-session-4"

        # Queue the send() turn's stdout without a task_complete line yet, so
        # the underlying process is still "running" when send() returns.
        provider.queue_turn([_agent_message_line("thinking...")])
        result = adapter.send(session.id, "keep going")
        assert result is None

        running_session = agent_models.get_agent_session(db, session.id)
        assert running_session.status == "RUNNING"
        process_id = running_session.metadata["process_id"]
        assert provider.process_status(process_id).status == "RUNNING"

        # A second send() while a turn is already in progress is rejected —
        # `codex exec` has no way to inject a prompt into an in-flight turn.
        with pytest.raises(ValueError):
            adapter.send(session.id, "another one")

        partial_events = adapter.stream(session.id)
        assert [e.event_type for e in partial_events] == [
            "PromptSubmitted",
            "AgentComplete",
            "PromptSubmitted",
            "AgentText",
        ]
        assert partial_events[-1].data == "thinking..."
        # The process has not exited yet, so nothing finalized this turn.
        assert agent_models.get_agent_session(db, session.id).status == "RUNNING"

        # More output arrives, then the process actually exits.
        provider.append_output(process_id, "stdout", _task_complete_line("second turn done"))
        provider.finish_process(process_id)

        final_events = adapter.stream(session.id, after_id=partial_events[-1].id)
        assert [e.event_type for e in final_events] == ["AgentComplete"]
        assert final_events[0].data == "second turn done"

        final_session = agent_models.get_agent_session(db, session.id)
        assert final_session.status == "COMPLETED"


def test_stream_reports_error_once_process_exits_without_task_complete(app):
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)

        provider = FakeExecutionProvider()
        provider.queue_turn(
            [
                _session_meta_line("ext-session-5", working_directory),
                _task_complete_line("first turn done"),
            ]
        )
        adapter = CodexAdapter(db, provider)
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

        # Nothing new has arrived, and the process hasn't exited yet.
        assert adapter.stream(session.id)[-1].event_type == "PromptSubmitted"

        provider.finish_process(process_id)

        events = adapter.stream(session.id)
        assert events[-1].event_type == "AgentError"
        assert events[-1].data == "boom"
        assert agent_models.get_agent_session(db, session.id).status == "FAILED"


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI not installed on this host")
def test_real_codex_binary_is_detected():
    adapter = CodexAdapter()

    assert adapter.available() is True
    assert adapter.version() != ""
