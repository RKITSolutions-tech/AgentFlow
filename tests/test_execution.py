from __future__ import annotations

import os
import time

import pytest

from app.execution.host import HostExecutionProvider
from app.security import PathNotAllowedError


@pytest.fixture
def provider(app):
    return HostExecutionProvider(
        app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"]
    )


def test_successful_command_streams_output_and_completes(app, provider):
    context = provider.create_context({"working_directory": app.config["allowed_root"]})

    process = provider.execute(["echo", "hello"], {"context_id": context.id})

    assert process.status == "COMPLETED"
    assert process.exit_code == 0

    events = provider.stream_output(process.id)
    event_types = [e.event_type for e in events]
    assert "ProcessStarted" in event_types
    assert "ProcessCompleted" in event_types
    assert any(
        e.event_type == "ProcessOutput" and e.stream == "stdout" and e.data == "hello"
        for e in events
    )


def test_failing_command_completes_with_nonzero_exit_code(app, provider):
    context = provider.create_context({"working_directory": app.config["allowed_root"]})

    process = provider.execute(["sh", "-c", "exit 1"], {"context_id": context.id})

    assert process.status == "COMPLETED"
    assert process.exit_code == 1


def test_cancel_terminates_the_process_group(app, provider):
    context = provider.create_context({"working_directory": app.config["allowed_root"]})

    process = provider.start_process(
        ["sh", "-c", "sleep 5"], {"context_id": context.id}
    )
    pid = process.external_process_id
    assert pid is not None

    provider.stop_process(process.id)
    final = provider.wait(process.id, poll_interval=0.02)

    assert final.status == "STOPPED"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_execution_timeout_terminates_the_process(app, provider):
    context = provider.create_context({"working_directory": app.config["allowed_root"]})

    start = time.monotonic()
    process = provider.start_process(
        ["sh", "-c", "sleep 5"], {"context_id": context.id, "timeout": 0.5}
    )
    pid = process.external_process_id

    final = provider.wait(process.id, poll_interval=0.02)
    elapsed = time.monotonic() - start

    assert final.status == "TIMED_OUT"
    assert elapsed < 2
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_create_context_rejects_path_outside_allowed_roots(provider, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(PathNotAllowedError):
        provider.create_context({"working_directory": str(outside)})


def test_stream_output_resumes_from_recorded_position(app, provider):
    context = provider.create_context({"working_directory": app.config["allowed_root"]})

    process = provider.execute(
        ["sh", "-c", "echo one; echo two; echo three"], {"context_id": context.id}
    )

    all_events = provider.stream_output(process.id)
    assert len(all_events) >= 3

    midpoint_id = all_events[len(all_events) // 2].id
    resumed = provider.stream_output(process.id, after_id=midpoint_id)

    assert [e.id for e in resumed] == [e.id for e in all_events if e.id > midpoint_id]
    assert all(e.id > midpoint_id for e in resumed)
