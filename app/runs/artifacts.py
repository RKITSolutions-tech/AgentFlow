from __future__ import annotations

import os
import re
import shutil
import sqlite3

from app.runs import models
from app.runs.security import redact

MAX_COLLECTED_BYTES = 5 * 1024 * 1024
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ArtifactPathError(ValueError):
    """Raised when an artifact path would escape its permitted directory."""


def artifact_root(config) -> str:
    """Directory holding artifact content: beside the database unless configured."""
    configured = config.get("ARTIFACT_DIR")
    if configured:
        return os.path.abspath(configured)
    db_path = config["DATABASE_PATH"]
    base = os.path.dirname(os.path.abspath(db_path)) if db_path != ":memory:" else os.getcwd()
    return os.path.join(base, "artifacts")


def _safe_name(name: str) -> str:
    cleaned = _UNSAFE_NAME.sub("_", os.path.basename(name)).strip("._")
    return cleaned or "artifact"


def _resolve_within(root: str, relative: str) -> str:
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, relative))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise ArtifactPathError(f"Artifact path {relative!r} escapes the artifact directory")
    return target


def step_directory(run_id: int, seq: int) -> str:
    return os.path.join("runs", str(run_id), f"step_{seq}")


def write_artifact(
    db: sqlite3.Connection,
    root: str,
    run_id: int,
    step: models.Step,
    kind: str,
    name: str,
    content: str | bytes,
    redacted: bool = False,
) -> int:
    """Store `content` under the artifact root and index it in SQLite."""
    filename = f"{kind}_{_safe_name(name)}"
    relative = os.path.join(step_directory(run_id, step.seq), filename)
    target = _resolve_within(root, relative)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    with open(target, "wb") as fh:
        fh.write(data)
    return models.add_artifact(
        db, run_id, step.id, kind, name, relative, len(data), redacted=redacted
    )


def read_artifact(root: str, artifact: models.Artifact, limit: int | None = None) -> bytes:
    target = _resolve_within(root, artifact.path)
    with open(target, "rb") as fh:
        return fh.read(limit) if limit else fh.read()


def artifact_file(root: str, artifact: models.Artifact) -> str:
    """Absolute path of a stored artifact, re-validated on every access."""
    return _resolve_within(root, artifact.path)


def collect_files(
    db: sqlite3.Connection,
    root: str,
    run_id: int,
    step: models.Step,
    working_directory: str,
    extra_patterns: tuple[str, ...] = (),
) -> list[int]:
    """Copy files a step declared in `collect` out of the working directory.

    Paths are relative to `working_directory`; anything resolving outside it
    (`..`, symlinks) or over the size cap is skipped. Text content is redacted.
    """
    ids: list[int] = []
    for relative in step.collect:
        try:
            source = _resolve_within(working_directory, relative)
        except ArtifactPathError:
            models.add_event(
                db, run_id, "ArtifactSkipped", step_id=step.id, data=f"{relative}: outside working directory"
            )
            continue
        if not os.path.isfile(source):
            models.add_event(
                db, run_id, "ArtifactSkipped", step_id=step.id, data=f"{relative}: not found"
            )
            continue
        if os.path.getsize(source) > MAX_COLLECTED_BYTES:
            models.add_event(
                db, run_id, "ArtifactSkipped", step_id=step.id, data=f"{relative}: too large"
            )
            continue
        with open(source, "rb") as fh:
            raw = fh.read()
        was_redacted = False
        try:
            text, was_redacted = redact(raw.decode("utf-8"), extra_patterns)
            raw = text.encode("utf-8")
        except UnicodeDecodeError:
            pass  # binary content is stored as-is
        ids.append(
            write_artifact(
                db, root, run_id, step, "output", relative, raw, redacted=was_redacted
            )
        )
    return ids


def remove_run_artifacts(root: str, run_id: int) -> None:
    shutil.rmtree(_resolve_within(root, os.path.join("runs", str(run_id))), ignore_errors=True)
