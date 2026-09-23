from __future__ import annotations

import os
from dataclasses import dataclass

from app.security import validate_repository_path

# Files larger than this are shown as a warning instead of being loaded into
# the browser editor/viewer.
MAX_FILE_SIZE = 5 * 1024 * 1024

_BINARY_SNIFF_SIZE = 8192


class FileNotFoundInRepositoryError(ValueError):
    """Raised when a relative path does not resolve to the expected kind of entry."""


@dataclass
class DirEntry:
    name: str
    relative_path: str
    is_dir: bool
    size: int


@dataclass
class FileContent:
    relative_path: str
    content: str | None
    is_binary: bool
    too_large: bool
    size: int


def resolve_path(repo_root: str, relative_path: str) -> str:
    """Resolve `relative_path` within `repo_root`, rejecting any escape attempt."""
    relative_path = (relative_path or "").strip().lstrip("/")
    candidate = os.path.join(repo_root, relative_path) if relative_path else repo_root
    return validate_repository_path(candidate, (repo_root,))


def list_directory(repo_root: str, relative_path: str = "") -> list[DirEntry]:
    absolute = resolve_path(repo_root, relative_path)
    if not os.path.isdir(absolute):
        raise FileNotFoundInRepositoryError(f"{relative_path!r} is not a directory")

    entries = []
    for name in os.listdir(absolute):
        full = os.path.join(absolute, name)
        rel = os.path.relpath(full, repo_root)
        is_dir = os.path.isdir(full)
        size = 0 if is_dir else os.path.getsize(full)
        entries.append(DirEntry(name=name, relative_path=rel, is_dir=is_dir, size=size))

    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


def _looks_binary(sample: bytes) -> bool:
    return b"\x00" in sample


def read_file(repo_root: str, relative_path: str) -> FileContent:
    absolute = resolve_path(repo_root, relative_path)
    if not os.path.isfile(absolute):
        raise FileNotFoundInRepositoryError(f"{relative_path!r} is not a file")

    size = os.path.getsize(absolute)
    with open(absolute, "rb") as fh:
        sample = fh.read(_BINARY_SNIFF_SIZE)
    is_binary = _looks_binary(sample)
    too_large = size > MAX_FILE_SIZE

    if is_binary or too_large:
        return FileContent(
            relative_path=relative_path,
            content=None,
            is_binary=is_binary,
            too_large=too_large,
            size=size,
        )

    with open(absolute, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    return FileContent(
        relative_path=relative_path,
        content=content,
        is_binary=False,
        too_large=False,
        size=size,
    )


def write_file(repo_root: str, relative_path: str, content: str) -> None:
    absolute = resolve_path(repo_root, relative_path)
    if os.path.isdir(absolute):
        raise FileNotFoundInRepositoryError(f"{relative_path!r} is a directory")
    with open(absolute, "w", encoding="utf-8") as fh:
        fh.write(content)
