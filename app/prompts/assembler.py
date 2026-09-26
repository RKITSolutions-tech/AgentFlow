"""Effective-prompt assembly (docs/PHASE2_PLANNING.md §2: AgentFlow-side, always).

Adapters only ever see the finished string. Assembly order:

1. resolve the template chain (a child overrides its base),
2. body, then fragments in order,
3. `${...}` variables (missing ones reported, or raised by a strict resolver),
4. `@path` mentions and explicit context globs -> file contents, path-checked,
5. optionally the enabled Ralph instruction blocks.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Callable

from app.projects import models as project_models
from app.prompts import models
from app.prompts.models import LibraryError

MAX_CONTEXT_FILES = 20
DISCOVERED_CONTEXT_FILES = ("CLAUDE.md", "AGENTS.md")
MAX_FILE_BYTES = 64 * 1024
_VARIABLE = re.compile(r"\$\{([^}]+)\}")
# `@src/app.py` but not `user@example.com` (a word character before the @).
_MENTION = re.compile(r"(?<![\w@])@([\w][\w./\-]*)")


@dataclass
class Assembled:
    text: str
    template_id: int | None = None
    template_name: str = ""
    resolved_files: list[str] = field(default_factory=list)
    blocks_included: list[str] = field(default_factory=list)
    fragments_included: list[str] = field(default_factory=list)
    variables_substituted: dict = field(default_factory=dict)
    missing_variables: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)

    def metadata(self) -> dict:
        return {
            "template": self.template_name, "resolved_files": self.resolved_files,
            "blocks_included": self.blocks_included, "fragments_included": self.fragments_included,
            "variables_substituted": self.variables_substituted, "missing_variables": self.missing_variables,
            "skipped_files": self.skipped_files,
        }


def template_chain(db, template: models.PromptTemplate) -> list[models.PromptTemplate]:
    """Base-most first. Loops are rejected when saved, but a hand-edited
    database must not hang the assembler."""
    chain, seen = [], set()
    cursor: models.PromptTemplate | None = template
    while cursor is not None and cursor.id not in seen:
        seen.add(cursor.id)
        chain.append(cursor)
        cursor = models.get_template(db, cursor.base_template_id) if cursor.base_template_id else None
    return list(reversed(chain))


def flatten_template(db, template: models.PromptTemplate, project_id: int | None = None) -> tuple[str, list[models.PromptFragment]]:
    """Inheritance: the most-derived non-empty body wins; fragments accumulate
    base-first. Fragment names are unique, so "same name" means the same
    fragment: one already contributed by a base keeps its base position and is
    not repeated; anything new is appended."""
    body, ordered = "", []
    overrides = models.get_overrides(db, project_id, "template") if project_id else {}
    for t in template_chain(db, template):
        text = overrides.get(t.id, {}).get("content") or t.body  # a project's own body wins
        if text.strip():
            body = text
        for fid in t.fragments:
            fragment = models.get_fragment(db, fid)
            if fragment is None:
                continue
            for i, existing in enumerate(ordered):
                if existing.name == fragment.name:
                    ordered[i] = fragment
                    break
            else:
                ordered.append(fragment)
    return body, ordered


def substitute(text: str, variables: dict, resolver: Callable[[str], str] | None = None) -> tuple[str, dict, list[str]]:
    """Replace `${name}`. With a `resolver` (the engine's strict one) its errors
    propagate; without, an unknown name is left in place and reported."""
    used, missing = {}, []

    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        if resolver is not None:
            value = resolver(match.group(0))
            used[key] = value
            return value
        if key in variables:
            used[key] = str(variables[key])
            return used[key]
        if key not in missing:
            missing.append(key)
        return match.group(0)

    return _VARIABLE.sub(repl, text), used, missing


def resolve_context_files(patterns: list[str], root: str, allowed_roots: tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """Expand globs under `root`; returns (relative paths, skipped with reason).

    Only regular files whose real path stays inside `root` (symlinks pointing
    out are skipped) are returned, and `root` itself must sit under one of
    `allowed_roots` when given: a prompt must never be a way to read outside a
    project."""
    real_root = os.path.realpath(root)
    if allowed_roots and not any(
        real_root == os.path.realpath(a) or real_root.startswith(os.path.realpath(a) + os.sep) for a in allowed_roots
    ):
        raise LibraryError("The repository is outside the allowed project roots")
    found, skipped = [], []
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        if os.path.isabs(pattern) or ".." in pattern.replace("\\", "/").split("/"):
            skipped.append(f"{pattern} (must be relative to the repository)")
            continue
        matches = sorted(glob.glob(os.path.join(glob.escape(real_root), pattern), recursive=True))
        if not matches:
            skipped.append(f"{pattern} (no match)")
        for path in matches:
            real = os.path.realpath(path)
            if not real.startswith(real_root + os.sep) or not os.path.isfile(real):
                if os.path.isfile(real) or os.path.islink(path):
                    skipped.append(f"{os.path.relpath(path, real_root)} (outside the repository)")
                continue
            rel = os.path.relpath(real, real_root)
            if rel not in found:
                found.append(rel)
    if len(found) > MAX_CONTEXT_FILES:
        skipped.extend(f"{p} (over the {MAX_CONTEXT_FILES}-file limit)" for p in found[MAX_CONTEXT_FILES:])
        found = found[:MAX_CONTEXT_FILES]
    return found, skipped


def _file_block(root: str, rel: str) -> str:
    with open(os.path.join(os.path.realpath(root), rel), "rb") as fh:
        data = fh.read(MAX_FILE_BYTES + 1)
    truncated = len(data) > MAX_FILE_BYTES
    text = data[:MAX_FILE_BYTES].decode("utf-8", "replace")
    return f"File: {rel}{' (truncated)' if truncated else ''}\n```\n{text.rstrip()}\n```"


def project_context(db, project_id: int, allowed_roots: tuple[str, ...] = ()) -> tuple[str | None, list[str], list[str]]:
    """(root, files, skipped) for the project's context files, resolved at its primary
    repository root. The `context_files` setting (one glob per line) wins; blank means
    discovery: `CLAUDE.md`, else `AGENTS.md`, if present."""
    project = project_models.get_project(db, project_id)
    repo = None
    if project is not None:
        repo = next((r for r in project.repositories if r.is_primary), None) or (
            project.repositories[0] if project.repositories else None)
    if repo is None or not os.path.isdir(repo.path):
        return None, [], []
    globs = [g.strip() for g in project.context_files.splitlines() if g.strip()]
    if not globs:
        globs = next(([n] for n in DISCOVERED_CONTEXT_FILES if os.path.isfile(os.path.join(repo.path, n))), [])
    files, skipped = resolve_context_files(globs, repo.path, allowed_roots) if globs else ([], [])
    return repo.path, files, skipped


def context_section(root: str, files: list[str], heading: str = "Project context files") -> str:
    return f"{heading}:\n\n" + "\n\n".join(_file_block(root, rel) for rel in files) if files else ""


def include_ralph_instructions(blocks: list[models.RalphInstructionBlock], agent_type: str | None = None) -> tuple[str, list[str]]:
    """Enabled blocks in position order, restricted to `applies_to` when a block
    names agent types (empty = every agent)."""
    chosen = [
        b for b in blocks
        if b.enabled and (not b.applies_to or (agent_type or "").lower() in b.applies_to)
    ]
    text = "Instructions:\n" + "\n".join(f"- {b.content}" for b in chosen) if chosen else ""
    return text, [b.name for b in chosen]


def assemble_effective_prompt(
    db, template: models.PromptTemplate | str | int | None = None, text: str = "",
    variables: dict | None = None, root: str | None = None, allowed_roots: tuple[str, ...] = (),
    context_globs: list[str] | None = None, resolver: Callable[[str], str] | None = None,
    project_id: int | None = None,
) -> Assembled:
    """Build the prompt. `template` may be a row, an id or a name; `text` is a
    literal prompt used when there is no template (or appended to one)."""
    if isinstance(template, (int, str)):
        found = models.get_template(db, template) if isinstance(template, int) else models.get_template_by_name(db, template)
        if found is None:
            raise LibraryError(f"Unknown prompt template {template!r}")
        template = found
    parts, fragments = [], []
    if template is not None:
        body, fragments = flatten_template(db, template, project_id)
        if body.strip():
            parts.append(body.strip())
        parts.extend(f.content.strip() for f in fragments)
    if text.strip():
        parts.append(text.strip())
    if not parts:
        raise LibraryError("Nothing to assemble: give a template or text")
    merged = "\n\n".join(parts)
    # Mentions come from the library text, not from substituted variables: task
    # text is user/agent input and must not be able to pull files into a prompt.
    mentions = _MENTION.findall(merged)
    defaults = dict(template.variables) if template is not None else {}
    merged, used, missing = substitute(merged, {**defaults, **(variables or {})}, resolver)

    files, skipped = [], []
    if root:
        wanted = list(context_globs or [])
        found_mentions, _ = resolve_context_files(mentions, root, allowed_roots) if mentions else ([], [])
        files, skipped = resolve_context_files(wanted, root, allowed_roots) if wanted else ([], [])
        for rel in found_mentions:
            if rel not in files:
                files.append(rel)
        files = files[:MAX_CONTEXT_FILES]
        if files:
            merged += "\n\nContext files:\n\n" + "\n\n".join(_file_block(root, rel) for rel in files)
    if project_id is not None:
        proot, pfiles, pskipped = project_context(db, project_id, allowed_roots)
        already = {os.path.realpath(os.path.join(root, f)) for f in files} if root else set()
        pfiles = [f for f in pfiles if os.path.realpath(os.path.join(proot, f)) not in already]
        if pfiles:
            merged += "\n\n" + context_section(proot, pfiles)
        files = files + [f for f in pfiles if f not in files]
        skipped = skipped + pskipped
    return Assembled(
        text=merged, template_id=template.id if template else None, template_name=template.name if template else "",
        resolved_files=files, fragments_included=[f.name for f in fragments],
        variables_substituted=used, missing_variables=missing, skipped_files=skipped,
    )
