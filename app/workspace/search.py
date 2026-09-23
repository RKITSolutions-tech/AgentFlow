from __future__ import annotations

import fnmatch
import os
import re
import time
from dataclasses import dataclass

from app.execution.base import ExecutionProvider
from app.security import PathNotAllowedError, validate_repository_path

MAX_QUERY_LENGTH = 200
MAX_RESULTS = 200
SEARCH_TIMEOUT_SECONDS = 15.0
_CACHE_TTL_SECONDS = 10.0

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_.*?/,{}\[\]+-]*$")
_RESULT_LINE_PATTERN = re.compile(r"^(.*?):(\d+):(.*)$")


class InvalidSearchQueryError(ValueError):
    """Raised when a search query or filter is empty, too long, or malformed."""


class SearchExecutionError(RuntimeError):
    """Raised when the underlying search command itself fails (e.g. bad filter)."""


@dataclass
class SearchMatch:
    relative_path: str
    line_number: int
    line_text: str


_cache: dict[tuple, tuple[float, list[SearchMatch]]] = {}


def _validate_query(query: str) -> str:
    query = (query or "").strip()
    if not query:
        raise InvalidSearchQueryError("Search query must not be empty")
    if len(query) > MAX_QUERY_LENGTH:
        raise InvalidSearchQueryError(
            f"Search query must be at most {MAX_QUERY_LENGTH} characters"
        )
    return query


def _validate_token(value: str, label: str) -> str:
    value = (value or "").strip()
    if value and not _TOKEN_PATTERN.match(value):
        raise InvalidSearchQueryError(f"{label} contains unsupported characters")
    return value


def _build_command(repo_root: str, query: str) -> list[str]:
    # File type / path filtering is applied in Python (see `_matches_filters`)
    # rather than via tool-specific pathspec flags, since `git grep`'s
    # pathspecs and GNU grep's basename-only `--include` behave differently
    # and would make filtering semantics depend on which tool is available.
    if os.path.isdir(os.path.join(repo_root, ".git")):
        # `--untracked` includes new-but-not-yet-committed files while still
        # honouring .gitignore, matching what a "search this repo" user expects.
        return ["git", "grep", "-n", "--color=never", "--untracked", "-m", "50", "-e", query]

    return ["grep", "-r", "-n", "-H", "-m", "50", "--exclude-dir=.git", "--", query, "."]


def _matches_filters(relative_path: str, file_type: str, path_pattern: str) -> bool:
    if file_type and not fnmatch.fnmatch(relative_path, f"*.{file_type}"):
        return False
    if path_pattern and not fnmatch.fnmatch(relative_path, path_pattern):
        return False
    return True


def _parse_line(line: str) -> SearchMatch | None:
    match = _RESULT_LINE_PATTERN.match(line)
    if not match:
        return None
    path, line_no, text = match.groups()
    path = path[2:] if path.startswith("./") else path
    return SearchMatch(relative_path=path, line_number=int(line_no), line_text=text[:300])


def search(
    execution_provider: ExecutionProvider,
    repo_root: str,
    query: str,
    *,
    file_type: str = "",
    path_pattern: str = "",
    allowed_roots: tuple[str, ...] = (),
) -> list[SearchMatch]:
    """Search `repo_root` for `query`, scoped to that repository only."""
    query = _validate_query(query)
    file_type = _validate_token(file_type, "File type filter")
    path_pattern = _validate_token(path_pattern, "Path filter")

    cache_key = (repo_root, query, file_type, path_pattern)
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    command = _build_command(repo_root, query)
    context = execution_provider.create_context({"working_directory": repo_root})
    try:
        process = execution_provider.execute(
            command, options={"context_id": context.id, "timeout": SEARCH_TIMEOUT_SECONDS}
        )
        events = execution_provider.stream_output(process.id)
    finally:
        execution_provider.destroy_context(context.id)

    if process.status != "COMPLETED":
        raise SearchExecutionError(f"Search command did not complete (status={process.status})")

    # Both git grep and grep exit 1 to mean "no matches" (not an error);
    # >=2 is a real failure (invalid regex, unreadable path, etc).
    if process.exit_code is not None and process.exit_code >= 2:
        stderr = "\n".join(
            e.data for e in events if e.event_type == "ProcessOutput" and e.stream == "stderr"
        )
        raise SearchExecutionError(stderr or "Search command failed")

    matches: list[SearchMatch] = []
    for event in events:
        if event.event_type != "ProcessOutput" or event.stream != "stdout":
            continue
        parsed = _parse_line(event.data)
        if parsed is None:
            continue
        if not _matches_filters(parsed.relative_path, file_type, path_pattern):
            continue
        try:
            validate_repository_path(
                f"{repo_root}/{parsed.relative_path}", allowed_roots or (repo_root,)
            )
        except PathNotAllowedError:
            continue
        matches.append(parsed)
        if len(matches) >= MAX_RESULTS:
            break

    _cache[cache_key] = (now, matches)
    return matches
