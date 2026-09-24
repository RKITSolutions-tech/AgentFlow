from __future__ import annotations

import sqlite3
from dataclasses import dataclass

PROVIDERS = ("openai", "anthropic")


@dataclass
class ModelCatalogEntry:
    id: int
    provider: str
    model_id: str
    enabled: bool


def _hydrate(row: sqlite3.Row) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=row["id"],
        provider=row["provider"],
        model_id=row["model_id"],
        enabled=bool(row["enabled"]),
    )


def list_models(db: sqlite3.Connection, provider: str | None = None) -> list[ModelCatalogEntry]:
    if provider is None:
        rows = db.execute(
            "SELECT * FROM model_catalog ORDER BY provider, model_id"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM model_catalog WHERE provider = ? ORDER BY model_id",
            (provider,),
        ).fetchall()
    return [_hydrate(row) for row in rows]


def list_enabled_models(db: sqlite3.Connection, provider: str | None = None) -> list[ModelCatalogEntry]:
    return [entry for entry in list_models(db, provider) if entry.enabled]


def add_model(db: sqlite3.Connection, provider: str, model_id: str) -> int:
    cur = db.execute(
        "INSERT INTO model_catalog (provider, model_id) VALUES (?, ?)",
        (provider, model_id),
    )
    db.commit()
    return cur.lastrowid


def set_model_enabled(db: sqlite3.Connection, catalog_id: int, enabled: bool) -> None:
    db.execute(
        "UPDATE model_catalog SET enabled = ? WHERE id = ?",
        (int(enabled), catalog_id),
    )
    db.commit()


def delete_model(db: sqlite3.Connection, catalog_id: int) -> None:
    db.execute("DELETE FROM model_catalog WHERE id = ?", (catalog_id,))
    db.commit()


def get_model(db: sqlite3.Connection, catalog_id: int) -> ModelCatalogEntry | None:
    row = db.execute("SELECT * FROM model_catalog WHERE id = ?", (catalog_id,)).fetchone()
    return _hydrate(row) if row else None
