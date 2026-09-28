from app.db import get_db
from app.prompts import models

AJAX = {"X-Requested-With": "XMLHttpRequest"}


def test_skills_page_lists_and_filters(client, app):
    client.post("/prompts/skills", data={"name": "s1", "content": "Do X", "roles": ["research"]}, headers=AJAX)
    client.post("/prompts/skills", data={"name": "s2", "content": "Do Y", "skill_status": "draft"}, headers=AJAX)
    page = client.get("/prompts/skills").get_data(as_text=True)
    assert "s1" in page and "s2" in page
    filtered = client.get("/prompts/skills?status=active").get_data(as_text=True)
    assert "s1" in filtered and "s2" not in filtered


def test_create_skill_over_ajax(client, app):
    resp = client.post(
        "/prompts/skills",
        data={
            "name": "research", "content": "Do research.", "when_to_use": "When investigating.",
            "constraints": "Needs network\nRate limited", "roles": ["research"], "adapter_types": "codex",
            "priority": "5", "author": "tester",
        },
        headers=AJAX,
    )
    assert resp.status_code == 200
    with app.app_context():
        skill = models.get_fragment_by_name(get_db(), "research")
    assert skill.is_skill and skill.skill_when_to_use == "When investigating."
    assert skill.skill_constraints == ["Needs network", "Rate limited"]
    assert skill.skill_roles == ["research"] and skill.skill_adapter_types == ["codex"]
    assert skill.skill_priority == 5 and skill.skill_author == "tester"

    dup = client.post("/prompts/skills", data={"name": "research", "content": "again"}, headers=AJAX)
    assert dup.status_code == 400


def test_edit_skill_page_and_save(client, app):
    client.post("/prompts/skills", data={"name": "s", "content": "v1"}, headers=AJAX)
    with app.app_context():
        sid = models.get_fragment_by_name(get_db(), "s").id
    page = client.get(f"/prompts/skills/{sid}").get_data(as_text=True)
    assert "v1" in page and "History" in page
    resp = client.post(f"/prompts/skills/{sid}", data={"content": "v2", "changelog": "clarified"}, headers=AJAX)
    assert resp.status_code == 200
    with app.app_context():
        skill = models.get_fragment(get_db(), sid)
    assert skill.content == "v2" and skill.version == 2


def test_edit_skill_404_for_non_skill_fragment(client, app):
    client.post("/prompts/library", data={"name": "ordinary", "content": "x"}, headers=AJAX)
    with app.app_context():
        fid = models.get_fragment_by_name(get_db(), "ordinary").id
    assert client.get(f"/prompts/skills/{fid}").status_code == 404


def test_deprecate_skill_over_ajax(client, app):
    client.post("/prompts/skills", data={"name": "s", "content": "x"}, headers=AJAX)
    with app.app_context():
        sid = models.get_fragment_by_name(get_db(), "s").id
    resp = client.post(f"/prompts/skills/{sid}/delete", headers=AJAX)
    assert resp.status_code == 200
    with app.app_context():
        assert models.get_fragment(get_db(), sid).skill_status == "deprecated"
    assert client.post(f"/prompts/skills/99999/delete", headers=AJAX).status_code == 404


def test_validate_skill_endpoint(client, app):
    client.post("/prompts/skills", data={"name": "s", "content": "x"}, headers=AJAX)
    with app.app_context():
        sid = models.get_fragment_by_name(get_db(), "s").id
    resp = client.post(f"/prompts/skills/{sid}/validate", headers=AJAX)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert any("when to use" in w.lower() for w in data["warnings"])


def test_mark_skill_reviewed_over_ajax(client, app):
    client.post("/prompts/skills", data={"name": "s", "content": "x"}, headers=AJAX)
    with app.app_context():
        sid = models.get_fragment_by_name(get_db(), "s").id
    page = client.get(f"/prompts/skills/{sid}").get_data(as_text=True)
    assert "Never marked reviewed" in page

    resp = client.post(f"/prompts/skills/{sid}/reviewed", data={"reviewer": "Ryan"}, headers=AJAX)
    assert resp.status_code == 200
    with app.app_context():
        skill = models.get_fragment(get_db(), sid)
    assert skill.skill_reviewed_by == "Ryan" and skill.skill_last_reviewed_at

    bad = client.post(f"/prompts/skills/{sid}/reviewed", data={"reviewer": ""}, headers=AJAX)
    assert bad.status_code == 400
    assert client.post("/prompts/skills/99999/reviewed", data={"reviewer": "Ryan"}, headers=AJAX).status_code == 404


def test_create_skill_non_ajax_redirects(client, app):
    resp = client.post("/prompts/skills", data={"name": "s", "content": "x"})
    assert resp.status_code == 302
    assert "/prompts/skills/" in resp.headers["Location"]


def test_skill_fragments_excluded_from_fragment_library_but_deletable_there(client, app):
    client.post("/prompts/skills", data={"name": "s", "content": "x"}, headers=AJAX)
    page = client.get("/prompts/library").get_data(as_text=True)
    assert ">s<" not in page  # not listed among ordinary fragments
