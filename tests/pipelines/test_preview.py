import pytest

from app.db import get_db
from app.pipelines import composer, persistence, visualization
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def setup(app, client):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        yield type("S", (), {"app": app, "client": client, "project_id": project_id, "db": get_db()})


def _elements(setup, *elements, name="preview-pl"):
    persistence.create_pipeline(setup.db, {"name": name, "elements": list(elements)}, setup.project_id)
    definition = persistence.get_definition(setup.db, persistence.find_pipeline(setup.db, name, setup.project_id).id)
    return composer.compose(definition, persistence.resolver_for(setup.db, setup.project_id)), name


def test_preview_graph_is_structural_and_pending(setup):
    elements, _ = _elements(
        setup,
        {"name": "build", "type": "COMMAND", "config": {"command": "true"}},
        {"name": "test", "type": "TEST", "depends_on": ["build"], "config": {"command": "true"}},
        {"name": "off", "type": "COMMAND", "enabled": "DISABLED", "depends_on": [], "config": {"command": "true"}},
    )
    g = visualization.build_preview_graph(elements)
    by = {n["id"]: n for n in g["nodes"]}
    assert by["build"]["state"] == "PENDING" and by["test"]["state"] == "PENDING"
    assert by["off"]["state"] == "DISABLED"
    assert by["build"]["layer"] == 0 and by["test"]["layer"] == 1
    assert {"from": "build", "to": "test", "kind": "dependency"} in g["edges"]
    assert g["layers"] == 2


def test_preview_node_detail_plain_and_group(setup):
    elements, _ = _elements(
        setup,
        {"name": "build", "type": "COMMAND", "config": {"command": "true"}},
    )
    detail = visualization.preview_node_detail(elements, "build")
    assert detail["configuration"] == {"command": "true"}
    assert detail["attempts"] == [] and detail["events"] == []
    assert visualization.preview_node_detail(elements, "missing") is None


def test_preview_route_renders_graph(setup):
    _, name = _elements(
        setup,
        {"name": "build", "type": "COMMAND", "config": {"command": "true"}},
    )
    url = f"/projects/{setup.project_id}/pipelines/{name}/preview"
    resp = setup.client.get(url)
    assert resp.status_code == 200
    assert b"build" in resp.data

    graph = setup.client.get(url + "/graph.json", headers=AJAX).get_json()
    assert any(n["id"] == "build" for n in graph["nodes"])

    detail = setup.client.get(url + "/nodes/build.json", headers=AJAX).get_json()
    assert detail["name"] == "build"


def test_preview_route_reports_composition_errors(setup):
    """Two pipelines that reference each other via SUB_PIPELINE validate fine on
    their own (each only checks for self-recursion) but form a cycle once both
    exist -- composer.compose only catches that when something tries to flatten
    one of them, which is exactly what the preview route does."""
    persistence.create_pipeline(
        setup.db, {"name": "a", "elements": [{"name": "s", "type": "COMMAND", "config": {"command": "true"}}]},
        setup.project_id,
    )
    persistence.create_pipeline(
        setup.db,
        {"name": "b", "elements": [{"name": "call_a", "type": "SUB_PIPELINE", "config": {"pipeline": "a"}}]},
        setup.project_id,
    )
    pipeline_a = persistence.find_pipeline(setup.db, "a", setup.project_id)
    persistence.new_version(
        setup.db, pipeline_a.id,
        {"name": "a", "elements": [{"name": "call_b", "type": "SUB_PIPELINE", "config": {"pipeline": "b"}}]},
    )

    resp = setup.client.get(f"/projects/{setup.project_id}/pipelines/a/preview")
    assert resp.status_code == 200
    assert b"Recursive" in resp.data

    err = setup.client.get(f"/projects/{setup.project_id}/pipelines/a/preview/graph.json", headers=AJAX)
    assert err.status_code == 400
