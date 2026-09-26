"""Sprint readiness (docs/SPRINT_PLANNING_AND_BACKLOG.md §25): explicit
conditions, not a score. FAIL blocks approval; WARN is advisory."""
from __future__ import annotations

import sqlite3

from app.backlog import persistence as backlog
from app.sprints import persistence as sprints

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


def evaluate(db: sqlite3.Connection, sprint_id: int) -> list[tuple[str, str, str]]:
    sprint = sprints.get_sprint(db, sprint_id)
    items = backlog.list_items(db, sprint.project_id, sprint_id=sprint_id, limit=1000)
    work = [w for w in sprints.list_work_items(db, sprint_id) if w.status != "REJECTED"]
    results: list[tuple[str, str, str]] = []

    def add(name: str, ok: bool, detail_fail: str, detail_ok: str = "", warn: bool = False):
        results.append((name, PASS if ok else (WARN if warn else FAIL), detail_ok if ok else detail_fail))

    add("goal_defined", bool(sprint.goal.strip()), "The sprint has no goal", "Goal set")
    add("items_selected", bool(items), "No backlog items are selected", f"{len(items)} item(s) selected")
    add("tasks_proposed", bool(work), "No tasks have been proposed", f"{len(work)} task(s) planned")

    linked = {i for w in work for i in w.backlog_item_ids}
    uncovered = [i.id for i in items if i.id not in linked]
    add(
        "backlog_covered",
        not uncovered,
        "Not covered by any task: " + ", ".join(f"#{i}" for i in uncovered),
        "Every selected item maps to a task",
    )

    no_criteria = [w.title for w in work if not w.acceptance]
    add(
        "acceptance_defined",
        not no_criteria,
        "No acceptance criteria: " + "; ".join(no_criteria),
        "All tasks have acceptance criteria",
    )

    unreviewed = [w.title for w in work if w.status == "SUGGESTED"]
    add(
        "acceptance_reviewed",
        not unreviewed,
        "Still agent-suggested (review or edit each): " + "; ".join(unreviewed),
        "All tasks reviewed by a person",
    )

    active_ids = {w.id for w in work}
    dangling = [w.title for w in work if any(d not in active_ids for d in w.depends_on)]
    cycle = sprints.find_cycle({w.id: set(w.depends_on) for w in work})
    add(
        "dependencies_valid",
        not dangling and not cycle,
        "Dependencies point at rejected/missing tasks: " + "; ".join(dangling)
        if dangling
        else "Dependency cycle detected",
        "Dependency graph is acyclic",
    )

    add(
        "documentation_referenced",
        bool(sprints.list_document_refs(db, sprint_id)),
        "No documentation is referenced (advisory)",
        "Documentation referenced",
        warn=True,
    )
    return results


def blocking(results: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    return [r for r in results if r[1] == FAIL]
