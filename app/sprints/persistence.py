from __future__ import annotations

import json
import sqlite3

from app.runs.models import now
from app.sprints.models import (
    PLANNING_PROFILES,
    PROPOSAL_STATES,
    SPRINT_STATUSES,
    SPRINT_TRANSITIONS,
    Approval,
    DocumentRef,
    InvalidSprintTransitionError,
    PlannedWorkItem,
    ReadinessCheck,
    Sprint,
)

_UPDATABLE = ("name", "goal", "description", "planning_profile", "start_date", "target_date")


def create_sprint(
    db: sqlite3.Connection,
    project_id: int,
    name: str,
    goal: str = "",
    description: str = "",
    planning_profile: str = "STANDARD_FEATURE",
    start_date: str | None = None,
    target_date: str | None = None,
) -> int:
    name = name.strip()
    if not name:
        raise ValueError("A sprint needs a name")
    if planning_profile not in PLANNING_PROFILES:
        raise ValueError(f"Unknown planning profile {planning_profile!r}")
    stamp = now()
    cur = db.execute(
        "INSERT INTO sprints (project_id, name, goal, description, planning_profile, "
        "start_date, target_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id, name, goal.strip(), description.strip(), planning_profile,
            start_date or None, target_date or None, stamp, stamp,
        ),
    )
    db.commit()
    return cur.lastrowid


def get_sprint(db: sqlite3.Connection, sprint_id: int) -> Sprint | None:
    row = db.execute("SELECT * FROM sprints WHERE id = ?", (sprint_id,)).fetchone()
    return Sprint(**dict(row)) if row else None


def list_sprints(db: sqlite3.Connection, project_id: int) -> list[Sprint]:
    rows = db.execute(
        "SELECT * FROM sprints WHERE project_id = ? ORDER BY id DESC", (project_id,)
    ).fetchall()
    return [Sprint(**dict(r)) for r in rows]


def update_sprint(db: sqlite3.Connection, sprint_id: int, **fields) -> None:
    unknown = set(fields) - set(_UPDATABLE)
    if unknown:
        raise ValueError(f"Cannot update {sorted(unknown)}")
    if fields.get("planning_profile", "STANDARD_FEATURE") not in PLANNING_PROFILES:
        raise ValueError(f"Unknown planning profile {fields['planning_profile']!r}")
    if "name" in fields and not fields["name"].strip():
        raise ValueError("A sprint needs a name")
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE sprints SET {assignments}, updated_at = ? WHERE id = ?",
        [*fields.values(), now(), sprint_id],
    )
    db.commit()


def set_status(db: sqlite3.Connection, sprint_id: int, new_status: str) -> None:
    if new_status not in SPRINT_STATUSES:
        raise ValueError(f"Unknown sprint status {new_status!r}")
    sprint = get_sprint(db, sprint_id)
    if sprint is None:
        raise LookupError(f"Sprint {sprint_id} not found")
    if new_status not in SPRINT_TRANSITIONS[sprint.status]:
        raise InvalidSprintTransitionError(f"{sprint.status} -> {new_status} is not allowed")
    db.execute(
        "UPDATE sprints SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, now(), sprint_id),
    )
    db.commit()


def set_planning_session(db: sqlite3.Connection, sprint_id: int, session_id: int | None) -> None:
    db.execute(
        "UPDATE sprints SET planning_session_id = ?, updated_at = ? WHERE id = ?",
        (session_id, now(), sprint_id),
    )
    db.commit()


# -- backlog membership -------------------------------------------------------


def add_backlog_item(db: sqlite3.Connection, sprint_id: int, item_id: int) -> None:
    db.execute(
        "UPDATE backlog_items SET sprint_id = ?, updated_at = ? WHERE id = ?",
        (sprint_id, now(), item_id),
    )
    db.commit()


def remove_backlog_item(db: sqlite3.Connection, item_id: int) -> None:
    db.execute(
        "UPDATE backlog_items SET sprint_id = NULL, updated_at = ? WHERE id = ?", (now(), item_id)
    )
    db.commit()


# -- document references ------------------------------------------------------


def add_document_ref(db: sqlite3.Connection, sprint_id: int, reference: str, note: str = "") -> int:
    reference = reference.strip()
    if not reference:
        raise ValueError("A document reference cannot be empty")
    cur = db.execute(
        "INSERT INTO sprint_document_refs (sprint_id, reference, note, created_at) "
        "VALUES (?, ?, ?, ?)",
        (sprint_id, reference, note.strip(), now()),
    )
    db.commit()
    return cur.lastrowid


def list_document_refs(db: sqlite3.Connection, sprint_id: int) -> list[DocumentRef]:
    rows = db.execute(
        "SELECT * FROM sprint_document_refs WHERE sprint_id = ? ORDER BY id", (sprint_id,)
    ).fetchall()
    return [DocumentRef(**dict(r)) for r in rows]


def remove_document_ref(db: sqlite3.Connection, sprint_id: int, ref_id: int) -> None:
    db.execute(
        "DELETE FROM sprint_document_refs WHERE id = ? AND sprint_id = ?", (ref_id, sprint_id)
    )
    db.commit()


# -- planned work items -------------------------------------------------------


def _hydrate_work_item(db: sqlite3.Connection, row: sqlite3.Row) -> PlannedWorkItem:
    data = dict(row)
    data["acceptance"] = json.loads(data["acceptance"])
    item = PlannedWorkItem(**data)
    item.depends_on = [
        r[0]
        for r in db.execute(
            "SELECT depends_on_id FROM planned_work_dependencies WHERE work_item_id = ? "
            "ORDER BY depends_on_id",
            (item.id,),
        )
    ]
    item.backlog_item_ids = [
        r[0]
        for r in db.execute(
            "SELECT backlog_item_id FROM planned_work_backlog_links WHERE work_item_id = ? "
            "ORDER BY backlog_item_id",
            (item.id,),
        )
    ]
    return item


def add_work_item(
    db: sqlite3.Connection,
    sprint_id: int,
    title: str,
    description: str = "",
    acceptance: list[str] | None = None,
    estimate: str = "",
    backlog_item_ids: list[int] | None = None,
) -> int:
    title = title.strip()
    if not title:
        raise ValueError("A planned task needs a title")
    stamp = now()
    seq = db.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM planned_work_items WHERE sprint_id = ?", (sprint_id,)
    ).fetchone()[0]
    cur = db.execute(
        "INSERT INTO planned_work_items (sprint_id, seq, title, description, acceptance, "
        "estimate, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sprint_id, seq, title, description.strip(),
            json.dumps([a.strip() for a in (acceptance or []) if a.strip()]),
            estimate.strip(), stamp, stamp,
        ),
    )
    work_id = cur.lastrowid
    for item_id in backlog_item_ids or []:
        db.execute(
            "INSERT OR IGNORE INTO planned_work_backlog_links (work_item_id, backlog_item_id) "
            "VALUES (?, ?)",
            (work_id, item_id),
        )
    db.commit()
    return work_id


def get_work_item(db: sqlite3.Connection, work_id: int) -> PlannedWorkItem | None:
    row = db.execute("SELECT * FROM planned_work_items WHERE id = ?", (work_id,)).fetchone()
    return _hydrate_work_item(db, row) if row else None


def list_work_items(db: sqlite3.Connection, sprint_id: int) -> list[PlannedWorkItem]:
    rows = db.execute(
        "SELECT * FROM planned_work_items WHERE sprint_id = ? ORDER BY seq", (sprint_id,)
    ).fetchall()
    return [_hydrate_work_item(db, r) for r in rows]


def update_work_item(
    db: sqlite3.Connection,
    work_id: int,
    title: str | None = None,
    description: str | None = None,
    acceptance: list[str] | None = None,
    estimate: str | None = None,
    status: str | None = None,
) -> None:
    """Edit a proposed task. Any content edit by a human moves a SUGGESTED task
    to REVIEWED so agent output stays distinguishable from reviewed work (§9)."""
    item = get_work_item(db, work_id)
    if item is None:
        raise LookupError(f"Planned task {work_id} not found")
    if status is not None and status not in PROPOSAL_STATES:
        raise ValueError(f"Unknown proposal state {status!r}")
    if title is not None and not title.strip():
        raise ValueError("A planned task needs a title")
    content_changed = any(v is not None for v in (title, description, acceptance, estimate))
    new_status = status or ("REVIEWED" if content_changed and item.status == "SUGGESTED" else item.status)
    new_acceptance = item.acceptance if acceptance is None else [a.strip() for a in acceptance if a.strip()]
    db.execute(
        "UPDATE planned_work_items SET title = ?, description = ?, acceptance = ?, estimate = ?, "
        "status = ?, updated_at = ? WHERE id = ?",
        (
            item.title if title is None else title.strip(),
            item.description if description is None else description.strip(),
            json.dumps(new_acceptance),
            item.estimate if estimate is None else estimate.strip(),
            new_status, now(), work_id,
        ),
    )
    db.commit()


def set_dependencies(db: sqlite3.Connection, work_id: int, depends_on: list[int]) -> None:
    """Replace a task's dependencies; rejects self-edges and cycles."""
    item = get_work_item(db, work_id)
    if item is None:
        raise LookupError(f"Planned task {work_id} not found")
    siblings = {i.id: i for i in list_work_items(db, item.sprint_id)}
    wanted = sorted(set(depends_on))
    if work_id in wanted:
        raise ValueError("A task cannot depend on itself")
    if any(d not in siblings for d in wanted):
        raise ValueError("Dependencies must be tasks in the same sprint")
    graph = {i.id: set(i.depends_on) for i in siblings.values()}
    graph[work_id] = set(wanted)
    if find_cycle(graph):
        raise ValueError("Those dependencies would create a cycle")
    db.execute("DELETE FROM planned_work_dependencies WHERE work_item_id = ?", (work_id,))
    db.executemany(
        "INSERT INTO planned_work_dependencies (work_item_id, depends_on_id) VALUES (?, ?)",
        [(work_id, d) for d in wanted],
    )
    db.commit()


def find_cycle(graph: dict[int, set[int]]) -> list[int]:
    """Return one cycle in `graph` (node -> dependencies) or []."""
    state: dict[int, int] = {}  # 1 = on stack, 2 = done
    stack: list[int] = []

    def visit(node: int) -> list[int]:
        state[node] = 1
        stack.append(node)
        for dep in sorted(graph.get(node, ())):
            if state.get(dep) == 1:
                return stack[stack.index(dep):] + [dep]
            if dep not in state:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return []

    for node in sorted(graph):
        if node not in state:
            found = visit(node)
            if found:
                return found
    return []


def delete_work_item(db: sqlite3.Connection, work_id: int) -> None:
    db.execute("DELETE FROM planned_work_items WHERE id = ?", (work_id,))
    db.commit()


def clear_suggested_items(db: sqlite3.Connection, sprint_id: int) -> None:
    """Drop un-reviewed agent output before a re-plan; human-touched tasks stay
    (docs/PHASE2_PLANNING.md §3: re-planning merges, never discards edits)."""
    db.execute(
        "DELETE FROM planned_work_items WHERE sprint_id = ? AND status = 'SUGGESTED'", (sprint_id,)
    )
    db.commit()


# -- readiness and approvals ----------------------------------------------------


def replace_readiness(
    db: sqlite3.Connection, sprint_id: int, results: list[tuple[str, str, str]]
) -> None:
    db.execute("DELETE FROM sprint_readiness_checks WHERE sprint_id = ?", (sprint_id,))
    stamp = now()
    db.executemany(
        "INSERT INTO sprint_readiness_checks (sprint_id, check_name, status, details, checked_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(sprint_id, name, status, details, stamp) for name, status, details in results],
    )
    db.commit()


def list_readiness(db: sqlite3.Connection, sprint_id: int) -> list[ReadinessCheck]:
    rows = db.execute(
        "SELECT * FROM sprint_readiness_checks WHERE sprint_id = ? ORDER BY id", (sprint_id,)
    ).fetchall()
    return [ReadinessCheck(**dict(r)) for r in rows]


def record_approval(
    db: sqlite3.Connection, sprint_id: int, decision: str, approved_by: str, comment: str = ""
) -> int:
    stamp = now()
    cur = db.execute(
        "INSERT INTO sprint_approvals (sprint_id, decision, approved_by, comment, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (sprint_id, decision, approved_by, comment.strip(), stamp),
    )
    if decision == "APPROVED":
        db.execute(
            "UPDATE sprints SET approved_at = ?, approved_by = ?, updated_at = ? WHERE id = ?",
            (stamp, approved_by, stamp, sprint_id),
        )
    else:
        db.execute(
            "UPDATE sprints SET approved_at = NULL, approved_by = NULL, updated_at = ? WHERE id = ?",
            (stamp, sprint_id),
        )
    db.commit()
    return cur.lastrowid


def list_approvals(db: sqlite3.Connection, sprint_id: int) -> list[Approval]:
    rows = db.execute(
        "SELECT * FROM sprint_approvals WHERE sprint_id = ? ORDER BY id", (sprint_id,)
    ).fetchall()
    return [Approval(**dict(r)) for r in rows]
