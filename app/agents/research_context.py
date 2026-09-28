"""Repository-scoped context for the RESEARCH role (docs/AGENT_ADAPTER.md §23,
Phase A: "the project's own repository and docs, reusing the context-file
path checks").

`RepositoryContextLoader` discovers a bounded set of files relevant to
answering a question about a project's own repository -- READMEs, `docs/`,
dependency manifests get their full content inlined (so design docs like
`docs/HIGH_LEVEL_DESIGN.md`/`docs/AGENT_ADAPTER.md` are fed in directly);
`src/`/`app/`/`tests/` are only listed (path + size), keeping the prompt
bounded on a large tree.

Path safety follows the same technique as
`app/prompts/assembler.resolve_context_files` (glob under a validated root;
absolute patterns, `..`, and symlinks that resolve outside the root are all
rejected) plus `app/security.validate_repository_path` for the root itself.
The caps here are just larger, since a one-shot research pass reasonably
wants more of a repository than a per-turn prompt-context assembly does, but
still bounded so a huge or malicious repository can't make a research
session read everything on disk.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

from app.security import validate_repository_path

MAX_FILES = 500
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_INLINE_BYTES = 64 * 1024  # per-file cap when a file's content is inlined
MAX_INLINE_FILES = 25  # how many discovered files get full content inlined

# Full content is inlined for these (small, high-signal: READMEs, docs, manifests).
INLINE_PATTERNS = (
    "README*", "readme*", "CHANGELOG*",
    "docs/**/*.md", "docs/**/*.rst", "docs/**/*.txt",
    "requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg",
    "Pipfile", "Pipfile.lock", "poetry.lock",
    "package.json", "package-lock.json",
    "Gemfile", "Gemfile.lock", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock",
    "environment.yml", "environment.yaml",
)
# Listed only (path + size), never inlined: enough for the agent to know code
# and tests exist without blowing up the prompt on a large tree.
LISTING_PATTERNS = (
    "src/**/*.py", "src/**/*.js", "src/**/*.ts",
    "app/**/*.py",
    "tests/**/*.py", "test/**/*.py",
)


@dataclass
class DiscoveredFile:
    path: str  # relative to the repository root
    size: int
    inline: bool  # whether render() inlines full content or just lists it


@dataclass
class DiscoveredContext:
    root: str
    files: list[DiscoveredFile] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    total_bytes: int = 0


class RepositoryContextLoader:
    """Discovers and renders a project's own repository as RESEARCH context.

    `allowed_roots` is required (mirrors every other place a repository path
    is trusted in this app -- `app/security.validate_repository_path`,
    `app/prompts/assembler.resolve_context_files`): a research session must
    never be a way to read a path outside the configured project roots.
    """

    def __init__(self, allowed_roots: tuple[str, ...]):
        if not allowed_roots:
            raise ValueError("RepositoryContextLoader requires at least one allowed root")
        self._allowed_roots = allowed_roots

    def discover(
        self,
        repo_root: str,
        extra_patterns: list[str] | None = None,
        max_files: int = MAX_FILES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
    ) -> DiscoveredContext:
        real_root = validate_repository_path(repo_root, self._allowed_roots)
        files: list[DiscoveredFile] = []
        skipped: list[str] = []
        seen: set[str] = set()
        state = {"total": 0}

        def add(pattern: str, inline: bool) -> None:
            pattern = pattern.strip()
            if not pattern:
                return
            if os.path.isabs(pattern) or ".." in pattern.replace("\\", "/").split("/"):
                skipped.append(f"{pattern} (must be relative to the repository)")
                return
            matches = sorted(glob.glob(os.path.join(glob.escape(real_root), pattern), recursive=True))
            for match in matches:
                real = os.path.realpath(match)
                match_rel = os.path.relpath(match, real_root)
                if not real.startswith(real_root + os.sep) or not os.path.isfile(real):
                    # A symlink (or anything else) that resolves outside the
                    # repository root is rejected, not silently skipped. The
                    # message names the symlink's own path, not the target it
                    # escapes to.
                    if os.path.islink(match) or os.path.isfile(real):
                        skipped.append(f"{match_rel} (outside the repository)")
                    continue
                rel = os.path.relpath(real, real_root)
                if rel in seen:
                    continue
                if len(files) >= max_files:
                    skipped.append(f"{rel} (over the {max_files}-file limit)")
                    continue
                size = os.path.getsize(real)
                if state["total"] + size > max_total_bytes:
                    skipped.append(
                        f"{rel} (over the {max_total_bytes // (1024 * 1024)}MB total size limit)"
                    )
                    continue
                seen.add(rel)
                state["total"] += size
                files.append(DiscoveredFile(path=rel, size=size, inline=inline))

        for pattern in INLINE_PATTERNS:
            add(pattern, True)
        for pattern in LISTING_PATTERNS:
            add(pattern, False)
        for pattern in extra_patterns or []:
            add(pattern, True)

        return DiscoveredContext(root=real_root, files=files, skipped=skipped, total_bytes=state["total"])

    def render(self, discovered: DiscoveredContext, max_inline_files: int = MAX_INLINE_FILES) -> str:
        """Text ready to drop into a prompt: full content for the first
        `max_inline_files` inlineable documents, then a path+size listing for
        everything else discovered."""
        inline = [f for f in discovered.files if f.inline][:max_inline_files]
        listing = [f for f in discovered.files if not f.inline] + [
            f for f in discovered.files if f.inline
        ][max_inline_files:]
        parts = []
        if inline:
            blocks = [self._file_block(discovered.root, f.path) for f in inline]
            parts.append("Repository documents:\n\n" + "\n\n".join(blocks))
        if listing:
            lines = "\n".join(f"- {f.path} ({f.size} bytes)" for f in listing)
            parts.append(f"Other repository files (not inlined; {len(listing)} total):\n{lines}")
        return "\n\n".join(parts)

    @staticmethod
    def _file_block(root: str, rel: str) -> str:
        with open(os.path.join(root, rel), "rb") as fh:
            data = fh.read(MAX_INLINE_BYTES + 1)
        truncated = len(data) > MAX_INLINE_BYTES
        text = data[:MAX_INLINE_BYTES].decode("utf-8", "replace")
        return f"File: {rel}{' (truncated)' if truncated else ''}\n```\n{text.rstrip()}\n```"
