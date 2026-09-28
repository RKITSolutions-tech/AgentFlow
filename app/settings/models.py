from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# "local" covers any self-hosted, OpenAI-compatible server (LM Studio, Ollama,
# vLLM, ...); each catalog entry carries its own base_url/api_key rather than
# there being one fixed endpoint per provider.
PROVIDERS = ("openai", "anthropic", "local")


class ModelCatalogConfigError(RuntimeError):
    """A feature was configured (env var/Settings) to use a `local` catalog
    model that doesn't exist, is disabled, or was never named at all."""


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


def resolve_local_model(db: sqlite3.Connection, model_id: str, *, label: str) -> ModelCatalogEntry:
    """Looks up an enabled `local` catalog entry by `model_id` for a feature that
    names its model via config/env var rather than a per-session picker (e.g. a
    PLANNING_AGENT=local role adapter). `label` identifies that setting (e.g.
    `"AGENTFLOW_PLANNING_MODEL"`) so a `ModelCatalogConfigError` names the exact
    knob to fix."""
    model_id = (model_id or "").strip()
    if not model_id:
        raise ModelCatalogConfigError(
            f"{label} must name a model id in the Settings 'local' model catalog"
        )
    entry = next(
        (
            e
            for e in list_models(db, provider="local")
            if e.model_id == model_id and e.enabled
        ),
        None,
    )
    if entry is None:
        raise ModelCatalogConfigError(
            f"{label}={model_id!r} has no enabled entry in the Settings 'local' model catalog"
        )
    return entry
