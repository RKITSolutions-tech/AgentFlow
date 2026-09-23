from __future__ import annotations

import os
import subprocess

import pytest

from app.execution.host import HostExecutionProvider
from app.workspace import search


def _create_project_with_repo(client, allowed_root, repo_name="repo-a"):
    repo_path = os.path.join(allowed_root, repo_name)
    os.makedirs(repo_path)

    client.post("/projects/new", data={"name": "Proj", "description": ""})
    client.post(
        "/projects/1/repositories",
        data={"name": repo_name, "path": repo_path, "is_primary": "on"},
    )
    return repo_path


def test_search_finds_expected_text_match(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)

    with open(os.path.join(repo_path, "hello.py"), "w") as fh:
        fh.write("def greet():\n    return 'needle-value'\n")

    resp = client.get("/projects/1/repos/1/files/search", query_string={"q": "needle-value"})
    assert resp.status_code == 200
    assert b"hello.py:2" in resp.data
    assert b"needle-value" in resp.data


def test_search_result_links_to_file_view(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)

    with open(os.path.join(repo_path, "hello.py"), "w") as fh:
        fh.write("marker-text\n")

    resp = client.get("/projects/1/repos/1/files/search", query_string={"q": "marker-text"})
    assert resp.status_code == 200
    assert b'/projects/1/repos/1/files/view?path=hello.py' in resp.data


def test_search_filters_by_file_type(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)

    with open(os.path.join(repo_path, "match.py"), "w") as fh:
        fh.write("shared-term\n")
    with open(os.path.join(repo_path, "match.txt"), "w") as fh:
        fh.write("shared-term\n")

    resp = client.get(
        "/projects/1/repos/1/files/search",
        query_string={"q": "shared-term", "type": "py"},
    )
    assert resp.status_code == 200
    assert b"match.py" in resp.data
    assert b"match.txt" not in resp.data


def test_search_filters_by_path_pattern(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)
    os.makedirs(os.path.join(repo_path, "sub"))

    with open(os.path.join(repo_path, "top.txt"), "w") as fh:
        fh.write("pattern-term\n")
    with open(os.path.join(repo_path, "sub", "nested.txt"), "w") as fh:
        fh.write("pattern-term\n")

    resp = client.get(
        "/projects/1/repos/1/files/search",
        query_string={"q": "pattern-term", "path": "sub/*"},
    )
    assert resp.status_code == 200
    assert b"nested.txt" in resp.data
    assert b"top.txt:" not in resp.data


def test_search_respects_gitignore(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)

    with open(os.path.join(repo_path, ".gitignore"), "w") as fh:
        fh.write("ignored.txt\n")
    with open(os.path.join(repo_path, "ignored.txt"), "w") as fh:
        fh.write("ignore-term\n")
    with open(os.path.join(repo_path, "tracked.txt"), "w") as fh:
        fh.write("ignore-term\n")

    resp = client.get("/projects/1/repos/1/files/search", query_string={"q": "ignore-term"})
    assert resp.status_code == 200
    assert b"tracked.txt" in resp.data
    assert b"ignored.txt" not in resp.data


def test_search_empty_query_shows_no_results(client, app):
    allowed_root = app.config["allowed_root"]
    _create_project_with_repo(client, allowed_root)

    resp = client.get("/projects/1/repos/1/files/search", query_string={"q": ""})
    assert resp.status_code == 200
    assert b"result" not in resp.data.lower() or b"0 result" not in resp.data


def test_search_rejects_unsupported_filter_characters(client, app):
    allowed_root = app.config["allowed_root"]
    _create_project_with_repo(client, allowed_root)

    resp = client.get(
        "/projects/1/repos/1/files/search",
        query_string={"q": "term", "type": "py; rm -rf"},
    )
    assert resp.status_code == 200
    assert b"unsupported characters" in resp.data


def test_search_unknown_repo_returns_404(client, app):
    allowed_root = app.config["allowed_root"]
    _create_project_with_repo(client, allowed_root)

    resp = client.get("/projects/1/repos/999/files/search", query_string={"q": "term"})
    assert resp.status_code == 404


def test_validate_query_rejects_empty_string():
    provider = None
    with pytest.raises(search.InvalidSearchQueryError):
        search.search(provider, "/tmp", "   ")


def test_validate_query_rejects_overlong_string():
    provider = None
    with pytest.raises(search.InvalidSearchQueryError):
        search.search(provider, "/tmp", "x" * (search.MAX_QUERY_LENGTH + 1))


def test_search_uses_cache_for_repeated_query(app, tmp_path):
    repo_path = tmp_path / "cache-repo"
    repo_path.mkdir()
    (repo_path / "file.txt").write_text("cache-term\n")

    provider = HostExecutionProvider(app.config["DATABASE_PATH"], (str(tmp_path),))

    first = search.search(provider, str(repo_path), "cache-term")
    (repo_path / "file.txt").write_text("cache-term\ncache-term\n")
    second = search.search(provider, str(repo_path), "cache-term")

    assert first == second
