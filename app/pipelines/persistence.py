from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Callable

from app.pipelines import schema
from app.pipelines.validator import validate
from app.runs.models import now

DEFINITIONS_DIR = os.path.join(os.path.dirname(__file__), "definitions")


class PipelineDefinitionError(ValueError):
    """A definition failed validation; `errors` lists every problem found."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Pipeline:
    id: int
    project_id: int | None  # None = shared built-in
    name: str
    description: str
    type: str
    enabled: bool
    current_version: int
    created_at: str
    updated_at: str


def _hydrate(row: sqlite3.Row) -> Pipeline:
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    return Pipeline(**data)


def _resolver(db: sqlite3.Connection, project_id: int | None) -> Callable[[str], dict | None]:
    def resolve(name: str) -> dict | None:
        pipeline = find_pipeline(db, name, project_id)
        return get_definition(db, pipeline.id) if pipeline else None

    return resolve


def _check(db, definition: dict, project_id: int | None, own_name: str | None = None) -> None:
    base = _resolver(db, project_id)
    # A pipeline that is not saved yet must still be able to refer to itself
    # so the composer (not the validator) reports the recursion clearly.
    errors = validate(
        definition, lambda n: base(n) or ({"name": n, "elements": []} if n == own_name else None)
    )
    if errors:
        raise PipelineDefinitionError(errors)


def find_pipeline(db: sqlite3.Connection, name: str, project_id: int | None = None) -> Pipeline | None:
    """Project pipeline by name, falling back to a shared built-in."""
    for scope in ([project_id] if project_id is not None else []) + [None]:
        row = db.execute(
            "SELECT * FROM pipelines WHERE name = ? AND project_id IS ?", (name, scope)
        ).fetchone()
        if row:
            return _hydrate(row)
    return None


def get_pipeline(db: sqlite3.Connection, pipeline_id: int) -> Pipeline | None:
    row = db.execute("SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)).fetchone()
    return _hydrate(row) if row else None


def list_pipelines(db: sqlite3.Connection, project_id: int | None = None) -> list[Pipeline]:
    rows = db.execute(
        "SELECT * FROM pipelines WHERE project_id IS NULL OR project_id = ? ORDER BY name",
        (project_id,),
    ).fetchall()
    return [_hydrate(r) for r in rows]


def create_pipeline(
    db: sqlite3.Connection,
    definition: dict,
    project_id: int | None = None,
    created_by: str = "",
    note: str = "",
) -> int:
    _check(db, definition, project_id, definition.get("name"))
    name = definition["name"].strip()
    existing = db.execute(
        "SELECT 1 FROM pipelines WHERE name = ? AND project_id IS ?", (name, project_id)
    ).fetchone()
    if existing:
        raise PipelineDefinitionError([f"A pipeline named {name!r} already exists"])
    stamp = now()
    cur = db.execute(
        "INSERT INTO pipelines (project_id, name, description, type, current_version, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
        (project_id, name, definition.get("description", ""), definition.get("type", "CUSTOM"), stamp, stamp),
    )
    pipeline_id = cur.lastrowid
    _write_version(db, pipeline_id, definition, created_by, note)
    db.commit()
    return pipeline_id


def new_version(
    db: sqlite3.Connection, pipeline_id: int, definition: dict, created_by: str = "", note: str = ""
) -> int:
    """Edits never change history: they append a version (§17)."""
    pipeline = get_pipeline(db, pipeline_id)
    if pipeline is None:
        raise LookupError(f"Pipeline {pipeline_id} not found")
    if definition.get("name", "").strip() != pipeline.name:
        raise PipelineDefinitionError(["A new version keeps the pipeline's name"])
    _check(db, definition, pipeline.project_id, pipeline.name)
    version = _write_version(db, pipeline_id, definition, created_by, note)
    db.execute(
        "UPDATE pipelines SET description = ?, type = ?, updated_at = ? WHERE id = ?",
        (definition.get("description", ""), definition.get("type", "CUSTOM"), now(), pipeline_id),
    )
    db.commit()
    return version


def _write_version(db, pipeline_id: int, definition: dict, created_by: str, note: str) -> int:
    version = db.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM pipeline_versions WHERE pipeline_id = ?",
        (pipeline_id,),
    ).fetchone()[0]
    stamp = now()
    cur = db.execute(
        "INSERT INTO pipeline_versions (pipeline_id, version, note, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (pipeline_id, version, note, created_by, stamp),
    )
    version_id = cur.lastrowid
    for seq, el in enumerate(definition["elements"], start=1):
        deps = el.get("depends_on")
        db.execute(
            "INSERT INTO pipeline_elements (version_id, sequence, name, element_type, description, "
            "enabled, phase, configuration, compensation_policy, depends_on, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id, seq, el["name"], el["type"], el.get("description", ""),
                el.get("enabled", "ENABLED"), el.get("phase", "MAIN"),
                json.dumps(el.get("config", {})), json.dumps(el.get("compensation", {})),
                None if deps is None else json.dumps(deps), stamp,
            ),
        )
    db.execute("UPDATE pipelines SET current_version = ? WHERE id = ?", (version, pipeline_id))
    return version


def get_definition(db: sqlite3.Connection, pipeline_id: int, version: int | None = None) -> dict:
    """Rebuild the definition dict for a version (default: the current one)."""
    pipeline = get_pipeline(db, pipeline_id)
    if pipeline is None:
        raise LookupError(f"Pipeline {pipeline_id} not found")
    version = version or pipeline.current_version
    row = db.execute(
        "SELECT id FROM pipeline_versions WHERE pipeline_id = ? AND version = ?",
        (pipeline_id, version),
    ).fetchone()
    if row is None:
        raise LookupError(f"Pipeline {pipeline_id} has no version {version}")
    elements = []
    for r in db.execute(
        "SELECT * FROM pipeline_elements WHERE version_id = ? ORDER BY sequence", (row["id"],)
    ):
        el = {
            "name": r["name"],
            "type": r["element_type"],
            "description": r["description"],
            "enabled": r["enabled"],
            "phase": r["phase"],
            "category": schema.category(r["element_type"]),
            "config": json.loads(r["configuration"]),
            "compensation": json.loads(r["compensation_policy"]),
        }
        if r["depends_on"] is not None:
            el["depends_on"] = json.loads(r["depends_on"])
        elements.append(el)
    return {
        "name": pipeline.name,
        "description": pipeline.description,
        "type": pipeline.type,
        "version": version,
        "elements": elements,
    }


def list_versions(db: sqlite3.Connection, pipeline_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT version, note, created_by, created_at FROM pipeline_versions "
        "WHERE pipeline_id = ? ORDER BY version DESC",
        (pipeline_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_enabled(db: sqlite3.Connection, pipeline_id: int, enabled: bool) -> None:
    db.execute("UPDATE pipelines SET enabled = ?, updated_at = ? WHERE id = ?", (int(enabled), now(), pipeline_id))
    db.commit()


def resolver_for(db: sqlite3.Connection, project_id: int | None) -> Callable[[str], dict | None]:
    return _resolver(db, project_id)


def seed_builtins(db: sqlite3.Connection) -> list[str]:
    """Install shared definitions from `definitions/*.json` that are missing.

    Existing ones are left alone (users may have new versions); sub-pipelines
    are created first so profiles that reference them validate.
    """
    loaded = []
    for filename in sorted(os.listdir(DEFINITIONS_DIR)):
        if filename.endswith(".json"):
            with open(os.path.join(DEFINITIONS_DIR, filename), encoding="utf-8") as fh:
                loaded.append(json.load(fh))
    created = []
    # Repeat until nothing more can be created so file order never matters.
    pending = [d for d in loaded if find_pipeline(db, d["name"]) is None]
    while pending:
        progressed = False
        for definition in list(pending):
            try:
                create_pipeline(db, definition, None, created_by="builtin", note="built-in")
            except PipelineDefinitionError:
                continue
            created.append(definition["name"])
            pending.remove(definition)
            progressed = True
        if not progressed:
            raise PipelineDefinitionError(
                [f"Built-in {d['name']!r} is invalid" for d in pending]
            )
    return created
