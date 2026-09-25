import io
import os

import pytest

from app.security import PathNotAllowedError
from app.workspace import files
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}
BASE = "/projects/1/repos/1/files"


@pytest.fixture
def repo(client, app):
    _pid, path = create_project_with_repo(client, app.config["allowed_root"], "ops")
    os.makedirs(os.path.join(path, "sub"))
    os.makedirs(os.path.join(path, ".git"))
    return path


def test_create_file_and_folder(client, repo):
    r = client.post(f"{BASE}/create", data={"path": "", "name": "a.txt", "kind": "file"}, headers=AJAX)
    assert r.status_code == 200 and r.get_json()["path"] == "a.txt"
    assert os.path.isfile(os.path.join(repo, "a.txt"))
    r = client.post(f"{BASE}/create", data={"path": "sub", "name": "d", "kind": "folder"}, headers=AJAX)
    assert r.status_code == 200
    assert os.path.isdir(os.path.join(repo, "sub", "d"))


def test_create_rejects_existing_bad_names_and_missing_parent(client, repo):
    open(os.path.join(repo, "a.txt"), "w").close()
    def make(path, name):
        return client.post(f"{BASE}/create", data={"path": path, "name": name, "kind": "file"}, headers=AJAX)
    assert make("", "a.txt").status_code == 409
    assert make("", "").status_code == 400
    assert make("", "..").status_code == 400
    assert make("", "x/y").status_code == 400
    assert make("", "..\\y") .status_code == 400
    assert make("nope", "x").status_code == 404
    assert make("../..", "x").status_code == 400
    assert make(".git", "hook").status_code == 400
    assert not os.path.exists(os.path.join(repo, "..", "x"))


def test_rename_file_and_folder(client, repo):
    with open(os.path.join(repo, "sub", "f.txt"), "w") as fh:
        fh.write("data")
    r = client.post(f"{BASE}/rename", data={"path": "sub/f.txt", "new_name": "g.txt"}, headers=AJAX)
    assert r.get_json()["path"] == os.path.join("sub", "g.txt")
    assert open(os.path.join(repo, "sub", "g.txt")).read() == "data"
    assert client.post(f"{BASE}/rename", data={"path": "sub", "new_name": "sub2"}, headers=AJAX).status_code == 200
    assert os.path.isdir(os.path.join(repo, "sub2"))


def test_rename_conflicts_and_escapes(client, repo):
    for n in ("a", "b"):
        open(os.path.join(repo, n), "w").close()
    def rename(path, new):
        return client.post(f"{BASE}/rename", data={"path": path, "new_name": new}, headers=AJAX)
    assert rename("a", "b").status_code == 409
    assert rename("a", "../evil").status_code == 400
    assert rename("missing", "z").status_code == 404
    assert rename("", "z").status_code == 400
    assert rename(".git", "z").status_code == 400
    assert rename("a", ".git").status_code == 400
    assert rename("../../etc/passwd", "z").status_code in (400, 404)


def test_delete_file_folder_and_protections(client, repo):
    os.makedirs(os.path.join(repo, "sub", "deep"))
    open(os.path.join(repo, "sub", "deep", "x"), "w").close()
    open(os.path.join(repo, "f"), "w").close()
    assert client.post(f"{BASE}/delete", query_string={"path": "f"}, headers=AJAX).status_code == 200
    assert not os.path.exists(os.path.join(repo, "f"))
    assert client.post(f"{BASE}/delete", query_string={"path": "sub"}, headers=AJAX).status_code == 200
    assert not os.path.exists(os.path.join(repo, "sub"))
    assert client.post(f"{BASE}/delete", query_string={"path": ""}, headers=AJAX).status_code == 400
    assert client.post(f"{BASE}/delete", query_string={"path": ".git"}, headers=AJAX).status_code == 400
    assert client.post(f"{BASE}/delete", query_string={"path": "gone"}, headers=AJAX).status_code == 404
    assert os.path.isdir(os.path.join(repo, ".git"))


def test_delete_symlink_leaves_target(client, repo):
    target = os.path.join(repo, "sub", "keep.txt")
    open(target, "w").close()
    os.symlink(target, os.path.join(repo, "link"))
    assert client.post(f"{BASE}/delete", query_string={"path": "link"}, headers=AJAX).status_code == 200
    assert not os.path.lexists(os.path.join(repo, "link"))
    assert os.path.exists(target)


def test_symlink_escape_blocked(client, repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, os.path.join(repo, "portal"))
    r = client.post(f"{BASE}/create", data={"path": "portal", "name": "x", "kind": "file"}, headers=AJAX)
    assert r.status_code == 400
    assert not (outside / "x").exists()


def test_upload_saves_files_and_refuses_overwrite(client, repo):
    data = {"path": "sub", "files": [(io.BytesIO(b"one"), "one.bin"), (io.BytesIO(b"two"), "two.bin")]}
    r = client.post(f"{BASE}/upload", data=data, content_type="multipart/form-data", headers=AJAX)
    assert r.status_code == 200 and "2 files" in r.get_json()["message"]
    assert open(os.path.join(repo, "sub", "two.bin"), "rb").read() == b"two"
    again = client.post(
        f"{BASE}/upload",
        data={"path": "sub", "files": (io.BytesIO(b"x"), "one.bin")},
        content_type="multipart/form-data", headers=AJAX,
    )
    assert again.status_code == 409
    assert open(os.path.join(repo, "sub", "one.bin"), "rb").read() == b"one"


def test_upload_size_limit_and_traversal_filename(client, app, repo):
    app.config["MAX_UPLOAD_BYTES"] = 10
    big = client.post(
        f"{BASE}/upload",
        data={"path": "", "files": (io.BytesIO(b"x" * 11), "big.bin")},
        content_type="multipart/form-data", headers=AJAX,
    )
    assert big.status_code == 413
    assert not os.path.exists(os.path.join(repo, "big.bin"))
    sneaky = client.post(
        f"{BASE}/upload",
        data={"path": "", "files": (io.BytesIO(b"ok"), "../../escape.txt")},
        content_type="multipart/form-data", headers=AJAX,
    )
    assert sneaky.status_code == 200
    assert os.path.isfile(os.path.join(repo, "escape.txt"))
    assert not os.path.exists(os.path.join(repo, "..", "escape.txt"))


def test_upload_requires_a_file(client, repo):
    r = client.post(f"{BASE}/upload", data={"path": ""}, headers=AJAX)
    assert r.status_code == 400


def test_non_ajax_falls_back_to_flash_redirect(client, repo):
    r = client.post(f"{BASE}/create", data={"path": "", "name": "n.txt", "kind": "file"})
    assert r.status_code == 302
    page = client.get(BASE).data
    assert b"n.txt" in page


def test_browse_offers_file_actions(client, repo):
    open(os.path.join(repo, "f.txt"), "w").close()
    page = client.get(BASE).data
    assert b"data-rename-url" in page and b"New folder" in page and b'name="files"' in page


def test_unknown_repo_404(client):
    assert client.post("/projects/9/repos/9/files/create", data={"name": "x"}).status_code == 404


def test_files_module_rejects_escape(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    with pytest.raises(PathNotAllowedError):
        files.create_file(str(root), "..", "x")
