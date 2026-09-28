"""Prompt library persistence (fragments, templates, Ralph instruction blocks,
recorded effective prompts). Plain sqlite3 like the rest of the app."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from app.agents.base import AGENT_ROLES
from app.prompts.defaults import (
    DEFAULT_INSTRUCTIONS,
    DEFAULT_SKILLS,
    DEFAULT_TEMPLATE_DESCRIPTIONS,
    DEFAULT_TEMPLATES,
)
from app.runs.models import now

CATEGORIES = ("instruction", "context", "example")
NAME_MAX = 80
CONTENT_MAX = 20_000
SKILL_STATUSES = ("draft", "active", "deprecated")


class LibraryError(ValueError):
    """A library rule was broken (duplicate name, in-use delete, inheritance loop)."""


@dataclass
class PromptFragment:
    id: int
    name: str
    category: str
    content: str
    version: int
    tags: list[str]
    created_at: str
    updated_at: str
    # Skill fields (docs/AGENT_ADAPTER.md §23.2/23.3): a skill is a fragment with
    # skill_status set to 'draft'/'active'/'deprecated'; '' means "not a skill".
    skill_status: str = ""
    skill_when_to_use: str = ""
    skill_constraints: list[str] = field(default_factory=list)
    skill_depends_on: list[int] = field(default_factory=list)
    skill_roles: list[str] = field(default_factory=list)
    skill_adapter_types: list[str] = field(default_factory=list)
    skill_priority: int = 0
    skill_author: str = ""
    # Review cadence (docs/AGENT_ADAPTER.md §23.4): set by `mark_skill_reviewed`,
    # independent of `version`/`prompt_fragment_versions` -- a review with no
    # content change leaves those untouched.
    skill_last_reviewed_at: str = ""
    skill_reviewed_by: str = ""

    @property
    def is_skill(self) -> bool:
        return bool(self.skill_status)


@dataclass
class PromptTemplate:
    id: int
    name: str
    description: str
    body: str
    fragments: list[int]
    variables: dict
    base_template_id: int | None
    created_at: str
    updated_at: str


@dataclass
class RalphInstructionBlock:
    id: int
    name: str
    description: str
    content: str
    enabled: bool
    position: int
    version: int
    applies_to: list[str]
    created_at: str
    updated_at: str


@dataclass
class ExecutionPrompt:
    id: int
    source_type: str
    source_id: int
    template_id: int | None
    template_name: str
    effective_prompt: str
    context_files: list[str]
    variables_used: dict
    blocks_included: list[str]
    redacted: bool
    assembled_at: str
    skills_included: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def _name(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise LibraryError("A name is required")
    if len(value) > NAME_MAX:
        raise LibraryError(f"Names are limited to {NAME_MAX} characters")
    return value


def _content(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise LibraryError("Content is required")
    if len(value) > CONTENT_MAX:
        raise LibraryError(f"Content is limited to {CONTENT_MAX} characters")
    return value


def _tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = tags.split(",")
    seen = []
    for t in tags or []:
        t = str(t).strip().lower()
        if t and t not in seen:
            seen.append(t)
    return seen[:20]


# -- fragments ---------------------------------------------------------------------------


def _fragment(row: sqlite3.Row) -> PromptFragment:
    d = dict(row)
    d["tags"] = json.loads(d["tags"])
    d["skill_constraints"] = json.loads(d["skill_constraints"])
    d["skill_depends_on"] = json.loads(d["skill_depends_on"])
    d["skill_roles"] = json.loads(d["skill_roles"])
    d["skill_adapter_types"] = json.loads(d["skill_adapter_types"])
    return PromptFragment(**d)


def _skill_status(value: str | None) -> str:
    value = (value or "").strip().lower()
    if value and value not in SKILL_STATUSES:
        raise LibraryError(f"Skill status must be one of {', '.join(SKILL_STATUSES)}")
    return value


def _check_skill_dependencies(db, fragment_id: int | None, depends_on: list[int]) -> None:
    """Every dependency must exist and be a skill; no direct or indirect cycle."""
    for dep_id in depends_on:
        dep = get_fragment(db, dep_id)
        if dep is None or not dep.is_skill:
            raise LibraryError(f"Skill dependency {dep_id} does not exist")

    def walk(current_id: int, seen: set[int]) -> None:
        if current_id in seen:
            raise LibraryError("A skill cannot depend on itself, directly or indirectly")
        seen = seen | {current_id}
        current = get_fragment(db, current_id)
        for dep_id in (current.skill_depends_on if current else []):
            walk(dep_id, seen)

    start = {fragment_id} if fragment_id else set()
    for dep_id in depends_on:
        walk(dep_id, start)


def create_fragment(
    db, name: str, content: str, category: str = "instruction", tags=None, *,
    skill_status: str | None = None, skill_when_to_use: str = "", skill_constraints=None,
    skill_depends_on=None, skill_roles=None, skill_adapter_types=None, skill_priority: int = 0,
    skill_author: str = "", changelog: str = "",
) -> int:
    if category not in CATEGORIES:
        raise LibraryError(f"Category must be one of {', '.join(CATEGORIES)}")
    status = _skill_status(skill_status)
    depends_on = [int(d) for d in skill_depends_on or []]
    if status:
        _check_skill_dependencies(db, None, depends_on)
    stamp = now()
    try:
        cur = db.execute(
            "INSERT INTO prompt_fragments (name, category, content, tags, created_at, updated_at, "
            "skill_status, skill_when_to_use, skill_constraints, skill_depends_on, skill_roles, "
            "skill_adapter_types, skill_priority, skill_author) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _name(name), category, _content(content), json.dumps(_tags(tags)), stamp, stamp,
                status, (skill_when_to_use or "").strip(), json.dumps(list(skill_constraints or [])),
                json.dumps(depends_on), json.dumps(_tags(skill_roles)), json.dumps(_tags(skill_adapter_types)),
                int(skill_priority), (skill_author or "").strip(),
            ),
        )
    except sqlite3.IntegrityError:
        raise LibraryError(f"A fragment named {name.strip()!r} already exists") from None
    db.execute(
        "INSERT INTO prompt_fragment_versions (fragment_id, version, content, created_at, changelog) "
        "VALUES (?, 1, ?, ?, ?)",
        (cur.lastrowid, _content(content), stamp, (changelog or "").strip()),
    )
    db.commit()
    return cur.lastrowid


def get_fragment(db, fragment_id: int) -> PromptFragment | None:
    row = db.execute("SELECT * FROM prompt_fragments WHERE id = ?", (fragment_id,)).fetchone()
    return _fragment(row) if row else None


def get_fragment_by_name(db, name: str) -> PromptFragment | None:
    row = db.execute("SELECT * FROM prompt_fragments WHERE name = ?", (name,)).fetchone()
    return _fragment(row) if row else None


def list_fragments(db, category: str | None = None) -> list[PromptFragment]:
    sql, params = "SELECT * FROM prompt_fragments", []
    if category:
        sql, params = sql + " WHERE category = ?", [category]
    return [_fragment(r) for r in db.execute(sql + " ORDER BY category, name", params)]


def update_fragment(
    db, fragment_id: int, name=None, content=None, category=None, tags=None, *,
    skill_status=-1, skill_when_to_use=None, skill_constraints=None, skill_depends_on=None,
    skill_roles=None, skill_adapter_types=None, skill_priority=None, skill_author=None, changelog: str = "",
) -> PromptFragment:
    """A content change makes a new version (history is kept); other edits do not.
    `skill_status=-1` (the sentinel) leaves the skill status unchanged."""
    current = get_fragment(db, fragment_id)
    if current is None:
        raise LookupError(f"Fragment {fragment_id} not found")
    if category is not None and category not in CATEGORIES:
        raise LibraryError(f"Category must be one of {', '.join(CATEGORIES)}")
    new_content = current.content if content is None else _content(content)
    version = current.version + (1 if new_content != current.content else 0)
    new_status = current.skill_status if skill_status == -1 else _skill_status(skill_status)
    new_depends_on = current.skill_depends_on if skill_depends_on is None else [int(d) for d in skill_depends_on]
    if new_status:
        _check_skill_dependencies(db, fragment_id, new_depends_on)
    stamp = now()
    try:
        db.execute(
            "UPDATE prompt_fragments SET name = ?, category = ?, content = ?, version = ?, tags = ?, "
            "updated_at = ?, skill_status = ?, skill_when_to_use = ?, skill_constraints = ?, "
            "skill_depends_on = ?, skill_roles = ?, skill_adapter_types = ?, skill_priority = ?, "
            "skill_author = ? WHERE id = ?",
            (
                current.name if name is None else _name(name), category or current.category, new_content,
                version, json.dumps(current.tags if tags is None else _tags(tags)), stamp,
                new_status,
                current.skill_when_to_use if skill_when_to_use is None else skill_when_to_use.strip(),
                json.dumps(current.skill_constraints if skill_constraints is None else list(skill_constraints)),
                json.dumps(new_depends_on),
                json.dumps(current.skill_roles if skill_roles is None else _tags(skill_roles)),
                json.dumps(current.skill_adapter_types if skill_adapter_types is None else _tags(skill_adapter_types)),
                current.skill_priority if skill_priority is None else int(skill_priority),
                current.skill_author if skill_author is None else skill_author.strip(),
                fragment_id,
            ),
        )
    except sqlite3.IntegrityError:
        raise LibraryError(f"A fragment named {name.strip()!r} already exists") from None
    if version != current.version:
        db.execute(
            "INSERT INTO prompt_fragment_versions (fragment_id, version, content, created_at, changelog) "
            "VALUES (?, ?, ?, ?, ?)",
            (fragment_id, version, new_content, stamp, (changelog or "").strip()),
        )
    db.commit()
    return get_fragment(db, fragment_id)


def fragment_versions(db, fragment_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT version, content, created_at, changelog FROM prompt_fragment_versions WHERE fragment_id = ? "
        "ORDER BY version DESC", (fragment_id,))]


def templates_using_fragment(db, fragment_id: int) -> list[PromptTemplate]:
    return [t for t in list_templates(db) if fragment_id in t.fragments]


def skills_depending_on(db, fragment_id: int) -> list[PromptFragment]:
    return [s for s in list_skills(db) if fragment_id in s.skill_depends_on]


def delete_fragment(db, fragment_id: int) -> None:
    users = templates_using_fragment(db, fragment_id)
    if users:
        raise LibraryError("In use by template(s): " + ", ".join(t.name for t in users))
    dependents = skills_depending_on(db, fragment_id)
    if dependents:
        raise LibraryError("Depended on by skill(s): " + ", ".join(s.name for s in dependents))
    db.execute("DELETE FROM prompt_fragments WHERE id = ?", (fragment_id,))
    db.commit()


# -- skills --------------------------------------------------------------------------------
# A skill is a prompt fragment with `skill_status` set. See docs/AGENT_ADAPTER.md §23.2/23.3
# for why this extends the fragment model instead of a parallel one.


def list_skills(db, role: str | None = None, adapter_type: str | None = None, status: str | None = None) -> list[PromptFragment]:
    skills = [f for f in list_fragments(db) if f.is_skill]
    if status:
        skills = [s for s in skills if s.skill_status == status]
    if role:
        role = role.lower()
        skills = [s for s in skills if not s.skill_roles or role in s.skill_roles]
    if adapter_type:
        adapter_type = adapter_type.lower()
        skills = [s for s in skills if not s.skill_adapter_types or adapter_type in s.skill_adapter_types]
    return skills


def resolve_skill_order(skills: list[PromptFragment]) -> list[PromptFragment]:
    """Dependencies before dependents (topological sort), ties broken by priority
    (higher first) then name. Cycle-safe: a hand-edited database must not hang
    assembly, so an already-seen id is simply not revisited."""
    by_id = {s.id: s for s in skills}
    ordered: list[PromptFragment] = []
    seen: set[int] = set()

    def visit(skill: PromptFragment, trail: set[int]) -> None:
        if skill.id in seen or skill.id in trail:
            return
        trail = trail | {skill.id}
        for dep_id in skill.skill_depends_on:
            dep = by_id.get(dep_id)
            if dep is not None:
                visit(dep, trail)
        if skill.id not in seen:
            seen.add(skill.id)
            ordered.append(skill)

    for skill in sorted(skills, key=lambda s: (-s.skill_priority, s.name)):
        visit(skill, set())
    return ordered


def deprecate_skill(db, fragment_id: int) -> PromptFragment:
    fragment = get_fragment(db, fragment_id)
    if fragment is None or not fragment.is_skill:
        raise LibraryError("Not a skill")
    return update_fragment(db, fragment_id, skill_status="deprecated")


def mark_skill_reviewed(db, fragment_id: int, reviewer: str) -> PromptFragment:
    """Record that a person reviewed this skill and judged its content still
    correct, without necessarily changing it (docs/AGENT_ADAPTER.md §23.4).
    `prompt_fragment_versions` already captures *changes* with a changelog;
    this covers the "looked at it, still fine" case that leaves no change
    behind. Advisory only -- `reviewer` is free text, not tied to any account
    (this app has no auth/admin concept, §23.3)."""
    fragment = get_fragment(db, fragment_id)
    if fragment is None or not fragment.is_skill:
        raise LibraryError("Not a skill")
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise LibraryError("A reviewer name is required")
    db.execute(
        "UPDATE prompt_fragments SET skill_last_reviewed_at = ?, skill_reviewed_by = ? WHERE id = ?",
        (now(), reviewer, fragment_id),
    )
    db.commit()
    return get_fragment(db, fragment_id)


def validate_skill(db, fragment_id: int) -> dict:
    """Schema/dependency/role checks for the "validate" action. Errors mean the
    skill is broken (shouldn't happen via the normal write path, but a
    hand-edited database must be checkable); warnings are advisory."""
    fragment = get_fragment(db, fragment_id)
    errors, warnings = [], []
    if fragment is None or not fragment.is_skill:
        return {"ok": False, "errors": ["Not a skill"], "warnings": []}
    for dep_id in fragment.skill_depends_on:
        dep = get_fragment(db, dep_id)
        if dep is None or not dep.is_skill:
            errors.append(f"Dependency {dep_id} does not exist")
    try:
        _check_skill_dependencies(db, fragment_id, fragment.skill_depends_on)
    except LibraryError as exc:
        errors.append(str(exc))
    for role in fragment.skill_roles:
        if role.upper() not in AGENT_ROLES:
            warnings.append(f"Role {role!r} is not one of {', '.join(AGENT_ROLES)}")
    if not fragment.skill_when_to_use.strip():
        warnings.append("No 'when to use' guidance set")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


# -- templates ---------------------------------------------------------------------------


def _template(row: sqlite3.Row) -> PromptTemplate:
    d = dict(row)
    d["fragments"] = json.loads(d["fragments"])
    d["variables"] = json.loads(d["variables"])
    return PromptTemplate(**d)


def _check_template(db, template_id: int | None, fragments: list[int], base_id: int | None, body: str) -> None:
    for fid in fragments:
        if get_fragment(db, fid) is None:
            raise LibraryError(f"Fragment {fid} does not exist")
    if base_id is not None:
        seen = {template_id} if template_id else set()
        cursor = base_id
        while cursor is not None:
            if cursor in seen:
                raise LibraryError("A template cannot inherit from itself, directly or indirectly")
            seen.add(cursor)
            base = get_template(db, cursor)
            if base is None:
                raise LibraryError(f"Base template {cursor} does not exist")
            cursor = base.base_template_id
    if not body.strip() and not fragments and base_id is None:
        raise LibraryError("A template needs a body, fragments or a base template")


def create_template(db, name, body="", fragments=None, description="", variables=None, base_template_id=None) -> int:
    fragments = [int(f) for f in fragments or []]
    body = (body or "").strip()
    if len(body) > CONTENT_MAX:
        raise LibraryError(f"Body is limited to {CONTENT_MAX} characters")
    _check_template(db, None, fragments, base_template_id, body)
    stamp = now()
    try:
        cur = db.execute(
            "INSERT INTO prompt_templates (name, description, body, fragments, variables, base_template_id, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_name(name), (description or "").strip(), body, json.dumps(fragments),
             json.dumps(variables or {}), base_template_id, stamp, stamp),
        )
    except sqlite3.IntegrityError:
        raise LibraryError(f"A template named {name.strip()!r} already exists") from None
    db.commit()
    return cur.lastrowid


def get_template(db, template_id: int) -> PromptTemplate | None:
    row = db.execute("SELECT * FROM prompt_templates WHERE id = ?", (template_id,)).fetchone()
    return _template(row) if row else None


def get_template_by_name(db, name: str) -> PromptTemplate | None:
    row = db.execute("SELECT * FROM prompt_templates WHERE name = ?", (name,)).fetchone()
    return _template(row) if row else None


def list_templates(db) -> list[PromptTemplate]:
    return [_template(r) for r in db.execute("SELECT * FROM prompt_templates ORDER BY name")]


def update_template(db, template_id, name=None, body=None, fragments=None, description=None, variables=None, base_template_id=-1) -> PromptTemplate:
    """`base_template_id=-1` leaves the base unchanged (None clears it)."""
    cur = get_template(db, template_id)
    if cur is None:
        raise LookupError(f"Template {template_id} not found")
    new_fragments = cur.fragments if fragments is None else [int(f) for f in fragments]
    new_body = cur.body if body is None else body.strip()
    new_base = cur.base_template_id if base_template_id == -1 else base_template_id
    _check_template(db, template_id, new_fragments, new_base, new_body)
    try:
        db.execute(
            "UPDATE prompt_templates SET name = ?, description = ?, body = ?, fragments = ?, variables = ?, "
            "base_template_id = ?, updated_at = ? WHERE id = ?",
            (
                cur.name if name is None else _name(name),
                cur.description if description is None else description.strip(), new_body,
                json.dumps(new_fragments), json.dumps(cur.variables if variables is None else variables),
                new_base, now(), template_id,
            ),
        )
    except sqlite3.IntegrityError:
        raise LibraryError(f"A template named {name.strip()!r} already exists") from None
    db.commit()
    return get_template(db, template_id)


def delete_template(db, template_id: int) -> None:
    children = [t.name for t in list_templates(db) if t.base_template_id == template_id]
    if children:
        raise LibraryError("Used as the base of: " + ", ".join(children))
    db.execute("DELETE FROM prompt_templates WHERE id = ?", (template_id,))
    db.commit()


# -- Ralph instruction blocks ---------------------------------------------------------------


def _block(row: sqlite3.Row) -> RalphInstructionBlock:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    d["applies_to"] = json.loads(d["applies_to"])
    return RalphInstructionBlock(**d)


def create_block(db, name, content, description="", enabled=True, applies_to=None) -> int:
    position = db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM ralph_instruction_blocks").fetchone()[0]
    stamp = now()
    try:
        cur = db.execute(
            "INSERT INTO ralph_instruction_blocks (name, description, content, enabled, position, applies_to, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_name(name), (description or "").strip(), _content(content), int(enabled), position,
             json.dumps(_tags(applies_to)), stamp, stamp),
        )
    except sqlite3.IntegrityError:
        raise LibraryError(f"A block named {name.strip()!r} already exists") from None
    db.commit()
    return cur.lastrowid


def get_block(db, block_id: int) -> RalphInstructionBlock | None:
    row = db.execute("SELECT * FROM ralph_instruction_blocks WHERE id = ?", (block_id,)).fetchone()
    return _block(row) if row else None


def list_blocks(db, enabled_only: bool = False) -> list[RalphInstructionBlock]:
    sql = "SELECT * FROM ralph_instruction_blocks" + (" WHERE enabled = 1" if enabled_only else "")
    return [_block(r) for r in db.execute(sql + " ORDER BY position, id")]


def update_block(db, block_id, name=None, content=None, description=None, enabled=None, applies_to=None) -> RalphInstructionBlock:
    cur = get_block(db, block_id)
    if cur is None:
        raise LookupError(f"Block {block_id} not found")
    new_content = cur.content if content is None else _content(content)
    version = cur.version + (1 if new_content != cur.content else 0)
    try:
        db.execute(
            "UPDATE ralph_instruction_blocks SET name = ?, description = ?, content = ?, enabled = ?, "
            "applies_to = ?, version = ?, updated_at = ? WHERE id = ?",
            (
                cur.name if name is None else _name(name),
                cur.description if description is None else description.strip(), new_content,
                int(cur.enabled if enabled is None else enabled),
                json.dumps(cur.applies_to if applies_to is None else _tags(applies_to)), version, now(), block_id,
            ),
        )
    except sqlite3.IntegrityError:
        raise LibraryError(f"A block named {name.strip()!r} already exists") from None
    db.commit()
    return get_block(db, block_id)


def move_block(db, block_id: int, direction: str) -> None:
    """Swap with the neighbour above/below in the current order."""
    blocks = list_blocks(db)
    ids = [b.id for b in blocks]
    if block_id not in ids or direction not in ("up", "down"):
        raise LibraryError("Cannot move that block")
    i = ids.index(block_id)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    for position, bid in enumerate(ids, start=1):
        db.execute("UPDATE ralph_instruction_blocks SET position = ? WHERE id = ?", (position, bid))
    db.commit()


def delete_block(db, block_id: int) -> None:
    db.execute("DELETE FROM ralph_instruction_blocks WHERE id = ?", (block_id,))
    db.commit()


# -- recorded effective prompts -----------------------------------------------------------------


def record_prompt(
    db, source_type: str, source_id: int, effective_prompt: str, template_id=None, template_name="",
    context_files=None, variables_used=None, blocks_included=None, redacted=False, skills_included=None,
) -> int:
    cur = db.execute(
        "INSERT INTO execution_prompts (source_type, source_id, template_id, template_name, effective_prompt, "
        "context_files, variables_used, blocks_included, redacted, assembled_at, skills_included) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (source_type, source_id, template_id, template_name, effective_prompt, json.dumps(context_files or []),
         json.dumps(variables_used or {}), json.dumps(blocks_included or []), int(redacted), now(),
         json.dumps(skills_included or [])),
    )
    db.commit()
    return cur.lastrowid


def get_execution_prompt(db, prompt_id: int) -> ExecutionPrompt | None:
    row = db.execute("SELECT * FROM execution_prompts WHERE id = ?", (prompt_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["context_files"] = json.loads(d["context_files"])
    d["variables_used"] = json.loads(d["variables_used"])
    d["blocks_included"] = json.loads(d["blocks_included"])
    d["skills_included"] = json.loads(d["skills_included"]) if d.get("skills_included") else []
    d["redacted"] = bool(d["redacted"])
    return ExecutionPrompt(**d)


# -- per-project overrides -----------------------------------------------------------------------


def set_override(db, project_id: int, kind: str, target_id: int, content: str = "", enabled: bool | None = None) -> None:
    """Override a template body (`kind='template'`, content required) or a Ralph block
    (`kind='block'`: new content and/or on/off) for one project. The global row is untouched."""
    content = (content or "").strip()
    if kind == "template":
        if get_template(db, target_id) is None:
            raise LibraryError("Unknown template")
        if not content:
            raise LibraryError("An override needs a body; use Remove override to go back to the global text")
        enabled = None
    elif kind == "block":
        if get_block(db, target_id) is None:
            raise LibraryError("Unknown block")
        if not content and enabled is None:
            raise LibraryError("Change the text or switch the block on/off")
    else:
        raise LibraryError("Overrides apply to templates and blocks")
    if len(content) > CONTENT_MAX:
        raise LibraryError(f"Content is limited to {CONTENT_MAX} characters")
    db.execute(
        "INSERT INTO project_prompt_overrides (project_id, kind, target_id, content, enabled, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, kind, target_id) DO UPDATE SET "
        "content = excluded.content, enabled = excluded.enabled, updated_at = excluded.updated_at",
        (project_id, kind, target_id, content, None if enabled is None else int(enabled), now()),
    )
    db.commit()


def clear_override(db, project_id: int, kind: str, target_id: int) -> None:
    db.execute(
        "DELETE FROM project_prompt_overrides WHERE project_id = ? AND kind = ? AND target_id = ?",
        (project_id, kind, target_id),
    )
    db.commit()


def get_overrides(db, project_id: int, kind: str) -> dict[int, dict]:
    return {
        r["target_id"]: {"content": r["content"], "enabled": None if r["enabled"] is None else bool(r["enabled"])}
        for r in db.execute(
            "SELECT target_id, content, enabled FROM project_prompt_overrides WHERE project_id = ? AND kind = ?",
            (project_id, kind),
        )
    }


def effective_blocks(db, project_id: int | None = None) -> list[RalphInstructionBlock]:
    """The global blocks with the project's overrides applied (same order)."""
    blocks = list_blocks(db)
    if project_id is None:
        return blocks
    over = get_overrides(db, project_id, "block")
    for b in blocks:
        o = over.get(b.id)
        if o:
            b.content = o["content"] or b.content
            if o["enabled"] is not None:
                b.enabled = o["enabled"]
    return blocks


# -- usage, recorded-prompt browsing, import / export ---------------------------------------------


def template_usage(db) -> dict[int, int]:
    """How many recorded prompts each template produced."""
    return {r[0]: r[1] for r in db.execute(
        "SELECT template_id, COUNT(*) FROM execution_prompts WHERE template_id IS NOT NULL GROUP BY template_id")}


def block_usage(db) -> dict[str, int]:
    """How many recorded prompts included each Ralph block (by name)."""
    return {r[0]: r[1] for r in db.execute(
        "SELECT j.value, COUNT(*) FROM execution_prompts ep, json_each(ep.blocks_included) j GROUP BY j.value")}


_RECORDED = """
SELECT ep.id, ep.source_type, ep.source_id, ep.template_name, ep.blocks_included, ep.context_files,
       ep.redacted, ep.assembled_at, pe.id AS execution_id, se.element_name AS label,
       NULL AS run_id, NULL AS iteration
FROM execution_prompts ep
JOIN step_executions se ON ep.source_type = 'pipeline_step' AND se.id = ep.source_id
JOIN pipeline_executions pe ON pe.id = se.execution_id
WHERE pe.project_id = :pid
UNION ALL
SELECT ep.id, ep.source_type, ep.source_id, ep.template_name, ep.blocks_included, ep.context_files,
       ep.redacted, ep.assembled_at, NULL, rr.title, rr.id, ri.number
FROM execution_prompts ep
JOIN ralph_iterations ri ON ep.source_type = 'ralph_iteration' AND ri.id = ep.source_id
JOIN ralph_runs rr ON rr.id = ri.run_id
WHERE rr.project_id = :pid
"""


def list_recorded(db, project_id: int, source_type: str = "", limit: int = 100) -> list[dict]:
    """Recorded prompts of a project, newest first, with where each one came from."""
    rows = db.execute(
        f"SELECT * FROM ({_RECORDED}) WHERE (:st = '' OR source_type = :st) "
        "ORDER BY assembled_at DESC, id DESC LIMIT :limit",
        {"pid": project_id, "st": source_type, "limit": limit},
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["blocks_included"] = json.loads(d["blocks_included"])
        d["context_files"] = json.loads(d["context_files"])
        out.append(d)
    return out


def get_recorded_for_project(db, project_id: int, prompt_id: int) -> dict | None:
    """One recorded prompt plus its origin, only if it belongs to the project."""
    origin = next((r for r in list_recorded(db, project_id, limit=1_000_000) if r["id"] == prompt_id), None)
    prompt = get_execution_prompt(db, prompt_id)
    if origin is None or prompt is None:
        return None
    return {"prompt": prompt, "origin": origin}


def export_library(db) -> dict:
    """The global library as portable JSON (references by name, not id). Project
    overrides and recorded prompts are not exported."""
    names = {f.id: f.name for f in list_fragments(db)}
    tmpl = {t.id: t.name for t in list_templates(db)}
    return {
        "format": "agentflow-prompt-library", "version": 1,
        "fragments": [
            {"name": f.name, "category": f.category, "content": f.content, "tags": f.tags}
            for f in list_fragments(db)
        ],
        "templates": [
            {
                "name": t.name, "description": t.description, "body": t.body, "variables": t.variables,
                "fragments": [names[i] for i in t.fragments if i in names],
                "base": tmpl.get(t.base_template_id),
            }
            for t in list_templates(db)
        ],
        "blocks": [
            {"name": b.name, "description": b.description, "content": b.content, "enabled": b.enabled,
             "applies_to": b.applies_to}
            for b in list_blocks(db)
        ],
    }


def import_library(db, data: dict, overwrite: bool = False) -> dict:
    """Merge an export into the library. Existing names are kept unless `overwrite`
    (then their content is replaced). Returns counts and a list of per-item errors;
    a bad item is reported and skipped, it does not abort the rest."""
    if not isinstance(data, dict) or data.get("format") != "agentflow-prompt-library":
        raise LibraryError("Not an AgentFlow prompt library export")
    if data.get("version") != 1:
        raise LibraryError(f"Unsupported export version {data.get('version')!r}")
    result = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

    def each(key):
        items = data.get(key) or []
        if not isinstance(items, list):
            raise LibraryError(f"'{key}' must be a list")
        return [i for i in items if isinstance(i, dict)]

    def attempt(label, fn):
        try:
            return fn()
        except LibraryError as exc:
            result["errors"].append(f"{label}: {exc}")

    for item in each("fragments"):
        name = str(item.get("name", "")).strip()
        existing = get_fragment_by_name(db, name)
        if existing is None:
            if attempt(f"fragment {name!r}", lambda: create_fragment(
                    db, name, item.get("content", ""), item.get("category", "instruction"), item.get("tags"))):
                result["created"] += 1
        elif overwrite:
            if attempt(f"fragment {name!r}", lambda: update_fragment(
                    db, existing.id, content=item.get("content"), category=item.get("category"), tags=item.get("tags"))):
                result["updated"] += 1
        else:
            result["skipped"] += 1

    pending = each("templates")
    while pending:  # a base is imported (or already present) before what inherits from it
        ready = [t for t in pending if not t.get("base") or get_template_by_name(db, t["base"]) is not None]
        if not ready:
            for t in pending:
                result["errors"].append(f"template {t.get('name')!r}: base {t.get('base')!r} is not available")
            break
        pending = [t for t in pending if t not in ready]
        for item in ready:
            name = str(item.get("name", "")).strip()
            frag_ids, missing = [], []
            for fname in item.get("fragments") or []:
                f = get_fragment_by_name(db, str(fname))
                (frag_ids if f else missing).append(f.id if f else fname)
            if missing:
                result["errors"].append(f"template {name!r}: unknown fragment(s) {', '.join(map(str, missing))}")
                continue
            base = get_template_by_name(db, item["base"]).id if item.get("base") else None
            fields = dict(body=item.get("body", ""), fragments=frag_ids, description=item.get("description", ""),
                          variables=item.get("variables") if isinstance(item.get("variables"), dict) else {})
            existing = get_template_by_name(db, name)
            if existing is None:
                if attempt(f"template {name!r}", lambda: create_template(db, name, base_template_id=base, **fields)):
                    result["created"] += 1
            elif overwrite:
                if attempt(f"template {name!r}", lambda: update_template(db, existing.id, base_template_id=base, **fields)):
                    result["updated"] += 1
            else:
                result["skipped"] += 1

    for item in each("blocks"):
        name = str(item.get("name", "")).strip()
        existing = next((b for b in list_blocks(db) if b.name == name), None)
        applies = item.get("applies_to")
        if existing is None:
            if attempt(f"block {name!r}", lambda: create_block(
                    db, name, item.get("content", ""), item.get("description", ""),
                    bool(item.get("enabled", True)), applies)):
                result["created"] += 1
        elif overwrite:
            if attempt(f"block {name!r}", lambda: update_block(
                    db, existing.id, content=item.get("content"), description=item.get("description"),
                    enabled=bool(item.get("enabled", True)), applies_to=applies)):
                result["updated"] += 1
        else:
            result["skipped"] += 1
    return result


# -- seeding (migration of the previously hard-coded prompts) ---------------------------------------


def seed_defaults(db) -> None:
    """Idempotent: adds the built-in templates and Ralph instruction blocks that
    used to be Python constants. A template is re-added if its name is missing;
    the block set is seeded only into an empty table, so a person can disable or
    reorder blocks (or delete them all) without them returning. Existing rows,
    edited or not, are never overwritten."""
    for name, body in DEFAULT_TEMPLATES.items():
        if get_template_by_name(db, name) is None:
            create_template(
                db, name, body=body,
                description=DEFAULT_TEMPLATE_DESCRIPTIONS.get(name, "Built-in AGENT step prompt"),
            )
    if not list_blocks(db):
        for name, content in DEFAULT_INSTRUCTIONS:
            create_block(db, name, content, description="Standard Ralph instruction")


def seed_builtin_skills(db) -> int:
    """Idempotent, additive-only (same contract as `seed_defaults`): a skill in
    `DEFAULT_SKILLS` whose name isn't already a fragment is created active;
    existing skills, edited or not, are never overwritten. Returns the count
    created.

    Deliberately **not** called from `seed_defaults` or wired into every
    `create_app()` the way `seed_defaults`/`seed_skills_from_disk` are --
    `tests/prompts/test_skill_assembler.py` (and others) assert an *empty*
    skill set on a freshly created app, so this is invoked separately (see
    `app/__init__.py`, guarded on `not TESTING`, and directly by
    `tests/prompts/test_skill_seed_content.py`)."""
    created = 0
    for skill in DEFAULT_SKILLS:
        if get_fragment_by_name(db, skill["name"]) is not None:
            continue
        create_fragment(
            db, skill["name"], skill["content"], "instruction",
            skill_status="active", skill_when_to_use=skill.get("when_to_use", ""),
            skill_constraints=skill.get("constraints"), skill_roles=skill.get("roles"),
            skill_adapter_types=skill.get("adapter_types"), skill_priority=skill.get("priority", 0),
            skill_author=skill.get("author", "AgentFlow"),
        )
        created += 1
    return created
