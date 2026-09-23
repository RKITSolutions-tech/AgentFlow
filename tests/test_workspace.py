import os

import pytest

from app.security import PathNotAllowedError
from app.workspace import files


def _create_project_with_repo(client, allowed_root, repo_name="repo-a"):
    repo_path = os.path.join(allowed_root, repo_name)
    os.makedirs(repo_path)

    client.post("/projects/new", data={"name": "Proj", "description": ""})
    client.post(
        "/projects/1/repositories",
        data={"name": repo_name, "path": repo_path, "is_primary": "on"},
    )
    return repo_path


def test_browse_lists_files_and_directories(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)

    os.makedirs(os.path.join(repo_path, "subdir"))
    with open(os.path.join(repo_path, "README.md"), "w") as fh:
        fh.write("hello")

    resp = client.get("/projects/1/repos/1/files")
    assert resp.status_code == 200
    assert b"subdir/" in resp.data
    assert b"README.md" in resp.data


def test_browse_subdirectory(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)

    os.makedirs(os.path.join(repo_path, "subdir"))
    with open(os.path.join(repo_path, "subdir", "nested.txt"), "w") as fh:
        fh.write("nested content")

    resp = client.get("/projects/1/repos/1/files", query_string={"path": "subdir"})
    assert resp.status_code == 200
    assert b"nested.txt" in resp.data


def test_view_file_shows_content(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)

    with open(os.path.join(repo_path, "hello.txt"), "w") as fh:
        fh.write("hello world")

    resp = client.get("/projects/1/repos/1/files/view", query_string={"path": "hello.txt"})
    assert resp.status_code == 200
    assert b"hello world" in resp.data


def test_edit_file_saves_content(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)

    with open(os.path.join(repo_path, "hello.txt"), "w") as fh:
        fh.write("original")

    resp = client.post(
        "/projects/1/repos/1/files/edit",
        data={"path": "hello.txt", "content": "updated content"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated content" in resp.data

    with open(os.path.join(repo_path, "hello.txt")) as fh:
        assert fh.read() == "updated content"


def test_binary_file_is_not_displayed(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)

    with open(os.path.join(repo_path, "image.bin"), "wb") as fh:
        fh.write(b"\x00\x01\x02\x03binarydata")

    resp = client.get("/projects/1/repos/1/files/view", query_string={"path": "image.bin"})
    assert resp.status_code == 200
    assert b"appears to be binary" in resp.data


def test_large_file_is_not_displayed(client, app, monkeypatch):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)

    monkeypatch.setattr(files, "MAX_FILE_SIZE", 10)
    with open(os.path.join(repo_path, "big.txt"), "w") as fh:
        fh.write("x" * 100)

    resp = client.get("/projects/1/repos/1/files/view", query_string={"path": "big.txt"})
    assert resp.status_code == 200
    assert b"too large to display" in resp.data


def test_binary_file_cannot_be_edited(client, app):
    allowed_root = app.config["allowed_root"]
    repo_path = _create_project_with_repo(client, allowed_root)

    with open(os.path.join(repo_path, "image.bin"), "wb") as fh:
        fh.write(b"\x00\x01\x02\x03binarydata")

    resp = client.get(
        "/projects/1/repos/1/files/edit",
        query_string={"path": "image.bin"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"cannot be edited in the browser" in resp.data


def test_path_traversal_via_query_param_is_rejected(client, app):
    allowed_root = app.config["allowed_root"]
    _create_project_with_repo(client, allowed_root)

    resp = client.get("/projects/1/repos/1/files", query_string={"path": "../../etc"})
    assert resp.status_code == 404


def test_resolve_path_rejects_escape():
    with pytest.raises(PathNotAllowedError):
        files.resolve_path("/tmp/some-repo", "../../etc/passwd")


def test_unknown_repo_returns_404(client, app):
    allowed_root = app.config["allowed_root"]
    _create_project_with_repo(client, allowed_root)

    resp = client.get("/projects/1/repos/999/files")
    assert resp.status_code == 404
