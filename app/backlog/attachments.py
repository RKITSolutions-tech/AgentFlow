from __future__ import annotations

import os
import shutil
import sqlite3

from app.backlog import persistence
from app.runs.artifacts import ArtifactPathError, _resolve_within, _safe_name

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg")


def item_directory(project_id: int, item_id: int) -> str:
    return os.path.join("backlog", str(project_id), str(item_id))


def _kind_for(filename: str) -> str:
    return "IMAGE" if filename.lower().endswith(_IMAGE_SUFFIXES) else "FILE"


def store_attachment(
    db: sqlite3.Connection, root: str, project_id: int, item_id: int, filename: str, content: bytes
) -> int:
    """Save an uploaded file under `<root>/backlog/<project>/<item>/` and index it.

    The client-supplied name is reduced to a safe basename and the final path
    is re-checked against the root; existing files are never overwritten.
    """
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes")
    directory = item_directory(project_id, item_id)
    safe = _safe_name(filename)
    stem, ext = os.path.splitext(safe)
    relative, n = os.path.join(directory, safe), 1
    while os.path.exists(_resolve_within(root, relative)):
        n += 1
        relative = os.path.join(directory, f"{stem}_{n}{ext}")
    target = _resolve_within(root, relative)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(content)
    return persistence.add_attachment(
        db, item_id, _kind_for(safe), os.path.basename(filename) or safe, relative, len(content)
    )


def attachment_file(root: str, attachment) -> str:
    """Absolute path of a stored attachment, re-validated on every access."""
    if attachment.kind == "LINK":
        raise ArtifactPathError("Link attachments have no file")
    return _resolve_within(root, attachment.path)


def remove_item_files(root: str, project_id: int, item_id: int) -> None:
    shutil.rmtree(_resolve_within(root, item_directory(project_id, item_id)), ignore_errors=True)
