import copy

import pytest

from app.db import get_db
from app.pipelines import composer, persistence, schema
from app.pipelines.persistence import PipelineDefinitionError
from app.pipelines.validator import topological_order, validate


@pytest.fixture
def db(app):
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        conn.commit()
        yield conn


def _def(name="p", *elements):
    return {"name": name, "type": "DEVELOPMENT", "elements": list(elements)}


def _cmd(name, **extra):
    return {"name": name, "type": "COMMAND", "config": {"command": "true"}, **extra}


def test_builtins_are_seeded_and_valid(db):
    names = {p.name for p in persistence.list_pipelines(db)}
    assert {"web-feature", "backend-only", "documentation-only", "standard-cleanup"} <= names
    resolver = persistence.resolver_for(db, None)
    for name in names:
        assert validate(resolver(name), resolver) == []
    assert persistence.seed_builtins(db) == []  # idempotent


def test_validator_reports_every_problem():
    errors = validate(
        _def(
            "",
            {"name": "a", "type": "NOPE"},
            {"name": "a", "type": "COMMAND", "config": {}},
            {"name": "b", "type": "TEST", "config": {"command": "x"}, "depends_on": ["ghost"]},
            {"name": "c", "type": "AGENT", "config": {"prompt": "hi"},
             "compensation": {"action": "LOOP", "step": "a"}},
            {"name": "d", "type": "WAIT", "config": {"seconds": 1}, "phase": "LATER"},
        )
    )
    text = "\n".join(errors)
    for expected in ("needs a name", "unknown type", "defined twice", "needs config: command",
                     "unknown element 'ghost'", "max_loops", "unknown phase"):
        assert expected in text


def test_validator_rejects_cycles_and_bad_compensation_refs():
    cyc = _def("p", _cmd("a", depends_on=["b"]), _cmd("b", depends_on=["a"]))
    assert any("cycle" in e for e in validate(cyc))
    bad = _def("p", _cmd("a", compensation={"action": "RUN_STEP", "step": "zzz"}))
    assert any("does not exist" in e for e in validate(bad))
    assert validate(_def("p")) == ["The pipeline needs at least one element"]


def test_missing_subpipeline_reference_is_flagged(db):
    definition = _def("p", {"name": "s", "type": "SUB_PIPELINE", "config": {"pipeline": "ghost"}})
    with pytest.raises(PipelineDefinitionError, match="ghost"):
        persistence.create_pipeline(db, definition, 1)


def test_create_version_and_history(db):
    definition = _def("mine", _cmd("a"), _cmd("b", depends_on=["a"]))
    pid = persistence.create_pipeline(db, definition, 1, created_by="sam")
    assert persistence.get_pipeline(db, pid).current_version == 1
    revised = copy.deepcopy(definition)
    revised["elements"].append(_cmd("c"))
    assert persistence.new_version(db, pid, revised, "sam", "add c") == 2
    assert [e["name"] for e in persistence.get_definition(db, pid)["elements"]] == ["a", "b", "c"]
    # history is immutable: version 1 still has two elements
    assert len(persistence.get_definition(db, pid, 1)["elements"]) == 2
    assert [v["version"] for v in persistence.list_versions(db, pid)] == [2, 1]
    assert "depends_on" not in persistence.get_definition(db, pid)["elements"][0]  # implicit stays implicit
    with pytest.raises(PipelineDefinitionError):
        persistence.new_version(db, pid, {**revised, "name": "other"})
    with pytest.raises(PipelineDefinitionError, match="already exists"):
        persistence.create_pipeline(db, definition, 1)


def test_project_pipeline_shadows_builtin(db):
    persistence.create_pipeline(db, _def("web-feature", _cmd("only")), 1)
    assert persistence.find_pipeline(db, "web-feature", 1).project_id == 1
    assert persistence.find_pipeline(db, "web-feature", None).project_id is None


def test_composer_expands_subpipeline_with_dependencies(db):
    resolver = persistence.resolver_for(db, None)
    definition = _def(
        "parent",
        _cmd("first"),
        {"name": "sub", "type": "SUB_PIPELINE", "config": {"pipeline": "backend-only"}},
        _cmd("last"),
    )
    ordered = composer.compose(definition, resolver)
    names = [e["name"] for e in ordered]
    assert names[0] == "first" and names[-1] == "last"
    assert "sub.implement" in names and "sub.prepare.clean_tree" in names
    by = {e["name"]: e for e in ordered}
    assert by["sub.prepare.clean_tree"]["depends_on"] == ["first"]
    assert set(by["last"]["depends_on"]) == {n for n in names if n.startswith("sub.")}
    assert by["sub.unit_tests"]["compensation"]["step"] == "sub.implement"


def test_composer_rejects_recursion(db):
    a = persistence.create_pipeline(db, _def("loop-a", _cmd("x")), 1)
    recursive = _def("loop-a", {"name": "again", "type": "SUB_PIPELINE", "config": {"pipeline": "loop-a"}})
    # A new version of loop-a that references itself validates (it exists) but cannot compose.
    persistence.new_version(db, a, recursive)
    with pytest.raises(composer.CompositionError, match="Recursive"):
        composer.compose(persistence.get_definition(db, a), persistence.resolver_for(db, 1))


def test_topological_order_keeps_sequence_where_possible():
    els = [_cmd("a", depends_on=["c"]), _cmd("b", depends_on=[]), _cmd("c", depends_on=["b"])]
    assert [e["name"] for e in topological_order(els)] == ["b", "c", "a"]
    with pytest.raises(ValueError):
        topological_order([_cmd("a", depends_on=["b"]), _cmd("b", depends_on=["a"])])


def test_implicit_linear_dependencies_per_phase():
    els = composer.with_implicit_dependencies(
        [_cmd("s1", phase="SETUP"), _cmd("m1"), _cmd("s2", phase="SETUP"), _cmd("m2")]
    )
    assert {e["name"]: e["depends_on"] for e in els} == {
        "s1": [], "m1": [], "s2": ["s1"], "m2": ["m1"]
    }


def test_category_marks_rigging_distinct():
    assert schema.category("PROCESS_START") == "RIGGING"
    assert schema.category("AGENT") == "DEVELOPMENT"
    assert schema.category("MANUAL_APPROVAL") == "HUMAN"
