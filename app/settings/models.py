from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# "local" covers any self-hosted, OpenAI-compatible server (LM Studio, Ollama,
# vLLM, ...); each catalog entry carries its own base_url/api_key rather than
# there being one fixed endpoint per provider.
PROVIDERS = ("openai", "anthropic", "local")


@dataclass
class ModelCatalogEntry:
    id: int
    provider: str
    model_id: str
    enabled: bool
    base_url: str = ""
    api_key: str = ""


def _hydrate(row: sqlite3.Row) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=row["id"],
        provider=row["provider"],
        model_id=row["model_id"],
        enabled=bool(row["enabled"]),
        base_url=row["base_url"],
        api_key=row["api_key"],
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


def add_model(
    db: sqlite3.Connection,
    provider: str,
    model_id: str,
    base_url: str = "",
    api_key: str = "",
) -> int:
    cur = db.execute(
        "INSERT INTO model_catalog (provider, model_id, base_url, api_key) VALUES (?, ?, ?, ?)",
        (provider, model_id, base_url, api_key),
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
