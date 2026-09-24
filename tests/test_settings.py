from app.db import get_db
from app.settings import models as settings_models


def test_settings_page_lists_seeded_catalog(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b"Openai models" in resp.data or b"OpenAI" in resp.data
    assert b"Anthropic models" in resp.data or b"Anthropic" in resp.data


def test_add_model_appears_in_catalog(client, app):
    resp = client.post(
        "/settings/models/add",
        data={"provider": "openai", "model_id": "gpt-test-9000"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"gpt-test-9000" in resp.data

    with app.app_context():
        db = get_db()
        models = settings_models.list_models(db, provider="openai")
        assert any(m.model_id == "gpt-test-9000" for m in models)


def test_toggle_model_disables_it(client, app):
    with app.app_context():
        db = get_db()
        catalog_id = settings_models.add_model(db, "openai", "gpt-toggle-test")

    resp = client.post(
        f"/settings/models/{catalog_id}/toggle",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["enabled"] is False

    with app.app_context():
        db = get_db()
        entry = settings_models.get_model(db, catalog_id)
        assert entry.enabled is False


def test_disabled_model_is_excluded_from_session_form(client, app):
    with app.app_context():
        db = get_db()
        catalog_id = settings_models.add_model(db, "openai", "gpt-hidden-test")
        settings_models.set_model_enabled(db, catalog_id, False)

    client.post("/projects/new", data={"name": "Model Test Project", "description": ""})

    resp = client.get("/sessions/project/1")
    assert resp.status_code == 200
    assert b"gpt-hidden-test" not in resp.data


def test_delete_model_removes_it(client, app):
    with app.app_context():
        db = get_db()
        catalog_id = settings_models.add_model(db, "openai", "gpt-delete-test")

    resp = client.post(
        f"/settings/models/{catalog_id}/delete",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200

    with app.app_context():
        db = get_db()
        assert settings_models.get_model(db, catalog_id) is None
