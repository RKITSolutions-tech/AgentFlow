import os

import pytest

from app.db import get_db
from app.prompts import assembler, models
from app.prompts.models import LibraryError


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hi')\n")
    (root / "src" / "util.py").write_text("X = 1\n")
    (root / "README.md").write_text("# readme\n")
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    return str(root)


def test_assemble_merges_body_and_fragments_in_order(db):
    f1 = models.create_fragment(db, "f1", "FRAG ONE")
    f2 = models.create_fragment(db, "f2", "FRAG TWO", category="example")
    tid = models.create_template(db, "t", body="BODY ${who}", fragments=[f2, f1])
    out = assembler.assemble_effective_prompt(db, tid, variables={"who": "you"})
    assert out.text == "BODY you\n\nFRAG TWO\n\nFRAG ONE"
    assert out.fragments_included == ["f2", "f1"] and out.template_name == "t"
    assert out.variables_substituted == {"who": "you"} and out.missing_variables == []


def test_variables_missing_are_reported_not_fatal(db):
    tid = models.create_template(db, "t", body="Hi ${a} and ${b}", variables={"a": "default-a"})
    out = assembler.assemble_effective_prompt(db, "t", variables={})
    assert out.text == "Hi default-a and ${b}" and out.missing_variables == ["b"]
    assert assembler.assemble_effective_prompt(db, tid, variables={"a": "X", "b": "Y"}).text == "Hi X and Y"


def test_strict_resolver_errors_propagate(db):
    models.create_template(db, "t", body="Hi ${nope}")

    def strict(ref):
        raise ValueError(f"cannot resolve {ref}")

    with pytest.raises(ValueError, match="cannot resolve"):
        assembler.assemble_effective_prompt(db, "t", resolver=strict)


def test_unknown_template_and_nothing_to_assemble(db):
    with pytest.raises(LibraryError, match="Unknown prompt template"):
        assembler.assemble_effective_prompt(db, "missing")
    with pytest.raises(LibraryError, match="Nothing to assemble"):
        assembler.assemble_effective_prompt(db, None)


def test_literal_text_without_a_template(db):
    assert assembler.assemble_effective_prompt(db, None, text="just this").text == "just this"


def test_inheritance_body_and_fragments(db):
    common = models.create_fragment(db, "style", "BASE STYLE")
    extra = models.create_fragment(db, "extra", "BASE EXTRA")
    added = models.create_fragment(db, "added", "CHILD ONLY")
    base = models.create_template(db, "base", body="BASE BODY", fragments=[common, extra])
    child = models.create_template(db, "child", fragments=[added], base_template_id=base)
    out = assembler.assemble_effective_prompt(db, child)
    # An empty child body keeps the base body; the child's fragments follow the base's.
    assert out.text == "BASE BODY\n\nBASE STYLE\n\nBASE EXTRA\n\nCHILD ONLY"
    own = models.create_template(db, "child2", body="OWN BODY", base_template_id=base)
    assert assembler.assemble_effective_prompt(db, own).text.startswith("OWN BODY")


def test_inheritance_does_not_duplicate_a_shared_fragment(db):
    a = models.create_fragment(db, "greeting", "hello")
    z = models.create_fragment(db, "closing", "bye")
    base = models.create_template(db, "base", body="B", fragments=[a, z])
    child = models.create_template(db, "child", fragments=[a], base_template_id=base)
    out = assembler.assemble_effective_prompt(db, child)
    assert out.text == "B\n\nhello\n\nbye" and out.fragments_included == ["greeting", "closing"]


def test_resolve_context_files_globs_and_validation(repo, tmp_path):
    files, skipped = assembler.resolve_context_files(["src/*.py", "README.md", "nope/*.py"], repo)
    assert files == ["src/app.py", "src/util.py", "README.md"]
    assert any("nope" in s and "no match" in s for s in skipped)
    files, skipped = assembler.resolve_context_files(["../secret.txt", "/etc/passwd"], repo)
    assert files == [] and len(skipped) == 2
    os.symlink(str(tmp_path / "secret.txt"), os.path.join(repo, "link.txt"))
    files, skipped = assembler.resolve_context_files(["*.txt"], repo)
    assert files == [] and any("outside the repository" in s for s in skipped)


def test_resolve_context_files_respects_allowed_roots(repo, tmp_path):
    assert assembler.resolve_context_files(["README.md"], repo, (str(tmp_path),))[0] == ["README.md"]
    with pytest.raises(LibraryError, match="allowed project roots"):
        assembler.resolve_context_files(["README.md"], repo, ("/somewhere/else",))


def test_file_limit_is_enforced(tmp_path):
    root = tmp_path / "big"
    root.mkdir()
    for i in range(assembler.MAX_CONTEXT_FILES + 3):
        (root / f"f{i:02}.txt").write_text("x")
    files, skipped = assembler.resolve_context_files(["*.txt"], str(root))
    assert len(files) == assembler.MAX_CONTEXT_FILES and len(skipped) == 3


def test_mentions_and_globs_inject_file_contents(db, repo):
    models.create_template(db, "t", body="Look at @src/app.py and mail me@example.com; ignore @nothing.py")
    out = assembler.assemble_effective_prompt(db, "t", root=repo, context_globs=["README.md"])
    assert out.resolved_files == ["README.md", "src/app.py"]
    assert "File: src/app.py\n```\nprint('hi')\n```" in out.text and "# readme" in out.text
    assert "me@example.com" in out.text and "@nothing.py" in out.text


def test_mentions_in_variable_values_do_not_pull_files(db, repo):
    models.create_template(db, "t", body="Task: ${task}")
    out = assembler.assemble_effective_prompt(db, "t", variables={"task": "see @README.md"}, root=repo)
    assert out.resolved_files == [] and "# readme" not in out.text


def test_ralph_instructions_respect_enabled_order_and_agent(db):
    blocks = models.list_blocks(db)
    models.update_block(db, blocks[0].id, enabled=False)
    models.create_block(db, "codex-only", "Codex tip", applies_to="codex")
    models.move_block(db, models.list_blocks(db)[-1].id, "up")
    text, names = assembler.include_ralph_instructions(models.list_blocks(db), "fake")
    assert blocks[0].name not in names and "codex-only" not in names
    assert text.startswith("Instructions:\n- ")
    text, names = assembler.include_ralph_instructions(models.list_blocks(db), "codex")
    assert "codex-only" in names and names.index("codex-only") < names.index(blocks[-1].name)
    assert assembler.include_ralph_instructions([], None) == ("", [])
