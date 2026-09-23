from __future__ import annotations

from app.execution.base import ExecutionProvider


def capture_git_diff(provider: ExecutionProvider, context_id: int) -> str:
    """Run `git diff` in the given execution context and return its stdout.

    Requested through the ExecutionProvider abstraction rather than a direct
    subprocess call, per docs/AGENT_ADAPTER.md section 13.
    """
    process = provider.execute(["git", "diff"], {"context_id": context_id})
    events = provider.stream_output(process.id)
    lines = [e.data for e in events if e.event_type == "ProcessOutput" and e.stream == "stdout"]
    return "\n".join(lines)


def run_configured_test(
    provider: ExecutionProvider, context_id: int, command: list[str]
) -> tuple[str, int | None, str]:
    """Run a configured test command and return (status, exit_code, output).

    stdout and stderr are drained on separate threads (app/execution/host.py),
    so their relative interleaving is not meaningful; each line is labelled
    with its originating stream rather than merged into one ambiguous blob.
    """
    process = provider.execute(command, {"context_id": context_id})
    events = provider.stream_output(process.id)
    lines = [
        f"[{e.stream}] {e.data}" for e in events if e.event_type == "ProcessOutput"
    ]
    output = "\n".join(lines)
    status = "PASSED" if process.exit_code == 0 else "FAILED"
    return status, process.exit_code, output
