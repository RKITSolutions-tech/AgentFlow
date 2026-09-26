"""Sprint planning workflow: keeps Sprint and Backlog statuses in step."""
from __future__ import annotations

import sqlite3

from app.backlog import persistence as backlog
from app.backlog.models import InvalidTransitionError
from app.sprints import persistence as sprints
from app.sprints import readiness
from app.sprints.models import NotReadyError, Sprint
from app.sprints.planning_agent import PlanningAgent

EDITABLE_MEMBERSHIP = ("DRAFT", "PLANNING", "REVIEW")


def _sprint(db: sqlite3.Connection, sprint_id: int) -> Sprint:
    sprint = sprints.get_sprint(db, sprint_id)
    if sprint is None:
        raise LookupError(f"Sprint {sprint_id} not found")
    return sprint


def _move_items(db, sprint: Sprint, from_status: str, to_status: str, notes: str) -> None:
    for item in backlog.list_items(db, sprint.project_id, sprint_id=sprint.id, limit=1000):
        if item.status == from_status:
            backlog.transition(db, item.id, to_status, notes=notes, changed_by="sprint")


def select_items(db: sqlite3.Connection, sprint_id: int, item_ids: list[int]) -> list[int]:
    """Add backlog items to a Sprint (moving them to SELECTED). Returns the ids added."""
    sprint = _sprint(db, sprint_id)
    if sprint.status not in EDITABLE_MEMBERSHIP:
        raise ValueError(f"Items cannot be added while the sprint is {sprint.status.lower()}")
    added = []
    for item_id in item_ids:
        item = backlog.get_item(db, item_id)
        if item is None or item.project_id != sprint.project_id:
            raise ValueError(f"Backlog item {item_id} is not in this project")
        if item.sprint_id == sprint_id:
            continue
        if item.sprint_id is not None:
            raise ValueError(f"Backlog item {item_id} already belongs to another sprint")
        if item.status != "SELECTED":
            try:
                backlog.transition(
                    db, item_id, "SELECTED", notes=f"added to sprint {sprint_id}", changed_by="sprint"
                )
            except InvalidTransitionError as exc:
                raise ValueError(f"Backlog item {item_id}: {exc}") from exc
        sprints.add_backlog_item(db, sprint_id, item_id)
        added.append(item_id)
    return added


def deselect_item(db: sqlite3.Connection, sprint_id: int, item_id: int) -> None:
    sprint = _sprint(db, sprint_id)
    item = backlog.get_item(db, item_id)
    if item is None or item.sprint_id != sprint_id:
        raise ValueError("That item is not in this sprint")
    if item.status != "SELECTED":
        raise ValueError("Only items that have not entered planning can be removed")
    if any(item_id in w.backlog_item_ids for w in sprints.list_work_items(db, sprint_id)):
        raise ValueError("Remove the planned tasks that cover this item first")
    backlog.transition(db, item_id, "TRIAGED", notes="removed from sprint", changed_by="sprint")
    sprints.remove_backlog_item(db, item_id)


def start_planning(db, sprint_id: int, agent: PlanningAgent, context, replan: bool = False) -> int:
    """Launch the planning agent. Returns the agent session id to poll."""
    sprint = _sprint(db, sprint_id)
    items = backlog.list_items(db, sprint.project_id, sprint_id=sprint_id, limit=1000)
    if not sprint.goal.strip():
        raise ValueError("Set a sprint goal before planning")
    if not items:
        raise ValueError("Select at least one backlog item before planning")
    if sprint.status not in ("DRAFT", "REVIEW") or (sprint.status == "REVIEW" and not replan):
        raise ValueError(f"Planning cannot start while the sprint is {sprint.status.lower()}")
    session_id = agent.start(context, sprint, items, sprints.list_document_refs(db, sprint_id))
    sprints.set_planning_session(db, sprint_id, session_id)
    sprints.set_status(db, sprint_id, "PLANNING")
    _move_items(db, sprint, "SELECTED", "PLANNING", "planning started")
    _move_items(db, sprint, "PLANNED", "PLANNING", "re-planning")
    return session_id


def ingest_proposal(db, sprint_id: int, agent: PlanningAgent) -> str:
    """Poll the planning session; on completion store the proposal and enter
    REVIEW. Returns RUNNING, COMPLETE or FAILED; a failure returns the sprint
    to DRAFT so it can be retried."""
    sprint = _sprint(db, sprint_id)
    if sprint.status != "PLANNING" or sprint.planning_session_id is None:
        raise ValueError("The sprint is not being planned")
    ids = {i.id for i in backlog.list_items(db, sprint.project_id, sprint_id=sprint_id, limit=1000)}
    outcome = agent.collect(sprint.planning_session_id, ids)
    if outcome.state == "RUNNING":
        return "RUNNING"
    if outcome.state == "FAILED":
        sprints.set_status(db, sprint_id, "DRAFT")
        _move_items(db, sprint, "PLANNING", "SELECTED", f"planning failed: {outcome.error}"[:200])
        raise PlanningFailed(outcome.error)

    sprints.clear_suggested_items(db, sprint_id)
    created: dict[str, int] = {}
    for task in outcome.tasks:
        created[task.ref] = sprints.add_work_item(
            db, sprint_id, task.title, task.description, task.acceptance, task.estimate,
            task.backlog_items,
        )
    for task in outcome.tasks:
        deps = [created[r] for r in task.depends_on if r in created and r != task.ref]
        try:
            sprints.set_dependencies(db, created[task.ref], deps)
        except ValueError:
            # A cyclic edge from the agent is dropped rather than failing the
            # whole proposal; readiness review shows any resulting gap.
            continue
    sprints.set_status(db, sprint_id, "REVIEW")
    _move_items(db, sprint, "PLANNING", "PLANNED", "proposal ready for review")
    check(db, sprint_id)
    return "COMPLETE"


class PlanningFailed(RuntimeError):
    pass


def check(db: sqlite3.Connection, sprint_id: int) -> list[tuple[str, str, str]]:
    results = readiness.evaluate(db, sprint_id)
    sprints.replace_readiness(db, sprint_id, results)
    return results


def approve(db: sqlite3.Connection, sprint_id: int, approved_by: str, comment: str = "") -> None:
    sprint = _sprint(db, sprint_id)
    approved_by = approved_by.strip()
    if not approved_by:
        raise ValueError("Enter your name to sign the approval")
    if sprint.status != "REVIEW":
        raise ValueError(f"Only a sprint in review can be approved (it is {sprint.status.lower()})")
    failing = readiness.blocking(check(db, sprint_id))
    if failing:
        raise NotReadyError("Not ready: " + "; ".join(f"{n} ({d})" for n, _, d in failing))
    for work in sprints.list_work_items(db, sprint_id):
        if work.status != "REJECTED":
            sprints.update_work_item(db, work.id, status="APPROVED")
    sprints.record_approval(db, sprint_id, "APPROVED", approved_by, comment)
    from app.acceptance import service as acceptance

    acceptance.sync_from_work_items(db, sprint_id, approved_by)
    sprints.set_status(db, sprint_id, "READY")
    _move_items(db, sprint, "PLANNED", "READY", f"sprint approved by {approved_by}")


def revoke(db: sqlite3.Connection, sprint_id: int, revoked_by: str, comment: str = "") -> None:
    sprint = _sprint(db, sprint_id)
    if not revoked_by.strip():
        raise ValueError("Enter your name to revoke the approval")
    if sprint.status != "READY":
        raise ValueError("Only a ready sprint can have its approval revoked")
    sprints.record_approval(db, sprint_id, "REVOKED", revoked_by.strip(), comment)
    sprints.set_status(db, sprint_id, "REVIEW")
    for work in sprints.list_work_items(db, sprint_id):
        if work.status == "APPROVED":
            sprints.update_work_item(db, work.id, status="REVIEWED")
    _move_items(db, sprint, "READY", "PLANNED", f"approval revoked by {revoked_by.strip()}")
    check(db, sprint_id)
