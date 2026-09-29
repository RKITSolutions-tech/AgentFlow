"""Backlog MCP tools: thin wrappers around `app/backlog/persistence.py` that
add the project-ownership check `app/backlog/views.py`'s `_item()` does for
every UI request, since `persistence.py` itself takes only an `item_id` and
does not enforce which project it belongs to.

Every write here goes through the same functions the Backlog UI calls, so
validation (`InvalidTransitionError`, unknown-priority `ValueError`) and the
`backlog_triage_history` audit trail behave identically whether the change
came from a person clicking a button or an agent calling a tool.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from typing import Any

from app.backlog import persistence
from app.backlog.models import BacklogItem, TriageEntry


def _owned_item(db: sqlite3.Connection, project_id: int, item_id: int) -> BacklogItem:
    item = persistence.get_item(db, item_id)
    if item is None or item.project_id != project_id:
        raise LookupError(f"Backlog item {item_id} not found in project {project_id}")
    return item


def _serialize(item: BacklogItem) -> dict[str, Any]:
    return dataclasses.asdict(item)


def _serialize_history(entry: TriageEntry) -> dict[str, Any]:
    return dataclasses.asdict(entry)


def create_item(
    db: sqlite3.Connection,
    project_id: int,
    title: str = "",
    text: str = "",
    priority: str | None = None,
    created_by: str = "",
) -> dict[str, Any]:
    item_id = persistence.create_item(
        db, project_id, text=text, title=title, priority=priority, created_by=created_by,
    )
    return _serialize(persistence.get_item(db, item_id))


def list_items(
    db: sqlite3.Connection,
    project_id: int,
    status: str | None = None,
    priority: str | None = None,
    sprint_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    items = persistence.list_items(
        db, project_id, status=status, priority=priority, sprint_id=sprint_id, limit=limit,
    )
    return [_serialize(item) for item in items]


def get_item(db: sqlite3.Connection, project_id: int, item_id: int) -> dict[str, Any]:
    return _serialize(_owned_item(db, project_id, item_id))


def update_item(
    db: sqlite3.Connection,
    project_id: int,
    item_id: int,
    title: str | None = None,
    text: str | None = None,
    priority: str | None = None,
    sprint_id: int | None = None,
) -> dict[str, Any]:
    _owned_item(db, project_id, item_id)
    fields = {
        k: v
        for k, v in {"title": title, "text": text, "priority": priority, "sprint_id": sprint_id}.items()
        if v is not None
    }
    persistence.update_item(db, item_id, **fields)
    return _serialize(persistence.get_item(db, item_id))


def transition_item(
    db: sqlite3.Connection,
    project_id: int,
    item_id: int,
    new_status: str,
    notes: str = "",
    changed_by: str = "",
) -> dict[str, Any]:
    _owned_item(db, project_id, item_id)
    persistence.transition(db, item_id, new_status, notes=notes, changed_by=changed_by)
    return _serialize(persistence.get_item(db, item_id))


def record_note(
    db: sqlite3.Connection, project_id: int, item_id: int, notes: str, changed_by: str = "",
) -> dict[str, Any]:
    _owned_item(db, project_id, item_id)
    persistence.record_note(db, item_id, notes, changed_by=changed_by)
    return _serialize(persistence.get_item(db, item_id))


def list_history(db: sqlite3.Connection, project_id: int, item_id: int) -> list[dict[str, Any]]:
    _owned_item(db, project_id, item_id)
    return [_serialize_history(entry) for entry in persistence.list_history(db, item_id)]
