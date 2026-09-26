import pytest

from app.db import get_db
from app.prompts import models
from app.prompts.defaults import DEFAULT_INSTRUCTIONS, DEFAULT_TEMPLATES
from app.prompts.models import LibraryError


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def test_defaults_are_seeded_from_the_old_constants(db):
    for name, body in DEFAULT_TEMPLATES.items():
        assert models.get_template_by_name(db, name).body == body
    blocks = models.list_blocks(db)
    assert [(b.name, b.content) for b in blocks] == list(DEFAULT_INSTRUCTIONS)
    assert all(b.enabled for b in blocks)


def test_seeding_is_idempotent_and_keeps_edits(db):
    block = models.list_blocks(db)[0]
    models.update_block(db, block.id, content="Edited", enabled=False)
    models.seed_defaults(db)
    models.seed_defaults(db)
    assert len(models.list_blocks(db)) == len(DEFAULT_INSTRUCTIONS)
    again = models.get_block(db, block.id)
    assert again.content == "Edited" and again.enabled is False


def test_fragment_versioning(db):
    fid = models.create_fragment(db, "tone", "Be brief.", tags="Style, style, tone")
    frag = models.get_fragment(db, fid)
    assert frag.version == 1 and frag.tags == ["style", "tone"] and frag.category == "instruction"
    models.update_fragment(db, fid, tags="other")  # metadata only: same version
    assert models.get_fragment(db, fid).version == 1
    models.update_fragment(db, fid, content="Be very brief.")
    models.update_fragment(db, fid, content="Be very brief.")  # unchanged content
    assert models.get_fragment(db, fid).version == 2
    history = models.fragment_versions(db, fid)
    assert [v["version"] for v in history] == [2, 1] and history[1]["content"] == "Be brief."


def test_fragment_validation(db):
    models.create_fragment(db, "a", "x")
    for args in (("a", "y"), ("", "y"), ("b", " "), ("b", "y", "bogus")):
        with pytest.raises(LibraryError):
            models.create_fragment(db, *args)


def test_template_composition_and_in_use_protection(db):
    f1 = models.create_fragment(db, "f1", "one")
    f2 = models.create_fragment(db, "f2", "two", category="context")
    tid = models.create_template(db, "t", body="Do it.", fragments=[f1, f2], variables={"lang": "py"})
    t = models.get_template(db, tid)
    assert t.fragments == [f1, f2] and t.variables == {"lang": "py"}
    with pytest.raises(LibraryError, match="In use"):
        models.delete_fragment(db, f1)
    models.update_template(db, tid, fragments=[f2])
    models.delete_fragment(db, f1)
    with pytest.raises(LibraryError):
        models.create_template(db, "bad", fragments=[999], body="x")
    with pytest.raises(LibraryError):
        models.create_template(db, "empty")


def test_template_inheritance_cannot_loop_and_base_is_protected(db):
    a = models.create_template(db, "a", body="A")
    b = models.create_template(db, "b", body="B", base_template_id=a)
    with pytest.raises(LibraryError, match="inherit"):
        models.update_template(db, a, base_template_id=b)
    with pytest.raises(LibraryError, match="inherit"):
        models.update_template(db, a, base_template_id=a)
    with pytest.raises(LibraryError, match="base of"):
        models.delete_template(db, a)
    models.delete_template(db, b)
    models.delete_template(db, a)


def test_block_enable_disable_and_order(db):
    blocks = models.list_blocks(db)
    first, second = blocks[0], blocks[1]
    models.update_block(db, first.id, enabled=False)
    assert [b.id for b in models.list_blocks(db, enabled_only=True)][0] == second.id
    models.move_block(db, second.id, "up")
    assert models.list_blocks(db)[0].id == second.id
    models.move_block(db, second.id, "up")  # already first: no-op
    assert models.list_blocks(db)[0].id == second.id
    models.update_block(db, second.id, content="New text")
    assert models.get_block(db, second.id).version == 2
    with pytest.raises(LibraryError):
        models.move_block(db, second.id, "sideways")
