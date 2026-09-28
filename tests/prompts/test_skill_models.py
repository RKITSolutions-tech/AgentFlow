import pytest

from app.db import get_db
from app.prompts import models
from app.prompts.models import LibraryError


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def _skill(db, name="research", **kwargs):
    kwargs.setdefault("skill_status", "active")
    return models.create_fragment(db, name, "Do the thing.", **kwargs)


def test_create_and_list_skill(db):
    fid = _skill(db, skill_when_to_use="When researching.", skill_roles=["research"], skill_adapter_types=["codex"])
    skill = models.get_fragment(db, fid)
    assert skill.is_skill and skill.skill_status == "active"
    assert skill.skill_when_to_use == "When researching."
    assert skill.skill_roles == ["research"] and skill.skill_adapter_types == ["codex"]
    assert [s.id for s in models.list_skills(db)] == [fid]
    # An ordinary fragment is not a skill and doesn't show up in list_skills.
    ofid = models.create_fragment(db, "ordinary", "x")
    assert not models.get_fragment(db, ofid).is_skill
    assert ofid not in [s.id for s in models.list_skills(db)]


def test_list_skills_filters_by_role_adapter_status(db):
    general = _skill(db, "general", skill_roles=[], skill_adapter_types=[])
    research_codex = _skill(db, "research-codex", skill_roles=["research"], skill_adapter_types=["codex"])
    draft = _skill(db, "draft-skill", skill_status="draft")

    active = {s.id for s in models.list_skills(db, status="active")}
    assert active == {general, research_codex}
    assert draft not in active

    for_research = {s.id for s in models.list_skills(db, role="research", status="active")}
    assert for_research == {general, research_codex}  # empty roles list matches everything

    for_planning = {s.id for s in models.list_skills(db, role="planning", status="active")}
    assert for_planning == {general}  # research_codex is scoped away from planning

    for_codex = {s.id for s in models.list_skills(db, adapter_type="codex", status="active")}
    assert for_codex == {general, research_codex}
    for_claude = {s.id for s in models.list_skills(db, adapter_type="claude", status="active")}
    assert for_claude == {general}


def test_changelog_recorded_on_content_change(db):
    fid = _skill(db)
    models.update_fragment(db, fid, tags="x")  # metadata only, no version bump, no changelog needed
    models.update_fragment(db, fid, content="New content", changelog="Rewrote for clarity")
    history = models.fragment_versions(db, fid)
    assert history[0]["version"] == 2 and history[0]["changelog"] == "Rewrote for clarity"
    assert history[1]["changelog"] == ""


def test_circular_dependency_rejected_direct_and_indirect(db):
    a = _skill(db, "a")
    with pytest.raises(LibraryError, match="cannot depend on itself"):
        models.update_fragment(db, a, skill_depends_on=[a])

    b = _skill(db, "b", skill_depends_on=[a])
    with pytest.raises(LibraryError, match="cannot depend on itself"):
        models.update_fragment(db, a, skill_depends_on=[b])

    c = _skill(db, "c", skill_depends_on=[b])
    with pytest.raises(LibraryError, match="cannot depend on itself"):
        models.update_fragment(db, a, skill_depends_on=[c])


def test_dependency_must_be_an_existing_skill(db):
    ordinary = models.create_fragment(db, "ordinary", "x")
    with pytest.raises(LibraryError, match="does not exist"):
        _skill(db, "s", skill_depends_on=[ordinary])
    with pytest.raises(LibraryError, match="does not exist"):
        _skill(db, "s2", skill_depends_on=[999999])


def test_resolve_skill_order_topological_and_priority(db):
    a = _skill(db, "a", skill_priority=1)
    b = _skill(db, "b", skill_depends_on=[a], skill_priority=5)
    c = _skill(db, "c", skill_priority=10)
    ordered = models.resolve_skill_order(models.list_skills(db))
    names = [s.name for s in ordered]
    assert names.index("a") < names.index("b")  # dependency before dependent
    assert names[0] == "c"  # highest priority, no deps, goes first among independents


def test_deprecate_skill(db):
    fid = _skill(db)
    models.deprecate_skill(db, fid)
    assert models.get_fragment(db, fid).skill_status == "deprecated"
    assert fid not in [s.id for s in models.list_skills(db, status="active")]
    with pytest.raises(LibraryError, match="Not a skill"):
        ordinary = models.create_fragment(db, "ordinary", "x")
        models.deprecate_skill(db, ordinary)


def test_delete_fragment_blocked_by_skill_dependents(db):
    a = _skill(db, "a")
    models.create_fragment(db, "b", "y", skill_status="active", skill_depends_on=[a])
    with pytest.raises(LibraryError, match="Depended on by skill"):
        models.delete_fragment(db, a)


def test_mark_skill_reviewed(db):
    fid = _skill(db)
    skill = models.get_fragment(db, fid)
    assert skill.skill_last_reviewed_at == "" and skill.skill_reviewed_by == ""

    reviewed = models.mark_skill_reviewed(db, fid, "Ryan")
    assert reviewed.skill_reviewed_by == "Ryan"
    assert reviewed.skill_last_reviewed_at != ""
    # A review with no content change leaves the version/changelog history untouched.
    assert reviewed.version == skill.version
    assert models.fragment_versions(db, fid) == models.fragment_versions(db, fid)  # still just v1
    assert len(models.fragment_versions(db, fid)) == 1

    with pytest.raises(LibraryError, match="reviewer name is required"):
        models.mark_skill_reviewed(db, fid, "  ")

    ordinary = models.create_fragment(db, "ordinary", "x")
    with pytest.raises(LibraryError, match="Not a skill"):
        models.mark_skill_reviewed(db, ordinary, "Ryan")


def test_validate_skill(db):
    fid = _skill(db, skill_when_to_use="", skill_roles=["not-a-role"])
    result = models.validate_skill(db, fid)
    assert result["ok"] is True  # warnings don't fail validation
    assert any("when to use" in w.lower() for w in result["warnings"])
    assert any("not-a-role" in w for w in result["warnings"])

    ordinary = models.create_fragment(db, "ordinary", "x")
    assert models.validate_skill(db, ordinary) == {"ok": False, "errors": ["Not a skill"], "warnings": []}
