"""Sprint execution queue (docs/SPRINT_PLANNING_AND_BACKLOG.md §27-28).

Approved planned tasks carry a canonical `task_state`. A human releases them
(READY -> RELEASED); the queue then hands the next *eligible* RELEASED task to
Ralph, one at a time per project. Task state follows the Ralph run it started
(`sync_from_run`), so the database, not memory, is the source of truth.
"""
from __future__ import annotations

import sqlite3

from app.pipelines import persistence as pipelines
from app.projects import lock
from app.projects import models as project_models
from app.ralph import models as ralph
from app.runs.models import now
from app.sprints import persistence as sprints
from app.sprints.models import TASK_STATES, TASK_TRANSITIONS, InvalidTaskTransitionError, PlannedWorkItem

# Ralph run status -> the Task state it implies.
RUN_TO_TASK = {
    "CREATED": "IN_PROGRESS", "RUNNING": "IN_PROGRESS", "VERIFYING": "IN_PROGRESS",
    "PAUSED": "IN_PROGRESS",
    "WAITING_FOR_HUMAN": "BLOCKED", "BLOCKED": "BLOCKED",
    "COMPLETED": "COMPLETE", "FAILED": "FAILED", "TIMED_OUT": "FAILED",
    "CANCELLED": "RELEASED",
}
PROGRESS_LABELS = (
    ("Complete", ("COMPLETE",)), ("Running", ("IN_PROGRESS",)), ("Released", ("RELEASED",)),
    ("Ready", ("READY",)), ("Blocked", ("BLOCKED", "FAILED")), ("Planning", ("DRAFT", "READY_FOR_REVIEW")),
)


class QueueError(ValueError):
    """The queue cannot do what was asked (nothing eligible, project busy, ...)."""


def set_state(db: sqlite3.Connection, work_id: int, new_state: str) -> None:
    """Move a Task to `new_state`, enforcing TASK_TRANSITIONS. Same-state is a no-op."""
    if new_state not in TASK_STATES:
        raise InvalidTaskTransitionError(f"Unknown task state {new_state!r}")
    row = db.execute("SELECT task_state FROM planned_work_items WHERE id = ?", (work_id,)).fetchone()
    if row is None:
        raise LookupError(f"Planned task {work_id} not found")
    current = row["task_state"]
    if current == new_state:
        return
    if new_state not in TASK_TRANSITIONS[current]:
        raise InvalidTaskTransitionError(
            f"A {current.lower().replace('_', ' ')} task cannot become {new_state.lower().replace('_', ' ')}"
        )
    released = ", released_at = ?" if new_state == "RELEASED" else ""
    params = [new_state, now(), *([now()] if released else []), work_id]
    db.execute(
        f"UPDATE planned_work_items SET task_state = ?, updated_at = ?{released} WHERE id = ?", params
    )
    db.commit()


def _live(db, sprint_id: int) -> list[PlannedWorkItem]:
    return [w for w in sprints.list_work_items(db, sprint_id) if w.status != "REJECTED"]


def release_task(db: sqlite3.Connection, work_id: int) -> None:
    set_state(db, work_id, "RELEASED")


def release_sprint(db: sqlite3.Connection, sprint_id: int) -> int:
    """Release every READY task and start the Sprint (READY -> EXECUTING)."""
    sprint = sprints.get_sprint(db, sprint_id)
    if sprint is None:
        raise LookupError(f"Sprint {sprint_id} not found")
    if sprint.status not in ("READY", "EXECUTING"):
        raise QueueError(f"Only an approved sprint can be released (it is {sprint.status.lower()})")
    ready = [w for w in _live(db, sprint_id) if w.task_state == "READY"]
    if not ready:
        raise QueueError("No ready tasks to release")
    for work in ready:
        release_task(db, work.id)
    if sprint.status == "READY":
        sprints.set_status(db, sprint_id, "EXECUTING")
    return len(ready)


def project_busy(db: sqlite3.Connection, project_id: int) -> str | None:
    """Why the Project cannot take another run right now (None = free).

    The execution lock covers work that is running; paused or waiting Ralph
    runs release it but still own the working tree, so they count as busy too."""
    held = lock.current(db, project_id)
    if held is not None:
        return f"locked by {held.owner}"
    row = db.execute(
        "SELECT id, status FROM ralph_runs WHERE project_id = ? AND status IN "
        "('CREATED','RUNNING','VERIFYING','PAUSED','WAITING_FOR_HUMAN') LIMIT 1",
        (project_id,),
    ).fetchone()
    if row:
        return f"Ralph run #{row['id']} is {row['status'].lower().replace('_', ' ')}"
    return None


def eligible_task(db: sqlite3.Connection, sprint_id: int) -> PlannedWorkItem | None:
    """First RELEASED task, in plan order, whose dependencies are all COMPLETE.
    A blocked/failed task only holds back its own dependants (§28)."""
    sprint = sprints.get_sprint(db, sprint_id)
    if sprint is None or sprint.status != "EXECUTING":
        return None
    work = _live(db, sprint_id)
    state = {w.id: w.task_state for w in work}
    for w in work:
        if w.task_state == "RELEASED" and all(state.get(d) == "COMPLETE" for d in w.depends_on):
            return w
    return None


def create_run_for_task(db: sqlite3.Connection, work: PlannedWorkItem, repository_id: int, pipeline: str) -> int:
    """RalphRunFactory: a Ralph run carrying the task's text, criteria and pipeline."""
    sprint = sprints.get_sprint(db, work.sprint_id)
    text = work.title + (f"\n\n{work.description}" if work.description else "")
    return ralph.create_run(
        db, sprint.project_id, repository_id, work.title, text, pipeline,
        acceptance=work.acceptance, work_item_id=work.id, sprint_id=sprint.id,
    )


def default_pipeline(db: sqlite3.Connection, project_id: int, work: PlannedWorkItem | None = None) -> str:
    if work is not None and work.verification_pipeline:
        return work.verification_pipeline
    enabled = [p for p in pipelines.list_pipelines(db, project_id) if p.enabled]
    return enabled[0].name if enabled else ""


def promote_next(db: sqlite3.Connection, sprint_id: int, repository_id: int | None = None, pipeline: str = "") -> tuple[int, int]:
    """Create the Ralph run for the next eligible task and mark it IN_PROGRESS.
    Returns (work_item_id, run_id); the caller starts the run."""
    sprint = sprints.get_sprint(db, sprint_id)
    work = eligible_task(db, sprint_id)
    if work is None:
        raise QueueError("No eligible task: nothing is released, or the rest are waiting on dependencies")
    busy = project_busy(db, sprint.project_id)
    if busy:
        raise QueueError(f"Project is busy: {busy}")
    if repository_id is None:
        project = project_models.get_project(db, sprint.project_id)
        repo = next((r for r in project.repositories if r.is_primary), None) or (
            project.repositories[0] if project.repositories else None
        )
        if repo is None:
            raise QueueError("The project has no repository to run in")
        repository_id = repo.id
    pipeline = pipeline or default_pipeline(db, sprint.project_id, work)
    if not pipeline:
        raise QueueError("Choose a verification pipeline: the project has none enabled")
    run_id = create_run_for_task(db, work, repository_id, pipeline)
    set_state(db, work.id, "IN_PROGRESS")
    return work.id, run_id


def sync_from_run(db: sqlite3.Connection, run_id: int) -> None:
    """Reflect a Ralph run's status on its Task and, when every task is done,
    move the Sprint to VERIFYING."""
    run = ralph.get_run(db, run_id)
    if run is None or run.work_item_id is None:
        return
    target = RUN_TO_TASK.get(run.status)
    row = db.execute("SELECT task_state FROM planned_work_items WHERE id = ?", (run.work_item_id,)).fetchone()
    if target and row and row["task_state"] not in ("COMPLETE", "CANCELLED"):
        try:
            set_state(db, run.work_item_id, target)
        except InvalidTaskTransitionError:
            pass  # e.g. a FAILED task re-run: the queue re-releases it first
    if run.sprint_id:
        _maybe_verify(db, run.sprint_id)


def _maybe_verify(db: sqlite3.Connection, sprint_id: int) -> None:
    sprint = sprints.get_sprint(db, sprint_id)
    work = [w for w in _live(db, sprint_id) if w.task_state != "CANCELLED"]
    if sprint and sprint.status == "EXECUTING" and work and all(w.task_state == "COMPLETE" for w in work):
        sprints.set_status(db, sprint_id, "VERIFYING")


def progress(db: sqlite3.Connection, sprint_id: int) -> list[tuple[str, int]]:
    """§29: counts derived from actual Task state."""
    work = _live(db, sprint_id)
    return [(label, sum(1 for w in work if w.task_state in states)) for label, states in PROGRESS_LABELS]


def describe(db: sqlite3.Connection, sprint_id: int) -> list[dict]:
    """Queue rows in plan order with why each task is (not) eligible."""
    work = _live(db, sprint_id)
    state = {w.id: w.task_state for w in work}
    nxt = eligible_task(db, sprint_id)
    rows = []
    for w in work:
        waiting = [d for d in w.depends_on if state.get(d) != "COMPLETE"]
        rows.append({
            "work": w, "waiting_on": waiting, "next": nxt is not None and nxt.id == w.id,
            "run_id": (db.execute(
                "SELECT id FROM ralph_runs WHERE work_item_id = ? ORDER BY id DESC LIMIT 1", (w.id,)
            ).fetchone() or [None])[0],
        })
    return rows
