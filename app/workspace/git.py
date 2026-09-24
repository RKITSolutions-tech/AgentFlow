from __future__ import annotations

import re
from dataclasses import dataclass

from app.execution.base import ExecutionProvider
from app.security import PathNotAllowedError, validate_repository_path

GIT_TIMEOUT_SECONDS = 30.0


class GitCommandError(RuntimeError):
    """Raised when a git command fails."""


class NotAGitRepositoryError(GitCommandError):
    """Raised when the path is not a git repository."""


@dataclass
class GitStatusFile:
    """Represents a file in git status output."""

    path: str
    status: str
    staged: bool


@dataclass
class GitStatus:
    """Result of git status operation."""

    branch: str | None
    staged: list[GitStatusFile]
    unstaged: list[GitStatusFile]
    untracked: list[GitStatusFile]


@dataclass
class DiffLine:
    """A single line in a diff."""

    type: str  # "context", "added", "removed", "hunk_header"
    content: str
    line_num_old: int | None
    line_num_new: int | None


@dataclass
class FileDiff:
    """Represents changes to a single file."""

    path: str
    old_mode: str | None
    new_mode: str | None
    is_binary: bool
    lines: list[DiffLine]


@dataclass
class CommitInfo:
    """Information about a commit."""

    hash: str
    short_hash: str
    author: str
    date: str
    message: str


def _is_git_repo(repo_root: str, execution_provider: ExecutionProvider) -> bool:
    """Check if the repository is a git repository."""
    context = execution_provider.create_context({"working_directory": repo_root})
    try:
        process = execution_provider.execute(
            ["git", "rev-parse", "--git-dir"],
            options={"context_id": context.id, "timeout": GIT_TIMEOUT_SECONDS},
        )
        return process.exit_code == 0
    except Exception:
        return False
    finally:
        execution_provider.destroy_context(context.id)


def status(
    execution_provider: ExecutionProvider,
    repo_root: str,
    allowed_roots: tuple[str, ...] = (),
) -> GitStatus:
    """Get git status showing staged, unstaged, and untracked files."""
    validate_repository_path(repo_root, allowed_roots or (repo_root,))

    context = execution_provider.create_context({"working_directory": repo_root})
    try:
        # Get current branch
        process = execution_provider.execute(
            ["git", "branch", "--show-current"],
            options={"context_id": context.id, "timeout": GIT_TIMEOUT_SECONDS},
        )
        events = execution_provider.stream_output(process.id)
        branch = None
        for event in events:
            if event.event_type == "ProcessOutput" and event.stream == "stdout":
                branch = event.data.strip() or None
                break

        # Get status
        process = execution_provider.execute(
            ["git", "status", "--short"],
            options={"context_id": context.id, "timeout": GIT_TIMEOUT_SECONDS},
        )
        events = execution_provider.stream_output(process.id)

        staged = []
        unstaged = []
        untracked = []

        for event in events:
            if event.event_type != "ProcessOutput" or event.stream != "stdout":
                continue
            line = event.data.rstrip("\n")
            if not line:
                continue

            # Parse git status format: XY PATH
            # X = staged status, Y = unstaged status
            if len(line) < 4:
                continue

            x_status = line[0]
            y_status = line[1]
            path = line[3:]

            # Validate path is within repo
            try:
                validate_repository_path(
                    f"{repo_root}/{path}", allowed_roots or (repo_root,)
                )
            except PathNotAllowedError:
                continue

            if x_status == "?" and y_status == "?":
                untracked.append(GitStatusFile(path=path, status="?", staged=False))
                continue

            if x_status != " ":
                staged.append(GitStatusFile(path=path, status=x_status, staged=True))
            if y_status != " ":
                unstaged.append(GitStatusFile(path=path, status=y_status, staged=False))

        return GitStatus(branch=branch, staged=staged, unstaged=unstaged, untracked=untracked)

    finally:
        execution_provider.destroy_context(context.id)


def diff(
    execution_provider: ExecutionProvider,
    repo_root: str,
    path: str = "",
    staged: bool = False,
    allowed_roots: tuple[str, ...] = (),
) -> FileDiff | None:
    """Get diff for a file or all changes."""
    validate_repository_path(repo_root, allowed_roots or (repo_root,))
    if path:
        validate_repository_path(
            f"{repo_root}/{path}", allowed_roots or (repo_root,)
        )

    context = execution_provider.create_context({"working_directory": repo_root})
    try:
        cmd = ["git", "diff", "--no-color", "-U3"]
        if staged:
            cmd.append("--cached")
        if path:
            cmd.extend(["--", path])

        process = execution_provider.execute(
            cmd,
            options={"context_id": context.id, "timeout": GIT_TIMEOUT_SECONDS},
        )
        events = execution_provider.stream_output(process.id)

        # Parse unified diff format
        lines = []
        current_path = path or ""
        is_binary = False
        old_mode = None
        new_mode = None

        for event in events:
            if event.event_type != "ProcessOutput" or event.stream != "stdout":
                continue
            line_text = event.data.rstrip("\n")

            if line_text.startswith("diff --git"):
                # New file in diff
                continue
            if line_text.startswith("Binary files"):
                is_binary = True
                continue
            if line_text.startswith("old mode"):
                old_mode = line_text.split()[-1] if len(line_text.split()) > 2 else None
            if line_text.startswith("new mode"):
                new_mode = line_text.split()[-1] if len(line_text.split()) > 2 else None
            if line_text.startswith("---"):
                continue
            if line_text.startswith("+++"):
                continue
            if line_text.startswith("@@"):
                # Hunk header
                lines.append(
                    DiffLine(
                        type="hunk_header",
                        content=line_text,
                        line_num_old=None,
                        line_num_new=None,
                    )
                )
            elif line_text.startswith("+"):
                lines.append(
                    DiffLine(
                        type="added",
                        content=line_text[1:],
                        line_num_old=None,
                        line_num_new=None,
                    )
                )
            elif line_text.startswith("-"):
                lines.append(
                    DiffLine(
                        type="removed",
                        content=line_text[1:],
                        line_num_old=None,
                        line_num_new=None,
                    )
                )
            elif line_text.startswith(" "):
                lines.append(
                    DiffLine(
                        type="context",
                        content=line_text[1:],
                        line_num_old=None,
                        line_num_new=None,
                    )
                )

        if not lines and not is_binary:
            return None

        return FileDiff(
            path=current_path,
            old_mode=old_mode,
            new_mode=new_mode,
            is_binary=is_binary,
            lines=lines,
        )

    finally:
        execution_provider.destroy_context(context.id)


def log(
    execution_provider: ExecutionProvider,
    repo_root: str,
    max_count: int = 20,
    allowed_roots: tuple[str, ...] = (),
) -> list[CommitInfo]:
    """Get commit history."""
    validate_repository_path(repo_root, allowed_roots or (repo_root,))

    context = execution_provider.create_context({"working_directory": repo_root})
    try:
        process = execution_provider.execute(
            [
                "git",
                "log",
                f"--max-count={max_count}",
                "--format=%H%n%h%n%an%n%ai%n%s%n---END---",
            ],
            options={"context_id": context.id, "timeout": GIT_TIMEOUT_SECONDS},
        )
        events = execution_provider.stream_output(process.id)

        commits = []
        current_commit_lines = []

        for event in events:
            if event.event_type != "ProcessOutput" or event.stream != "stdout":
                continue

            current_commit_lines.append(event.data.rstrip("\n"))

        # Parse commit output
        full_output = "\n".join(current_commit_lines)
        commit_blocks = full_output.split("---END---")

        for block in commit_blocks:
            block = block.strip("\n")
            if not block.strip():
                continue
            parts = block.split("\n", 4)
            if len(parts) >= 5:
                full_hash, short_hash, author, date, message = (
                    parts[0],
                    parts[1],
                    parts[2],
                    parts[3],
                    parts[4],
                )
                commits.append(
                    CommitInfo(
                        hash=full_hash,
                        short_hash=short_hash,
                        author=author,
                        date=date,
                        message=message.strip(),
                    )
                )

        return commits

    finally:
        execution_provider.destroy_context(context.id)


def stage(
    execution_provider: ExecutionProvider,
    repo_root: str,
    path: str,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Stage a file for commit."""
    validate_repository_path(repo_root, allowed_roots or (repo_root,))
    validate_repository_path(f"{repo_root}/{path}", allowed_roots or (repo_root,))

    context = execution_provider.create_context({"working_directory": repo_root})
    try:
        process = execution_provider.execute(
            ["git", "add", "--", path],
            options={"context_id": context.id, "timeout": GIT_TIMEOUT_SECONDS},
        )
        if process.exit_code != 0:
            events = execution_provider.stream_output(process.id)
            stderr = "\n".join(
                e.data for e in events if e.event_type == "ProcessOutput" and e.stream == "stderr"
            )
            raise GitCommandError(f"git add failed: {stderr}")
    finally:
        execution_provider.destroy_context(context.id)


def unstage(
    execution_provider: ExecutionProvider,
    repo_root: str,
    path: str,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Unstage a file."""
    validate_repository_path(repo_root, allowed_roots or (repo_root,))
    validate_repository_path(f"{repo_root}/{path}", allowed_roots or (repo_root,))

    context = execution_provider.create_context({"working_directory": repo_root})
    try:
        process = execution_provider.execute(
            ["git", "reset", "HEAD", "--", path],
            options={"context_id": context.id, "timeout": GIT_TIMEOUT_SECONDS},
        )
        if process.exit_code != 0:
            events = execution_provider.stream_output(process.id)
            stderr = "\n".join(
                e.data for e in events if e.event_type == "ProcessOutput" and e.stream == "stderr"
            )
            raise GitCommandError(f"git reset failed: {stderr}")
    finally:
        execution_provider.destroy_context(context.id)


def commit(
    execution_provider: ExecutionProvider,
    repo_root: str,
    message: str,
    amend: bool = False,
    allowed_roots: tuple[str, ...] = (),
) -> CommitInfo:
    """Create a commit with the given message.

    Args:
        execution_provider: Execution provider for running git commands.
        repo_root: Root of the repository.
        message: Commit message.
        amend: Whether to amend the previous commit instead of creating a new one.
        allowed_roots: Allowed repository root paths.

    Returns:
        Information about the created/amended commit.

    Raises:
        GitCommandError: If the commit fails.
        ValueError: If the message is invalid.
    """
    validate_repository_path(repo_root, allowed_roots or (repo_root,))

    message = message.strip()
    if not message:
        raise ValueError("Commit message cannot be empty")
    if len(message) > 1000:
        raise ValueError("Commit message must be at most 1000 characters")

    context = execution_provider.create_context({"working_directory": repo_root})
    try:
        cmd = ["git", "commit", "-m", message]
        if amend:
            cmd.append("--amend")

        process = execution_provider.execute(
            cmd,
            options={"context_id": context.id, "timeout": GIT_TIMEOUT_SECONDS},
        )

        if process.exit_code != 0:
            events = execution_provider.stream_output(process.id)
            stderr = "\n".join(
                e.data for e in events if e.event_type == "ProcessOutput" and e.stream == "stderr"
            )
            raise GitCommandError(f"git commit failed: {stderr}")

        # Fetch the new commit info
        commits = log(
            execution_provider,
            repo_root,
            max_count=1,
            allowed_roots=allowed_roots,
        )

        if commits:
            return commits[0]

        raise GitCommandError("Commit succeeded but could not retrieve commit info")

    finally:
        execution_provider.destroy_context(context.id)
