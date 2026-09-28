"""Task 54: the generic built-in skills (app/prompts/defaults.DEFAULT_SKILLS),
seeded via `models.seed_builtin_skills`. Not wired into every `app`/`db` fixture
(see docs/AGENT_ADAPTER.md §23.4) -- each test seeds explicitly."""
import pytest

from app.agents.base import AGENT_ROLES
from app.db import get_db
from app.prompts import assembler, models
from app.prompts.defaults import DEFAULT_SKILLS


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def test_seed_builtin_skills_creates_all_and_is_idempotent(db):
    created = models.seed_builtin_skills(db)
    assert created == len(DEFAULT_SKILLS)
    names = {s.name for s in models.list_skills(db)}
    assert names == {s["name"] for s in DEFAULT_SKILLS}

    again = models.seed_builtin_skills(db)
    assert again == 0
    assert len(models.list_skills(db)) == len(DEFAULT_SKILLS)


def test_seeded_skills_are_active_and_well_formed(db):
    models.seed_builtin_skills(db)
    for skill in models.list_skills(db):
        assert skill.skill_status == "active"
        assert skill.skill_author == "AgentFlow"
        assert skill.content.strip()
        for role in skill.skill_roles:
            assert role.upper() in AGENT_ROLES


def test_seeded_skills_pass_validate_skill(db):
    models.seed_builtin_skills(db)
    for skill in models.list_skills(db):
        result = models.validate_skill(db, skill.id)
        assert result["ok"] is True, f"{skill.name}: {result['errors']}"
        # A skill this codebase ships should already document when to use it.
        assert not any("when to use" in w.lower() for w in result["warnings"]), skill.name


def test_seeded_skills_have_no_dependency_cycles(db):
    models.seed_builtin_skills(db)
    skills = models.list_skills(db)
    ordered = models.resolve_skill_order(skills)
    # A cycle would silently drop members (resolve_skill_order is cycle-*safe*,
    # not cycle-detecting); the real guarantee is that nothing is missing.
    assert {s.id for s in ordered} == {s.id for s in skills}


def test_no_web_or_knowledge_base_research_skill_seeded(db):
    """docs/AGENT_ADAPTER.md §23 Phase B/C (shared knowledge base, web) are
    proposed, not built (task 45 shipped only Phase A, repository-scoped) --
    a skill describing either would describe capability that doesn't exist."""
    models.seed_builtin_skills(db)
    names = " ".join(s.name for s in models.list_skills(db))
    assert "web-search" not in names and "knowledge-base" not in names


def test_seeded_skills_render_via_skill_context_for_intended_roles(db):
    models.seed_builtin_skills(db)
    expected_by_role = {}
    for skill in DEFAULT_SKILLS:
        for role in skill["roles"] or [r.lower() for r in AGENT_ROLES]:
            expected_by_role.setdefault(role, set()).add(skill["name"])

    for role, expected_names in expected_by_role.items():
        text, names = assembler.skill_context(db, role=role)
        assert expected_names <= set(names), f"role {role!r} missing {expected_names - set(names)}"
        assert text  # rendered into prompt text, not just selected
        for name in expected_names:
            assert f"### {name}" in text
