from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime

from app.security import validate_repository_path

# Files larger than this are shown as a warning instead of being loaded into
# the browser editor/viewer.
MAX_FILE_SIZE = 5 * 1024 * 1024

# Largest single upload accepted; the app config key ``MAX_UPLOAD_BYTES``
# overrides it.
MAX_UPLOAD_SIZE = 25 * 1024 * 1024

_BINARY_SNIFF_SIZE = 8192
_MAX_NAME_LENGTH = 255


class FileNotFoundInRepositoryError(ValueError):
    """Raised when a relative path does not resolve to the expected kind of entry."""


class FileOperationError(ValueError):
    """A create/rename/delete/upload was rejected; the message is user-facing."""


class FileExistsInRepositoryError(FileOperationError):
    """The target of a create/rename/upload already exists."""


class UploadTooLargeError(FileOperationError):
    """An uploaded file exceeds the size limit."""


@dataclass
class DirEntry:
    name: str
    relative_path: str
    is_dir: bool
    size: int
    modified_at: datetime


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
        mtime = os.path.getmtime(full)
        modified_at = datetime.fromtimestamp(mtime)
        entries.append(DirEntry(name=name, relative_path=rel, is_dir=is_dir, size=size, modified_at=modified_at))

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


def get_file_type(name: str, is_dir: bool) -> str:
    """Determine file type for display purposes."""
    if is_dir:
        return "directory"

    ext = os.path.splitext(name)[1].lower()
    if not ext:
        return "file"

    type_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".json": "json",
        ".html": "html",
        ".css": "css",
        ".md": "markdown",
        ".txt": "text",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".sql": "sql",
        ".sh": "shell",
        ".bash": "shell",
        ".git": "directory",
        ".gitignore": "text",
    }
    return type_map.get(ext, f"file")


# -- Mutating operations -----------------------------------------------------
#
# All of these resolve every path through `resolve_path`, so anything that
# escapes the repository (``..``, absolute paths, symlinks pointing outside)
# raises PathNotAllowedError. The repository root and ``.git`` are never
# touched: git owns that directory's contents.


def _check_name(name: str) -> str:
    """Validate a single path component (no separators) and return it stripped."""
    name = (name or "").strip()
    if not name or name in (".", ".."):
        raise FileOperationError("A name is required.")
    if "/" in name or "\\" in name or "\x00" in name:
        raise FileOperationError("Names cannot contain slashes or null bytes.")
    if len(name.encode("utf-8")) > _MAX_NAME_LENGTH:
        raise FileOperationError("That name is too long.")
    return name


def _guard_git_dir(repo_root: str, absolute: str) -> None:
    rel = os.path.relpath(absolute, os.path.realpath(repo_root))
    if rel == ".git" or rel.startswith(".git" + os.sep):
        raise FileOperationError("The .git directory cannot be modified from here.")


def _child_path(repo_root: str, directory: str, name: str) -> tuple[str, str]:
    """Absolute path for ``name`` inside ``directory`` (which must exist)."""
    name = _check_name(name)
    parent = resolve_path(repo_root, directory)
    if not os.path.isdir(parent):
        raise FileNotFoundInRepositoryError(f"{directory!r} is not a directory")
    target = os.path.join(parent, name)
    _guard_git_dir(repo_root, target)
    return target, name


def _rel(repo_root: str, absolute: str) -> str:
    return os.path.relpath(absolute, os.path.realpath(repo_root))


def create_file(repo_root: str, directory: str, name: str) -> str:
    """Create an empty file; returns its repo-relative path."""
    target, _ = _child_path(repo_root, directory, name)
    try:
        with open(target, "x", encoding="utf-8"):
            pass
    except FileExistsError:
        raise FileExistsInRepositoryError(f"{name.strip()!r} already exists.") from None
    return _rel(repo_root, target)


def create_folder(repo_root: str, directory: str, name: str) -> str:
    target, _ = _child_path(repo_root, directory, name)
    try:
        os.mkdir(target)
    except FileExistsError:
        raise FileExistsInRepositoryError(f"{name.strip()!r} already exists.") from None
    return _rel(repo_root, target)


def _existing_entry(repo_root: str, relative_path: str) -> str:
    """Absolute path of an entry itself (a symlink is not followed)."""
    relative_path = (relative_path or "").strip().strip("/")
    if not relative_path:
        raise FileOperationError("The repository root cannot be changed.")
    parent_rel, name = os.path.split(relative_path)
    name = _check_name(name)
    target = os.path.join(resolve_path(repo_root, parent_rel), name)
    _guard_git_dir(repo_root, target)
    if not os.path.lexists(target):
        raise FileNotFoundInRepositoryError(f"{relative_path!r} does not exist")
    return target


def rename_entry(repo_root: str, relative_path: str, new_name: str) -> str:
    """Rename a file or folder within its directory; returns the new path."""
    source = _existing_entry(repo_root, relative_path)
    new_name = _check_name(new_name)
    destination = os.path.join(os.path.dirname(source), new_name)
    _guard_git_dir(repo_root, destination)
    if os.path.lexists(destination):
        raise FileExistsInRepositoryError(f"{new_name!r} already exists.")
    os.rename(source, destination)
    return _rel(repo_root, destination)


def delete_entry(repo_root: str, relative_path: str) -> None:
    """Delete a file, symlink or folder (recursively)."""
    target = _existing_entry(repo_root, relative_path)
    if os.path.isdir(target) and not os.path.islink(target):
        shutil.rmtree(target)
    else:
        os.unlink(target)


def save_upload(
    repo_root: str,
    directory: str,
    filename: str,
    stream,
    max_bytes: int = MAX_UPLOAD_SIZE,
) -> str:
    """Write an uploaded stream into ``directory`` without overwriting.

    Only the final component of ``filename`` is used. The size limit is
    enforced while reading, so an oversized upload is never fully buffered
    or left half-written on disk.
    """
    target, name = _child_path(repo_root, directory, os.path.basename((filename or "").replace("\\", "/")))
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UploadTooLargeError(
            f"{name!r} is larger than the {max_bytes // (1024 * 1024)} MB upload limit."
        )
    try:
        with open(target, "xb") as fh:
            fh.write(data)
    except FileExistsError:
        raise FileExistsInRepositoryError(f"{name!r} already exists.") from None
    return _rel(repo_root, target)
