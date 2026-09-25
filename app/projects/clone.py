"""Clone a repository from a URL into an allowed root and register it as a project.

The clone runs as a host process through the execution provider, so its
output is persisted as process events; the page polls ``poll_job`` and turns
git's ``Receiving objects: 45%`` lines into a progress bar. There is no
background thread of our own: a finished clone is turned into a project the
first time a poll notices it (guarded so concurrent polls create one).
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from dataclasses import dataclass

from app.execution.base import ExecutionProvider
from app.projects import models
from app.security import validate_repository_path

CLONE_TIMEOUT_SECONDS = 1800.0
_NON_INTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
}
_URL_PATTERNS = (
    re.compile(r"^https?://[^\s/@]+(:\d+)?(/[^\s]*)?$", re.I),
    re.compile(r"^https?://[^\s/@]+@[^\s/]+(/[^\s]*)?$", re.I),
    re.compile(r"^ssh://[^\s]+$", re.I),
    re.compile(r"^git@[A-Za-z0-9.-]+:[^\s]+$"),
)
_FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_PROGRESS = re.compile(r"(\d{1,3})%")


class CloneError(ValueError):
    """The clone request was rejected; the message is user-facing."""


@dataclass
class CloneJob:
    id: int
    url: str
    destination: str
    project_name: str
    description: str
    context_id: int | None
    process_id: int | None
    status: str
    message: str
    project_id: int | None


def validate_url(url: str) -> str:
    """Accept https/http/ssh/scp-style URLs only.

    Rejects anything else — notably ``file://``, ``ext::`` transports and a
    leading ``-`` that git would read as an option.
    """
    url = (url or "").strip()
    if not url or url.startswith("-") or not any(p.match(url) for p in _URL_PATTERNS):
        raise CloneError("Enter an https://, ssh:// or git@host:path repository URL.")
    return url


def folder_name_from_url(url: str) -> str:
    tail = re.split(r"[/:]", url.strip().rstrip("/"))[-1]
    tail = re.sub(r"\.git$", "", tail)
    tail = re.sub(r"[^A-Za-z0-9._-]", "-", tail).strip(".-")
    return tail or "repository"


def _row_to_job(row: sqlite3.Row) -> CloneJob:
    return CloneJob(**{k: row[k] for k in CloneJob.__dataclass_fields__})


def get_job(db: sqlite3.Connection, job_id: int) -> CloneJob | None:
    row = db.execute("SELECT * FROM clone_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def start_clone(
    db: sqlite3.Connection,
    provider: ExecutionProvider,
    allowed_roots: tuple[str, ...],
    url: str,
    parent: str,
    folder: str,
    project_name: str,
    description: str = "",
) -> CloneJob:
    url = validate_url(url)
    folder = (folder or "").strip() or folder_name_from_url(url)
    if not _FOLDER_PATTERN.match(folder) or folder.endswith((".", ".lock")) or ".." in folder:
        raise CloneError("Folder names may use letters, digits, '.', '_' and '-'.")
    project_name = (project_name or "").strip() or folder

    try:
        parent_path = validate_repository_path(parent, allowed_roots)
    except ValueError:
        raise CloneError("The clone location is outside the allowed project roots.") from None
    if not os.path.isdir(parent_path):
        raise CloneError("The clone location does not exist.")
    destination = os.path.join(parent_path, folder)
    if os.path.lexists(destination):
        raise CloneError(f"{destination} already exists.")

    context = provider.create_context({"working_directory": parent_path})
    cur = db.execute(
        "INSERT INTO clone_jobs (url, destination, project_name, description, context_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (url, destination, project_name, description, context.id),
    )
    db.commit()
    job_id = cur.lastrowid
    try:
        process = provider.start_process(
            ["git", "-c", "protocol.ext.allow=never", "clone", "--progress", "--", url, folder],
            options={
                "context_id": context.id,
                "timeout": CLONE_TIMEOUT_SECONDS,
                "environment": _NON_INTERACTIVE_ENV,
            },
        )
    except OSError as exc:
        _finish(db, job_id, "FAILED", f"Could not run git: {exc}")
        provider.destroy_context(context.id)
        return get_job(db, job_id)
    db.execute("UPDATE clone_jobs SET process_id = ? WHERE id = ?", (process.id, job_id))
    db.commit()
    return get_job(db, job_id)


def _finish(db: sqlite3.Connection, job_id: int, status: str, message: str = "") -> bool:
    """Move a RUNNING job to a final state; False if someone else already did."""
    cur = db.execute(
        "UPDATE clone_jobs SET status = ?, message = ? WHERE id = ? AND status = 'RUNNING'",
        (status, message, job_id),
    )
    db.commit()
    return cur.rowcount == 1


def poll_job(
    db: sqlite3.Connection,
    provider: ExecutionProvider,
    allowed_roots: tuple[str, ...],
    job_id: int,
) -> tuple[CloneJob, int | None]:
    """Return the job (finalising it if the clone ended) and progress percent."""
    job = get_job(db, job_id)
    if job is None or job.process_id is None:
        return job, None
    process = provider.process_status(job.process_id)
    events = provider.stream_output(job.process_id)
    stderr = [e.data for e in events if e.event_type == "ProcessOutput" and e.stream == "stderr"]
    percent = None
    for line in reversed(stderr):
        match = _PROGRESS.search(line)
        if match:
            percent = min(100, int(match.group(1)))
            break

    if job.status == "RUNNING" and process is not None and process.status in (
        "COMPLETED", "FAILED", "STOPPED", "TIMED_OUT", "LOST"
    ):
        if process.exit_code == 0:
            if _finish(db, job_id, "DONE"):
                try:
                    project_id = models.create_project(db, job.project_name, job.description)
                    models.add_repository(
                        db, project_id, os.path.basename(job.destination), job.destination,
                        allowed_roots, is_primary=True,
                    )
                except ValueError as exc:
                    db.execute(
                        "UPDATE clone_jobs SET status = 'FAILED', message = ? WHERE id = ?",
                        (str(exc), job_id),
                    )
                    db.commit()
                else:
                    db.execute(
                        "UPDATE clone_jobs SET project_id = ? WHERE id = ?", (project_id, job_id)
                    )
                    db.commit()
        else:
            reason = next((l.strip() for l in reversed(stderr) if l.strip()), "git clone failed")
            if _finish(db, job_id, "FAILED", reason):
                _remove_partial(job.destination, allowed_roots)
        provider.destroy_context(job.context_id)
        job = get_job(db, job_id)
    return job, 100 if job.status == "DONE" else percent


def cancel_job(
    db: sqlite3.Connection,
    provider: ExecutionProvider,
    allowed_roots: tuple[str, ...],
    job_id: int,
) -> CloneJob | None:
    job = get_job(db, job_id)
    if job is None:
        return None
    if job.status == "RUNNING" and _finish(db, job_id, "CANCELLED", "Cancelled."):
        if job.process_id is not None:
            provider.stop_process(job.process_id)
        if job.context_id is not None:
            provider.destroy_context(job.context_id)
        _remove_partial(job.destination, allowed_roots)
    return get_job(db, job_id)


def _remove_partial(destination: str, allowed_roots: tuple[str, ...]) -> None:
    """Delete what a failed/cancelled clone left behind (git usually cleans up itself).

    Re-validated against the allowed roots and never the root itself, since
    this is a recursive delete.
    """
    try:
        resolved = validate_repository_path(destination, allowed_roots)
    except ValueError:
        return
    if resolved in {os.path.realpath(r) for r in allowed_roots}:
        return
    if os.path.isdir(destination) and not os.path.islink(destination):
        shutil.rmtree(destination, ignore_errors=True)
