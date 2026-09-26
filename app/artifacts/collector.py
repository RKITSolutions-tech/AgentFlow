"""Collects step output into the Artifact Library.

Files are copied (never moved) under `<artifact root>/pipelines/<execution>/
step_<id>/`, classified by extension, secret-redacted when they are text, and
indexed with a link back to the originating step.
"""
from __future__ import annotations

import glob
import mimetypes
import os
import sqlite3
import struct

from app.artifacts import models
from app.runs.artifacts import ArtifactPathError, _resolve_within, _safe_name
from app.runs.security import redact

MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_STEP = 50
TEXT_KINDS = ("log", "diff", "report")
_IMAGE = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_VIDEO = (".mp4", ".webm", ".mov")
_LOG = (".log", ".txt", ".out", ".err")
_DIFF = (".diff", ".patch")
_REPORT = (".html", ".htm", ".json", ".xml", ".csv", ".md", ".lcov", ".junit")


def classify(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(_IMAGE):
        return "screenshot"
    if lower.endswith(_VIDEO):
        return "video"
    if lower.endswith(_DIFF):
        return "diff"
    if "trace" in lower and lower.endswith((".zip", ".trace", ".har")):
        return "trace"
    if lower.endswith(_LOG):
        return "log"
    if lower.endswith(_REPORT):
        return "report"
    return "file"


def image_size(data: bytes) -> tuple[int, int] | None:
    """Width/height of a PNG or GIF, from the header (no imaging library)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    return None


def _metadata(kind: str, content: bytes) -> dict:
    meta: dict = {"size": len(content)}
    if kind == "screenshot":
        size = image_size(content)
        if size:
            meta["width"], meta["height"] = size
    elif kind in TEXT_KINDS:
        meta["lines"] = content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0)
    return meta


def store_bytes(
    db: sqlite3.Connection,
    root: str,
    project_id: int,
    name: str,
    content: bytes,
    directory: str,
    patterns: tuple[str, ...] = (),
    kind: str | None = None,
    **link,
) -> int:
    """Write `content` under `directory` (relative to the artifact root) and index it.

    Text kinds are redacted before they touch disk. `link` carries the
    originating references (execution_id, step_execution_id, step_name,
    ralph_run_id, iteration_number, tags).
    """
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError(f"{name} exceeds {MAX_ARTIFACT_BYTES} bytes")
    safe = _safe_name(name)
    kind = kind or classify(safe)
    redacted = False
    if kind in TEXT_KINDS:
        text, redacted = redact(content.decode("utf-8", "replace"), patterns)
        content = text.encode("utf-8")
    stem, ext = os.path.splitext(safe)
    relative, n = os.path.join(directory, safe), 1
    while os.path.exists(_resolve_within(root, relative)):
        n += 1
        relative = os.path.join(directory, f"{stem}_{n}{ext}")
    target = _resolve_within(root, relative)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(content)
    mime = mimetypes.guess_type(safe)[0] or "application/octet-stream"
    return models.add_artifact(
        db, project_id, kind, safe, relative, mime, len(content), redacted=redacted,
        metadata=_metadata(kind, content), **link,
    )


def step_directory(execution_id: int, step_id: int) -> str:
    return os.path.join("pipelines", str(execution_id), f"step_{step_id}")


def collect_files(
    db: sqlite3.Connection,
    root: str,
    project_id: int,
    workdir: str,
    patterns: list[str],
    execution_id: int,
    step_id: int,
    step_name: str,
    redact_patterns: tuple[str, ...] = (),
) -> list[int]:
    """Copy files matching `patterns` (globs relative to `workdir`) into the
    library. Anything resolving outside `workdir` is ignored."""
    ids: list[int] = []
    workdir_real = os.path.realpath(workdir)
    seen: set[str] = set()
    for pattern in patterns:
        if os.path.isabs(pattern) or ".." in pattern.split("/"):
            continue
        for match in sorted(glob.glob(os.path.join(workdir_real, pattern), recursive=True)):
            real = os.path.realpath(match)
            if real in seen or not os.path.isfile(real):
                continue
            if not real.startswith(workdir_real + os.sep):
                continue  # symlink pointing out of the repository
            seen.add(real)
            if len(ids) >= MAX_FILES_PER_STEP or os.path.getsize(real) > MAX_ARTIFACT_BYTES:
                continue
            with open(real, "rb") as fh:
                content = fh.read()
            ids.append(
                store_bytes(
                    db, root, project_id, os.path.basename(real), content,
                    step_directory(execution_id, step_id), redact_patterns,
                    execution_id=execution_id, step_execution_id=step_id, step_name=step_name,
                )
            )
    return ids


def register_existing(
    db: sqlite3.Connection,
    root: str,
    project_id: int,
    relative_path: str,
    name: str,
    execution_id: int,
    step_id: int,
    step_name: str,
    redacted: bool = False,
    kind: str | None = None,
) -> int:
    """Index a file the engine already wrote under the artifact root (step logs)."""
    target = _resolve_within(root, relative_path)
    size = os.path.getsize(target)
    with open(target, "rb") as fh:
        head = fh.read(MAX_ARTIFACT_BYTES)
    kind = kind or classify(name)
    return models.add_artifact(
        db, project_id, kind, _safe_name(name), relative_path,
        mimetypes.guess_type(name)[0] or "text/plain", size,
        execution_id=execution_id, step_execution_id=step_id, step_name=step_name,
        redacted=redacted, metadata=_metadata(kind, head),
    )


def file_path(root: str, artifact: models.Artifact) -> str:
    """Absolute path of a stored artifact, re-validated on every access."""
    return _resolve_within(root, artifact.path)


def remove_files(root: str, artifacts: list[models.Artifact]) -> None:
    for a in artifacts:
        try:
            os.remove(file_path(root, a))
        except (OSError, ArtifactPathError):
            pass


