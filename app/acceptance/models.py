from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from app.runs.models import now

STATUSES = ("DRAFT", "APPROVED", "VERIFIED", "FAILED", "WAIVED")
ORIGINS = ("MANUAL", "PLANNING", "TEMPLATE", "AGENT")
EVIDENCE_TYPES = ("ARTIFACT", "TEST_RESULT", "MANUAL")
EVIDENCE_STATES = ("SUGGESTED", "LINKED", "DISMISSED")
# Allowed moves. A failed or verified criterion can be re-opened for another look.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("APPROVED", "WAIVED"),
    "APPROVED": ("VERIFIED", "FAILED", "WAIVED"),
    "VERIFIED": ("APPROVED",),
    "FAILED": ("APPROVED", "WAIVED"),
    "WAIVED": ("APPROVED",),
}
SATISFIED = ("VERIFIED", "WAIVED")


class AcceptanceError(ValueError):
    """A criterion change is not permitted."""


@dataclass
class Criterion:
    id: int
    project_id: int
    work_item_id: int | None
    ralph_run_id: int | None
    title: str
    description: str
    status: str
    required: bool
    origin: str
    iteration: int | None
    created_by: str
    approved_by: str | None
    approved_at: str | None
    verified_by: str | None
    verified_at: str | None
    waived_reason: str
    template_key: str
    hints: dict
    created_at: str
    updated_at: str


@dataclass
class Evidence:
    id: int
    criterion_id: int
    evidence_type: str
    reference_id: int | None
    note: str
    state: str
    recorded_by: str
    created_at: str


def _criterion(row: sqlite3.Row) -> Criterion:
    d = dict(row)
    d["required"] = bool(d["required"])
    d["hints"] = json.loads(d["hints"])
    return Criterion(**d)


def create(
    db: sqlite3.Connection,
    project_id: int,
    title: str,
    description: str = "",
    work_item_id: int | None = None,
    ralph_run_id: int | None = None,
    required: bool = True,
    origin: str = "MANUAL",
    iteration: int | None = None,
    created_by: str = "",
    template_key: str = "",
    hints: dict | None = None,
    status: str = "DRAFT",
    approved_by: str | None = None,
) -> int:
    title = title.strip()
    if not title:
        raise AcceptanceError("A criterion needs a title")
    if work_item_id is None and ralph_run_id is None:
        raise AcceptanceError("A criterion belongs to a planned task or a Ralph run")
    if origin not in ORIGINS or status not in ("DRAFT", "APPROVED"):
        raise AcceptanceError("Invalid origin or status")
    stamp = now()
    cur = db.execute(
        "INSERT INTO acceptance_criteria (project_id, work_item_id, ralph_run_id, title, description, "
        "status, required, origin, iteration, created_by, approved_by, approved_at, template_key, hints, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            project_id, work_item_id, ralph_run_id, title, description.strip(), status, int(required),
            origin, iteration, created_by.strip(), approved_by, stamp if status == "APPROVED" else None,
            template_key, json.dumps(hints or {}), stamp, stamp,
        ),
    )
    db.commit()
    return cur.lastrowid


def get(db: sqlite3.Connection, criterion_id: int) -> Criterion | None:
    row = db.execute("SELECT * FROM acceptance_criteria WHERE id = ?", (criterion_id,)).fetchone()
    return _criterion(row) if row else None


def list_criteria(
    db: sqlite3.Connection,
    project_id: int,
    work_item_id: int | None = None,
    ralph_run_id: int | None = None,
    status: str | None = None,
) -> list[Criterion]:
    sql, params = "SELECT * FROM acceptance_criteria WHERE project_id = ?", [project_id]
    if work_item_id:
        sql += " AND work_item_id = ?"
        params.append(work_item_id)
    if ralph_run_id:
        sql += " AND ralph_run_id = ?"
        params.append(ralph_run_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    return [_criterion(r) for r in db.execute(sql + " ORDER BY id", params)]


def update_text(db: sqlite3.Connection, criterion_id: int, title: str, description: str, required: bool) -> None:
    c = get(db, criterion_id)
    if c is None:
        raise LookupError("Criterion not found")
    if c.status not in ("DRAFT", "APPROVED"):
        raise AcceptanceError(f"A {c.status.lower()} criterion cannot be edited; re-open it first")
    if not title.strip():
        raise AcceptanceError("A criterion needs a title")
    # Editing approved wording invalidates the approval.
    status, approved_by, approved_at = (c.status, c.approved_by, c.approved_at)
    if c.status == "APPROVED" and title.strip() != c.title:
        status, approved_by, approved_at = "DRAFT", None, None
    db.execute(
        "UPDATE acceptance_criteria SET title = ?, description = ?, required = ?, status = ?, "
        "approved_by = ?, approved_at = ?, updated_at = ? WHERE id = ?",
        (title.strip(), description.strip(), int(required), status, approved_by, approved_at, now(), criterion_id),
    )
    db.commit()


def set_status(db: sqlite3.Connection, criterion_id: int, new_status: str, **fields) -> None:
    c = get(db, criterion_id)
    if c is None:
        raise LookupError("Criterion not found")
    if new_status not in TRANSITIONS[c.status]:
        raise AcceptanceError(f"{c.status} -> {new_status} is not allowed")
    values = {"status": new_status, "updated_at": now(), **fields}
    assignments = ", ".join(f"{k} = ?" for k in values)
    db.execute(f"UPDATE acceptance_criteria SET {assignments} WHERE id = ?", [*values.values(), criterion_id])
    db.commit()


def delete_draft(db: sqlite3.Connection, criterion_id: int) -> None:
    c = get(db, criterion_id)
    if c is None:
        raise LookupError("Criterion not found")
    if c.status != "DRAFT":
        raise AcceptanceError("Only draft criteria can be deleted")
    db.execute("DELETE FROM acceptance_criteria WHERE id = ?", (criterion_id,))
    db.commit()


# -- evidence ------------------------------------------------------------------------------


def add_evidence(
    db: sqlite3.Connection,
    criterion_id: int,
    evidence_type: str,
    reference_id: int | None = None,
    note: str = "",
    state: str = "LINKED",
    recorded_by: str = "",
) -> int:
    if evidence_type not in EVIDENCE_TYPES or state not in EVIDENCE_STATES:
        raise AcceptanceError("Invalid evidence type or state")
    if evidence_type != "MANUAL" and reference_id is None:
        raise AcceptanceError("Evidence needs a reference")
    if evidence_type == "MANUAL" and not note.strip():
        raise AcceptanceError("Manual evidence needs a note describing what was checked")
    existing = db.execute(
        "SELECT id, state FROM acceptance_evidence WHERE criterion_id = ? AND evidence_type = ? AND reference_id IS ?",
        (criterion_id, evidence_type, reference_id),
    ).fetchone()
    if existing and evidence_type != "MANUAL":
        if existing["state"] != state and state == "LINKED":
            set_evidence_state(db, existing["id"], "LINKED", recorded_by)
        return existing["id"]
    cur = db.execute(
        "INSERT INTO acceptance_evidence (criterion_id, evidence_type, reference_id, note, state, "
        "recorded_by, created_at) VALUES (?,?,?,?,?,?,?)",
        (criterion_id, evidence_type, reference_id, note.strip(), state, recorded_by.strip(), now()),
    )
    db.commit()
    return cur.lastrowid


def set_evidence_state(db: sqlite3.Connection, evidence_id: int, state: str, by: str = "") -> None:
    if state not in EVIDENCE_STATES:
        raise AcceptanceError("Invalid evidence state")
    db.execute(
        "UPDATE acceptance_evidence SET state = ?, recorded_by = CASE WHEN ? != '' THEN ? ELSE recorded_by END WHERE id = ?",
        (state, by.strip(), by.strip(), evidence_id),
    )
    db.commit()


def get_evidence(db: sqlite3.Connection, evidence_id: int) -> Evidence | None:
    row = db.execute("SELECT * FROM acceptance_evidence WHERE id = ?", (evidence_id,)).fetchone()
    return Evidence(**dict(row)) if row else None


def list_evidence(db: sqlite3.Connection, criterion_id: int, state: str | None = None) -> list[Evidence]:
    sql, params = "SELECT * FROM acceptance_evidence WHERE criterion_id = ?", [criterion_id]
    if state:
        sql += " AND state = ?"
        params.append(state)
    return [Evidence(**dict(r)) for r in db.execute(sql + " ORDER BY id", params)]
