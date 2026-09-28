"""Disk mirror of the generic skills layer (docs/AGENT_ADAPTER.md §23.2/23.3).

The database is the source of truth; files under `app/agents/skills/` are a
generated, reviewable mirror plus a way to ship built-in skills as plain files
checked into the repo. Syncing skills into project repositories is explicitly
out of scope here (an open follow-up in §23.2's decision record).
"""
from __future__ import annotations

import json
import os

from app.prompts import models
from app.runs.artifacts import _safe_name

SKILLS_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents", "skills")


def _skill_dir(adapter_types: list[str]) -> str:
    return adapter_types[0] if adapter_types else "generic"


def _resolve_within_skills_root(relative: str) -> str:
    root_real = os.path.realpath(SKILLS_ROOT)
    target = os.path.realpath(os.path.join(root_real, relative))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise ValueError(f"Skill path {relative!r} escapes the skills directory")
    return target


def skill_to_dict(skill: models.PromptFragment) -> dict:
    return {
        "title": skill.name,
        "description": skill.content,
        "when_to_use": skill.skill_when_to_use,
        "constraints": skill.skill_constraints,
        "roles": skill.skill_roles,
        "adapter_types": skill.skill_adapter_types,
        "priority": skill.skill_priority,
        "author": skill.skill_author,
        "status": skill.skill_status,
        "version": skill.version,
    }


def export_skill_to_disk(skill: models.PromptFragment) -> str:
    """Write one skill as JSON under app/agents/skills/<adapter-or-generic>/<name>.json.
    Returns the path written."""
    relative = os.path.join(_skill_dir(skill.skill_adapter_types), f"{_safe_name(skill.name)}.json")
    path = _resolve_within_skills_root(relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(skill_to_dict(skill), fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def _iter_skill_files() -> list[str]:
    if not os.path.isdir(SKILLS_ROOT):
        return []
    found = []
    for dirpath, _dirnames, filenames in os.walk(SKILLS_ROOT):
        for filename in filenames:
            if filename.endswith(".json"):
                found.append(os.path.join(dirpath, filename))
    return sorted(found)


def seed_skills_from_disk(db) -> int:
    """Idempotent, additive-only (same contract as `models.seed_defaults`): a
    skill file whose title isn't already a skill in the database is created;
    existing skills, edited or not, are never overwritten. Returns the count
    created."""
    created = 0
    for path in _iter_skill_files():
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        title = str(data.get("title", "")).strip()
        if not title or models.get_fragment_by_name(db, title) is not None:
            continue
        try:
            models.create_fragment(
                db, title, data.get("description", ""), "instruction",
                skill_status=data.get("status", "active") or "active",
                skill_when_to_use=data.get("when_to_use", ""),
                skill_constraints=data.get("constraints"),
                skill_roles=data.get("roles"),
                skill_adapter_types=data.get("adapter_types"),
                skill_priority=int(data.get("priority", 0) or 0),
                skill_author=data.get("author", ""),
            )
            created += 1
        except (models.LibraryError, ValueError, TypeError):
            continue
    return created
