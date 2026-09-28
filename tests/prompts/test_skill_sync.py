import json
import os

import pytest

from app.db import get_db
from app.prompts import models, skill_sync


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    monkeypatch.setattr(skill_sync, "SKILLS_ROOT", str(root))
    return str(root)


def test_export_skill_to_disk_writes_json(db, skills_root):
    fid = models.create_fragment(
        db, "research", "Do research.", skill_status="active",
        skill_when_to_use="When researching.", skill_adapter_types=["codex"],
    )
    skill = models.get_fragment(db, fid)
    path = skill_sync.export_skill_to_disk(skill)
    assert os.path.isfile(path)
    assert os.path.dirname(path) == os.path.join(skills_root, "codex")
    with open(path) as fh:
        data = json.load(fh)
    assert data["title"] == "research" and data["when_to_use"] == "When researching."


def test_export_skill_to_disk_uses_generic_dir_when_no_adapter_types(db, skills_root):
    fid = models.create_fragment(db, "general", "Be helpful.", skill_status="active")
    path = skill_sync.export_skill_to_disk(models.get_fragment(db, fid))
    assert os.path.dirname(path) == os.path.join(skills_root, "generic")


def test_export_skill_to_disk_sanitises_unsafe_names(db, skills_root):
    fid = models.create_fragment(db, "../../evil", "x", skill_status="active")
    path = skill_sync.export_skill_to_disk(models.get_fragment(db, fid))
    assert skills_root in path
    assert ".." not in os.path.relpath(path, skills_root)


def test_seed_skills_from_disk_is_idempotent_and_additive(db, skills_root):
    os.makedirs(os.path.join(skills_root, "generic"), exist_ok=True)
    with open(os.path.join(skills_root, "generic", "greet.json"), "w") as fh:
        json.dump({"title": "greet", "description": "Say hello.", "status": "active"}, fh)

    created = skill_sync.seed_skills_from_disk(db)
    assert created == 1
    skill = models.get_fragment_by_name(db, "greet")
    assert skill is not None and skill.is_skill and skill.content == "Say hello."

    models.update_fragment(db, skill.id, content="Edited by a person")
    assert skill_sync.seed_skills_from_disk(db) == 0  # already present: not overwritten
    assert models.get_fragment_by_name(db, "greet").content == "Edited by a person"


def test_seed_skills_from_disk_handles_missing_directory(db, skills_root):
    assert skill_sync.seed_skills_from_disk(db) == 0
