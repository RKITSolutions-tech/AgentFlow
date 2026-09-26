"""Prompt library persistence (fragments, templates, Ralph instruction blocks,
recorded effective prompts). Plain sqlite3 like the rest of the app."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from app.prompts.defaults import DEFAULT_INSTRUCTIONS, DEFAULT_TEMPLATES
from app.runs.models import now

CATEGORIES = ("instruction", "context", "example")
NAME_MAX = 80
CONTENT_MAX = 20_000


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
    return PromptFragment(**d)


def create_fragment(db, name: str, content: str, category: str = "instruction", tags=None) -> int:
    if category not in CATEGORIES:
        raise LibraryError(f"Category must be one of {', '.join(CATEGORIES)}")
    stamp = now()
    try:
        cur = db.execute(
            "INSERT INTO prompt_fragments (name, category, content, tags, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_name(name), category, _content(content), json.dumps(_tags(tags)), stamp, stamp),
        )
    except sqlite3.IntegrityError:
        raise LibraryError(f"A fragment named {name.strip()!r} already exists") from None
    db.execute(
        "INSERT INTO prompt_fragment_versions (fragment_id, version, content, created_at) VALUES (?, 1, ?, ?)",
        (cur.lastrowid, _content(content), stamp),
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


def update_fragment(db, fragment_id: int, name=None, content=None, category=None, tags=None) -> PromptFragment:
    """A content change makes a new version (history is kept); other edits do not."""
    current = get_fragment(db, fragment_id)
    if current is None:
        raise LookupError(f"Fragment {fragment_id} not found")
    if category is not None and category not in CATEGORIES:
        raise LibraryError(f"Category must be one of {', '.join(CATEGORIES)}")
    new_content = current.content if content is None else _content(content)
    version = current.version + (1 if new_content != current.content else 0)
    stamp = now()
    try:
        db.execute(
            "UPDATE prompt_fragments SET name = ?, category = ?, content = ?, version = ?, tags = ?, "
            "updated_at = ? WHERE id = ?",
            (
                current.name if name is None else _name(name), category or current.category, new_content,
                version, json.dumps(current.tags if tags is None else _tags(tags)), stamp, fragment_id,
            ),
        )
    except sqlite3.IntegrityError:
        raise LibraryError(f"A fragment named {name.strip()!r} already exists") from None
    if version != current.version:
        db.execute(
            "INSERT INTO prompt_fragment_versions (fragment_id, version, content, created_at) VALUES (?, ?, ?, ?)",
            (fragment_id, version, new_content, stamp),
        )
    db.commit()
    return get_fragment(db, fragment_id)


def fragment_versions(db, fragment_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT version, content, created_at FROM prompt_fragment_versions WHERE fragment_id = ? "
        "ORDER BY version DESC", (fragment_id,))]


def templates_using_fragment(db, fragment_id: int) -> list[PromptTemplate]:
    return [t for t in list_templates(db) if fragment_id in t.fragments]


def delete_fragment(db, fragment_id: int) -> None:
    users = templates_using_fragment(db, fragment_id)
    if users:
        raise LibraryError("In use by template(s): " + ", ".join(t.name for t in users))
    db.execute("DELETE FROM prompt_fragments WHERE id = ?", (fragment_id,))
    db.commit()


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
    context_files=None, variables_used=None, blocks_included=None, redacted=False,
) -> int:
    cur = db.execute(
        "INSERT INTO execution_prompts (source_type, source_id, template_id, template_name, effective_prompt, "
        "context_files, variables_used, blocks_included, redacted, assembled_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (source_type, source_id, template_id, template_name, effective_prompt, json.dumps(context_files or []),
         json.dumps(variables_used or {}), json.dumps(blocks_included or []), int(redacted), now()),
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
    d["redacted"] = bool(d["redacted"])
    return ExecutionPrompt(**d)


# -- seeding (migration of the previously hard-coded prompts) ---------------------------------------


def seed_defaults(db) -> None:
    """Idempotent: adds the built-in templates and Ralph instruction blocks that
    used to be Python constants. A template is re-added if its name is missing;
    the block set is seeded only into an empty table, so a person can disable or
    reorder blocks (or delete them all) without them returning. Existing rows,
    edited or not, are never overwritten."""
    for name, body in DEFAULT_TEMPLATES.items():
        if get_template_by_name(db, name) is None:
            create_template(db, name, body=body, description="Built-in AGENT step prompt")
    if not list_blocks(db):
        for name, content in DEFAULT_INSTRUCTIONS:
            create_block(db, name, content, description="Standard Ralph instruction")
