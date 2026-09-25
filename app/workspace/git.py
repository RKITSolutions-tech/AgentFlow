from __future__ import annotations

import os
import re
from dataclasses import dataclass

from app.execution.base import ExecutionProvider
from app.security import PathNotAllowedError, validate_repository_path

GIT_TIMEOUT_SECONDS = 30.0
GIT_NETWORK_TIMEOUT_SECONDS = 120.0
# Network operations must never wait on a credential prompt nobody can answer.
_NON_INTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
}


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


@dataclass
class BranchInfo:
    """A local branch."""

    name: str
    current: bool
    upstream: str | None


_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def validate_ref_name(name: str) -> str:
    """Validate a branch name, rejecting anything git or a shell could misread.

    Returns the stripped name. Raises ValueError for option-like names, path
    traversal segments, and other characters git disallows in refs.
    """
    name = name.strip()
    if not name or len(name) > 200 or not _REF_PATTERN.match(name):
        raise ValueError(f"Invalid branch name: {name!r}")
    if (
        ".." in name
        or "//" in name
        or name.endswith(("/", ".", ".lock"))
        or any(part.startswith(".") or part.endswith(".lock") for part in name.split("/"))
    ):
        raise ValueError(f"Invalid branch name: {name!r}")
    return name


def _run_git(
    execution_provider: ExecutionProvider,
    repo_root: str,
    args: list[str],
    allowed_roots: tuple[str, ...] = (),
    network: bool = False,
) -> str:
    """Run a git command in `repo_root`, returning stdout or raising GitCommandError."""
    validate_repository_path(repo_root, allowed_roots or (repo_root,))

    context = execution_provider.create_context({"working_directory": repo_root})
    try:
        options: dict = {"context_id": context.id, "timeout": GIT_TIMEOUT_SECONDS}
        if network:
            options["timeout"] = GIT_NETWORK_TIMEOUT_SECONDS
            options["environment"] = _NON_INTERACTIVE_ENV
        process = execution_provider.execute(["git", *args], options=options)
        events = list(execution_provider.stream_output(process.id))
        if process.exit_code != 0:
            stderr = "\n".join(
                e.data for e in events if e.event_type == "ProcessOutput" and e.stream == "stderr"
            )
            raise GitCommandError(f"git {args[0]} failed: {stderr.strip()}")
        return "\n".join(
            e.data for e in events if e.event_type == "ProcessOutput" and e.stream == "stdout"
        )
    finally:
        execution_provider.destroy_context(context.id)


def list_branches(
    execution_provider: ExecutionProvider,
    repo_root: str,
    allowed_roots: tuple[str, ...] = (),
) -> list[BranchInfo]:
    """List local branches with their upstream, marking the current one."""
    output = _run_git(
        execution_provider,
        repo_root,
        ["branch", "--format=%(HEAD)|%(refname:short)|%(upstream:short)"],
        allowed_roots,
    )
    branches = []
    for line in output.splitlines():
        parts = line.split("|")
        if len(parts) != 3 or not parts[1]:
            continue  # e.g. detached HEAD pseudo-entry
        branches.append(
            BranchInfo(name=parts[1], current=parts[0] == "*", upstream=parts[2] or None)
        )
    return branches


def checkout_branch(
    execution_provider: ExecutionProvider,
    repo_root: str,
    name: str,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Switch to an existing local branch."""
    name = validate_ref_name(name)
    _run_git(execution_provider, repo_root, ["switch", name], allowed_roots)


def create_branch(
    execution_provider: ExecutionProvider,
    repo_root: str,
    name: str,
    checkout: bool = True,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Create a branch from HEAD, optionally switching to it."""
    name = validate_ref_name(name)
    args = ["switch", "-c", name] if checkout else ["branch", name]
    _run_git(execution_provider, repo_root, args, allowed_roots)


def delete_branch(
    execution_provider: ExecutionProvider,
    repo_root: str,
    name: str,
    force: bool = False,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Delete a local branch. Without `force`, git refuses unmerged branches."""
    name = validate_ref_name(name)
    _run_git(
        execution_provider,
        repo_root,
        ["branch", "-D" if force else "-d", name],
        allowed_roots,
    )


@dataclass
class RemoteStatus:
    """Where the current branch stands relative to its upstream."""

    branch: str | None
    upstream: str | None
    ahead: int
    behind: int
    remotes: list[str]


def remote_status(
    execution_provider: ExecutionProvider,
    repo_root: str,
    allowed_roots: tuple[str, ...] = (),
) -> RemoteStatus:
    """Report remotes and the current branch's ahead/behind counts (local data only)."""
    remotes = _run_git(execution_provider, repo_root, ["remote"], allowed_roots).split()
    branch = _run_git(
        execution_provider, repo_root, ["branch", "--show-current"], allowed_roots
    ).strip() or None

    upstream = None
    ahead = behind = 0
    if branch:
        try:
            upstream = _run_git(
                execution_provider,
                repo_root,
                ["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"],
                allowed_roots,
            ).strip() or None
        except GitCommandError:
            upstream = None  # no upstream configured
    if upstream:
        counts = _run_git(
            execution_provider,
            repo_root,
            ["rev-list", "--left-right", "--count", f"{branch}...{upstream}"],
            allowed_roots,
        ).split()
        if len(counts) == 2:
            ahead, behind = int(counts[0]), int(counts[1])
    return RemoteStatus(
        branch=branch, upstream=upstream, ahead=ahead, behind=behind, remotes=remotes
    )


def _validate_remote(execution_provider, repo_root, remote, allowed_roots) -> str:
    """Only accept a remote that is actually configured (also blocks option-like values)."""
    remote = validate_ref_name(remote)
    configured = _run_git(execution_provider, repo_root, ["remote"], allowed_roots).split()
    if remote not in configured:
        raise ValueError(f"Unknown remote: {remote!r}")
    return remote


def fetch(
    execution_provider: ExecutionProvider,
    repo_root: str,
    remote: str = "origin",
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Fetch from a configured remote, pruning deleted branches."""
    remote = _validate_remote(execution_provider, repo_root, remote, allowed_roots)
    _run_git(
        execution_provider, repo_root, ["fetch", "--prune", remote], allowed_roots, network=True
    )


def pull(
    execution_provider: ExecutionProvider,
    repo_root: str,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Fast-forward the current branch from its upstream; never creates merge commits."""
    _run_git(
        execution_provider, repo_root, ["pull", "--ff-only"], allowed_roots, network=True
    )


def push(
    execution_provider: ExecutionProvider,
    repo_root: str,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Push the current branch to its upstream. Never forces."""
    _run_git(execution_provider, repo_root, ["push"], allowed_roots, network=True)


def publish_branch(
    execution_provider: ExecutionProvider,
    repo_root: str,
    remote: str = "origin",
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Push the current branch to `remote` and set it as the upstream."""
    remote = _validate_remote(execution_provider, repo_root, remote, allowed_roots)
    branch = _run_git(
        execution_provider, repo_root, ["branch", "--show-current"], allowed_roots
    ).strip()
    if not branch:
        raise GitCommandError("Cannot publish a detached HEAD")
    branch = validate_ref_name(branch)
    _run_git(
        execution_provider,
        repo_root,
        ["push", "--set-upstream", remote, branch],
        allowed_roots,
        network=True,
    )


def _repo_relative_path(repo_root: str, path: str, allowed_roots: tuple[str, ...]) -> str:
    """Validate `path` for a destructive operation: it must stay inside `repo_root`.

    The allowed-roots check used elsewhere would accept a sibling repository;
    destructive actions are stricter and never leave this repository.
    """
    path = path.strip()
    if not path or "\x00" in path:
        raise ValueError("Path required")
    if os.path.isabs(path):
        raise ValueError("Path must be relative to the repository")
    root = validate_repository_path(repo_root, allowed_roots or (repo_root,))
    target = validate_repository_path(f"{root}/{path}", (root,))
    if target == root:
        raise ValueError("Refusing to act on the repository root")
    return path


def discard_changes(
    execution_provider: ExecutionProvider,
    repo_root: str,
    path: str,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Throw away unstaged changes to a tracked file (irreversible)."""
    path = _repo_relative_path(repo_root, path, allowed_roots)
    _run_git(execution_provider, repo_root, ["restore", "--", path], allowed_roots)


def delete_untracked(
    execution_provider: ExecutionProvider,
    repo_root: str,
    path: str,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Delete an untracked file or directory (irreversible; tracked files are never touched)."""
    path = _repo_relative_path(repo_root, path, allowed_roots)
    _run_git(execution_provider, repo_root, ["clean", "-fd", "--", path], allowed_roots)


def undo_last_commit(
    execution_provider: ExecutionProvider,
    repo_root: str,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Undo the latest commit, keeping its changes staged.

    Only allowed while that commit has not been pushed, so published history
    is never rewritten.
    """
    status = remote_status(execution_provider, repo_root, allowed_roots)
    if status.upstream and status.ahead == 0:
        raise GitCommandError("The latest commit is already pushed; refusing to rewrite it")
    try:
        _run_git(execution_provider, repo_root, ["rev-parse", "--verify", "HEAD~1"], allowed_roots)
    except GitCommandError:
        raise GitCommandError("Nothing to undo: there is no earlier commit") from None
    _run_git(execution_provider, repo_root, ["reset", "--soft", "HEAD~1"], allowed_roots)


@dataclass
class WorktreeInfo:
    """A working tree attached to the repository (the first is the main one)."""

    path: str
    head: str
    branch: str | None
    is_main: bool
    locked: bool


def list_worktrees(
    execution_provider: ExecutionProvider,
    repo_root: str,
    allowed_roots: tuple[str, ...] = (),
) -> list[WorktreeInfo]:
    output = _run_git(
        execution_provider, repo_root, ["worktree", "list", "--porcelain"], allowed_roots
    )
    worktrees: list[WorktreeInfo] = []
    for block in output.split("\n\n"):
        fields = {}
        for line in block.strip().splitlines():
            key, _, value = line.partition(" ")
            fields[key] = value
        if "worktree" not in fields:
            continue
        branch = fields.get("branch")
        if branch and branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/"):]
        worktrees.append(
            WorktreeInfo(
                path=fields["worktree"],
                head=fields.get("HEAD", ""),
                branch=branch or None,
                is_main=not worktrees,
                locked="locked" in fields,
            )
        )
    return worktrees


def default_worktree_path(repo_root: str, branch: str) -> str:
    """``<repo>.worktrees/<branch>``: a sibling of the repo, never inside it,
    so the main checkout's status stays clean."""
    return os.path.join(
        os.path.realpath(repo_root) + ".worktrees", validate_ref_name(branch).replace("/", "-")
    )


def add_worktree(
    execution_provider: ExecutionProvider,
    repo_root: str,
    branch: str,
    base: str | None = None,
    new_branch: bool = True,
    path: str | None = None,
    allowed_roots: tuple[str, ...] = (),
) -> str:
    """Check ``branch`` out in a new worktree and return its path.

    With ``new_branch`` the branch is created (from ``base`` or HEAD); otherwise
    an existing branch that is not checked out elsewhere is used.
    """
    branch = validate_ref_name(branch)
    target = validate_repository_path(
        path or default_worktree_path(repo_root, branch), allowed_roots or (repo_root,)
    )
    if os.path.lexists(target):
        raise GitCommandError(f"{target} already exists")
    args = ["worktree", "add"]
    if new_branch:
        args += ["-b", branch, target]
        if base:
            args.append(validate_ref_name(base))
    else:
        args += [target, branch]
    os.makedirs(os.path.dirname(target), exist_ok=True)
    _run_git(execution_provider, repo_root, args, allowed_roots)
    return target


def remove_worktree(
    execution_provider: ExecutionProvider,
    repo_root: str,
    path: str,
    force: bool = False,
    allowed_roots: tuple[str, ...] = (),
) -> None:
    """Remove a linked worktree. Git refuses one with uncommitted changes
    unless ``force``; the main worktree can never be removed."""
    target = os.path.realpath(path)
    known = {
        os.path.realpath(w.path): w
        for w in list_worktrees(execution_provider, repo_root, allowed_roots)
    }
    worktree = known.get(target)
    if worktree is None:
        raise GitCommandError("That is not a worktree of this repository")
    if worktree.is_main:
        raise GitCommandError("The main worktree cannot be removed")
    args = ["worktree", "remove"] + (["--force"] if force else []) + [worktree.path]
    _run_git(execution_provider, repo_root, args, allowed_roots)


def merge_branch(
    execution_provider: ExecutionProvider,
    repo_root: str,
    branch: str,
    allowed_roots: tuple[str, ...] = (),
) -> str:
    """Merge ``branch`` into the branch checked out at ``repo_root``.

    Refuses on uncommitted changes. A conflicting merge is aborted so the
    checkout is left exactly as it was, and the conflict is reported.
    """
    branch = validate_ref_name(branch)
    if _run_git(execution_provider, repo_root, ["status", "--porcelain"], allowed_roots).strip():
        raise GitCommandError("Commit or discard your uncommitted changes before merging")
    try:
        return _run_git(
            execution_provider, repo_root, ["merge", "--no-edit", branch], allowed_roots
        ).strip()
    except GitCommandError as exc:
        try:
            _run_git(execution_provider, repo_root, ["merge", "--abort"], allowed_roots)
        except GitCommandError:
            pass  # nothing to abort: the merge failed before starting
        raise GitCommandError(
            f"Merge of {branch} was aborted (usually conflicts; nothing was changed). {exc}"
        ) from exc
