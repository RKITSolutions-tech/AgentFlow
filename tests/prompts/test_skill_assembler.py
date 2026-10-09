import pytest

from app.db import get_db
from app.projects import models as project_models
from app.prompts import assembler, models


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def test_skill_context_empty_when_no_skills(db):
    text, names = assembler.skill_context(db, role="GENERAL", agent_type="codex")
    assert text == "" and names == []


def test_skill_context_filters_by_role_and_adapter(db):
    models.create_fragment(
        db, "general-skill", "Be helpful.", skill_status="active",
        skill_when_to_use="Always.",
    )
    models.create_fragment(
        db, "research-only", "Cite sources.", skill_status="active",
        skill_roles=["research"], skill_adapter_types=["codex"],
    )
    text, names = assembler.skill_context(db, role="research", agent_type="codex")
    assert names == ["general-skill", "research-only"]
    assert "Be helpful." in text and "Cite sources." in text
    assert "When to use: Always." in text

    text2, names2 = assembler.skill_context(db, role="planning", agent_type="codex")
    assert names2 == ["general-skill"]


def test_skill_context_dependency_and_priority_order(db):
    base = models.create_fragment(db, "base", "Base skill.", skill_status="active", skill_priority=1)
    models.create_fragment(db, "derived", "Derived skill.", skill_status="active", skill_depends_on=[base], skill_priority=5)
    models.create_fragment(db, "independent", "Independent.", skill_status="active", skill_priority=10)
    _, names = assembler.skill_context(db)
    assert names.index("base") < names.index("derived")
    assert names[0] == "independent"


def test_skill_context_renders_constraints(db):
    models.create_fragment(
        db, "constrained", "Do X.", skill_status="active",
        skill_constraints=["Requires network", "Rate limited"],
    )
    text, _ = assembler.skill_context(db)
    assert "Constraints: Requires network; Rate limited" in text


def test_skill_context_degrades_gracefully_on_error(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(models, "list_skills", boom)
    text, names = assembler.skill_context(db)
    assert text == "" and names == []


def test_assemble_effective_prompt_includes_skills_when_role_given(db):
    models.create_fragment(db, "s", "Skill text.", skill_status="active")
    out = assembler.assemble_effective_prompt(db, text="Task text", role="GENERAL")
    assert "Task text" in out.text and "Skill text." in out.text
    assert out.skills_included == ["s"]

    # Without role/agent_type, skills are not appended (backward compatible).
    out2 = assembler.assemble_effective_prompt(db, text="Task text")
    assert "Skill text." not in out2.text
    assert out2.skills_included == []


def test_assemble_effective_prompt_skips_inactive_and_unmatched_skills(db):
    models.create_fragment(db, "draft", "Draft skill.", skill_status="draft")
    models.create_fragment(db, "wrong-role", "Wrong role.", skill_status="active", skill_roles=["planning"])
    out = assembler.assemble_effective_prompt(db, text="Task", role="GENERAL")
    assert "Draft skill." not in out.text and "Wrong role." not in out.text


# -- per-project activation overrides (docs/AGENT_ADAPTER.md §23.5) --------------------------------


def test_skill_context_project_override_disables_active_skill(db):
    pid = project_models.create_project(db, "p")
    sid = models.create_fragment(db, "proj-skill", "Skill text.", skill_status="active")
    assert "proj-skill" in assembler.skill_context(db)[1]
    models.set_override(db, pid, "skill", sid, enabled=False)
    assert "proj-skill" not in assembler.skill_context(db, project_id=pid)[1]
    assert "proj-skill" in assembler.skill_context(db)[1]  # global, unaffected
    models.clear_override(db, pid, "skill", sid)
    assert "proj-skill" in assembler.skill_context(db, project_id=pid)[1]


def test_effective_skills_falls_back_to_global_when_no_override(db):
    pid = project_models.create_project(db, "p")
    models.create_fragment(db, "active-skill", "x", skill_status="active")
    models.create_fragment(db, "draft-skill", "x", skill_status="draft")
    assert [s.name for s in models.effective_skills(db, project_id=pid)] == ["active-skill"]
    assert [s.name for s in models.effective_skills(db)] == ["active-skill"]
