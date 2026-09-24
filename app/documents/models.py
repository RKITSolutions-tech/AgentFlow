from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class Document:
    id: int
    project_id: int
    repo_id: int
    chain_id: str | None
    doc_type: str
    path: str
    title: str
    status: str
    current_git_sha: str | None
    supersedes_id: int | None
    created_at: str
    updated_at: str


@dataclass
class DocumentRef:
    document_id: int
    referenced_document_id: int
    relationship: str


def create_document(
    db: sqlite3.Connection,
    project_id: int,
    repo_id: int,
    doc_type: str,
    path: str,
    title: str,
    chain_id: str | None = None,
    current_git_sha: str | None = None,
) -> int:
    cur = db.execute(
        "INSERT INTO documents "
        "(project_id, repo_id, chain_id, doc_type, path, title, status, current_git_sha) "
        "VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?)",
        (project_id, repo_id, chain_id, doc_type, path, title, current_git_sha),
    )
    db.commit()
    return cur.lastrowid


def update_document_status(
    db: sqlite3.Connection, document_id: int, status: str
) -> None:
    db.execute(
        "UPDATE documents SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, document_id),
    )
    db.commit()


def get_document(db: sqlite3.Connection, document_id: int) -> Document | None:
    row = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if row is None:
        return None
    return _hydrate_document(row)


def list_documents(
    db: sqlite3.Connection,
    project_id: int | None = None,
    repo_id: int | None = None,
    status: str | None = None,
) -> list[Document]:
    query = "SELECT * FROM documents WHERE 1 = 1"
    params: list[Any] = []

    if project_id is not None:
        query += " AND project_id = ?"
        params.append(project_id)

    if repo_id is not None:
        query += " AND repo_id = ?"
        params.append(repo_id)

    if status is not None:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY id"

    rows = db.execute(query, params).fetchall()
    return [_hydrate_document(r) for r in rows]


def supersede_document(
    db: sqlite3.Connection, document_id: int, supersedes_id: int
) -> None:
    db.execute(
        "UPDATE documents SET supersedes_id = ?, status = 'SUPERSEDED', updated_at = datetime('now') WHERE id = ?",
        (supersedes_id, document_id),
    )
    db.commit()


def create_document_ref(
    db: sqlite3.Connection,
    document_id: int,
    referenced_document_id: int,
    relationship: str,
) -> None:
    db.execute(
        "INSERT INTO document_refs (document_id, referenced_document_id, relationship) "
        "VALUES (?, ?, ?)",
        (document_id, referenced_document_id, relationship),
    )
    db.commit()


def get_document_refs(
    db: sqlite3.Connection, document_id: int
) -> list[DocumentRef]:
    rows = db.execute(
        "SELECT * FROM document_refs WHERE document_id = ? ORDER BY referenced_document_id",
        (document_id,),
    ).fetchall()
    return [_hydrate_ref(r) for r in rows]


def get_referencing_documents(
    db: sqlite3.Connection, document_id: int
) -> list[DocumentRef]:
    rows = db.execute(
        "SELECT * FROM document_refs WHERE referenced_document_id = ? ORDER BY document_id",
        (document_id,),
    ).fetchall()
    return [_hydrate_ref(r) for r in rows]


def _hydrate_document(row: sqlite3.Row) -> Document:
    return Document(
        id=row["id"],
        project_id=row["project_id"],
        repo_id=row["repo_id"],
        chain_id=row["chain_id"],
        doc_type=row["doc_type"],
        path=row["path"],
        title=row["title"],
        status=row["status"],
        current_git_sha=row["current_git_sha"],
        supersedes_id=row["supersedes_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _hydrate_ref(row: sqlite3.Row) -> DocumentRef:
    return DocumentRef(
        document_id=row["document_id"],
        referenced_document_id=row["referenced_document_id"],
        relationship=row["relationship"],
    )
