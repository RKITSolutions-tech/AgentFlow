from __future__ import annotations

import shutil
import subprocess

import pytest

from app.agents.codex import CodexAdapter


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


def test_unimplemented_methods_raise_not_implemented():
    adapter = CodexAdapter()

    with pytest.raises(NotImplementedError):
        adapter.discover_sessions(project_id=1)
    with pytest.raises(NotImplementedError):
        adapter.start(context=None, prompt="hi")
    with pytest.raises(NotImplementedError):
        adapter.send(session_id=1, content="hi")
    with pytest.raises(NotImplementedError):
        adapter.stop(session_id=1)
    with pytest.raises(NotImplementedError):
        adapter.status(session_id=1)
    with pytest.raises(NotImplementedError):
        adapter.stream(session_id=1)


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI not installed on this host")
def test_real_codex_binary_is_detected():
    adapter = CodexAdapter()

    assert adapter.available() is True
    assert adapter.version() != ""
