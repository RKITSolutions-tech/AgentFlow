import os
import subprocess
from pathlib import Path

import pytest

from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.projects import models as project_models
from app.workspace import git
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}
BASE = "/projects/1/repos/1/git/worktrees"


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


@pytest.fixture
def repo(client, app):
    _pid, path = create_project_with_repo(client, app.config["allowed_root"], "wt-repo")
    _git(path, "init", "-q", "-b", "main")
    Path(path, "a.txt").write_text("one\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture
def provider(app):
    return HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])


def _roots(app):
    return app.config["ALLOWED_PROJECT_ROOTS"]


def test_list_starts_with_main(app, repo, provider):
    trees = git.list_worktrees(provider, repo, allowed_roots=_roots(app))
    assert len(trees) == 1 and trees[0].is_main and trees[0].branch == "main"


def test_add_list_remove_cycle(app, repo, provider):
    path = git.add_worktree(provider, repo, "feature/x", allowed_roots=_roots(app))
    assert path == os.path.realpath(repo) + ".worktrees/feature-x"
    assert os.path.isfile(os.path.join(path, "a.txt"))
    trees = git.list_worktrees(provider, repo, allowed_roots=_roots(app))
    assert [t.branch for t in trees] == ["main", "feature/x"] and not trees[1].is_main
    git.remove_worktree(provider, repo, path, allowed_roots=_roots(app))
    assert not os.path.exists(path)
    assert len(git.list_worktrees(provider, repo, allowed_roots=_roots(app))) == 1


def test_add_rejects_bad_names_existing_paths_and_outside_roots(app, repo, provider, tmp_path):
    for bad in ("", "-x", "a..b", "x.lock"):
        with pytest.raises(ValueError):
            git.add_worktree(provider, repo, bad, allowed_roots=_roots(app))
    with pytest.raises(ValueError):
        git.add_worktree(provider, repo, "ok", path=str(tmp_path / "elsewhere"), allowed_roots=_roots(app))
    git.add_worktree(provider, repo, "dup", allowed_roots=_roots(app))
    with pytest.raises(git.GitCommandError):
        git.add_worktree(provider, repo, "dup", allowed_roots=_roots(app))


def test_remove_protects_main_unknown_and_dirty(app, repo, provider):
    with pytest.raises(git.GitCommandError, match="main worktree"):
        git.remove_worktree(provider, repo, repo, allowed_roots=_roots(app))
    with pytest.raises(git.GitCommandError, match="not a worktree"):
        git.remove_worktree(provider, repo, "/tmp", allowed_roots=_roots(app))
    path = git.add_worktree(provider, repo, "dirty", allowed_roots=_roots(app))
    Path(path, "new.txt").write_text("x")
    _git(path, "add", "new.txt")
    with pytest.raises(git.GitCommandError):
        git.remove_worktree(provider, repo, path, allowed_roots=_roots(app))
    assert os.path.isdir(path)
    git.remove_worktree(provider, repo, path, force=True, allowed_roots=_roots(app))
    assert not os.path.exists(path)


def test_merge_worktree_branch(app, repo, provider):
    path = git.add_worktree(provider, repo, "topic", allowed_roots=_roots(app))
    Path(path, "b.txt").write_text("from topic\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "topic work")
    git.merge_branch(provider, repo, "topic", allowed_roots=_roots(app))
    assert Path(repo, "b.txt").read_text() == "from topic\n"


def test_merge_refuses_dirty_and_aborts_conflicts(app, repo, provider):
    path = git.add_worktree(provider, repo, "clash", allowed_roots=_roots(app))
    Path(path, "a.txt").write_text("topic\n")
    _git(path, "commit", "-aq", "-m", "topic edit")
    Path(repo, "a.txt").write_text("main edit\n")
    with pytest.raises(git.GitCommandError, match="uncommitted"):
        git.merge_branch(provider, repo, "clash", allowed_roots=_roots(app))
    _git(repo, "commit", "-aq", "-m", "main edit")
    with pytest.raises(git.GitCommandError, match="aborted"):
        git.merge_branch(provider, repo, "clash", allowed_roots=_roots(app))
    assert Path(repo, "a.txt").read_text() == "main edit\n"
    assert not os.path.exists(os.path.join(repo, ".git", "MERGE_HEAD"))


# -- routes ------------------------------------------------------------------


def test_worktrees_page_and_tab(client, repo):
    page = client.get(BASE).data
    assert b"Worktrees" in page and b"(main)" in page
    assert b"/git/worktrees" in client.get("/projects/1/repos/1/files").data


def test_create_registers_repository_and_remove_unregisters(client, app, repo):
    r = client.post(BASE, data={"branch": "ui/one"}, headers=AJAX)
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        project = project_models.get_project(get_db(), 1)
        names = [x.name for x in project.repositories]
        wt = next(x for x in project.repositories if x.name == "ui-one")
    assert wt.path.endswith("wt-repo.worktrees/ui-one") and len(names) == 2
    page = client.get(BASE).data
    assert b"Open" in page and b"Merge into this" in page
    r = client.post(BASE + "/remove", query_string={"path": wt.path}, headers=AJAX)
    assert r.status_code == 200
    with app.app_context():
        assert [x.name for x in project_models.get_project(get_db(), 1).repositories] == ["wt-repo"]


def test_create_without_registering_then_add_as_repository(client, app, repo):
    client.post(BASE, data={"branch": "solo", "register": "0"}, headers=AJAX)
    with app.app_context():
        assert len(project_models.get_project(get_db(), 1).repositories) == 1
    path = os.path.realpath(repo) + ".worktrees/solo"
    r = client.post(BASE + "/register", query_string={"path": path}, headers=AJAX)
    assert r.status_code == 200
    with app.app_context():
        assert len(project_models.get_project(get_db(), 1).repositories) == 2
    bad = client.post(BASE + "/register", query_string={"path": "/tmp"}, headers=AJAX)
    assert bad.status_code == 400


def test_route_errors_are_reported(client, repo):
    assert client.post(BASE, data={"branch": "-bad"}, headers=AJAX).status_code == 400
    assert client.post(BASE + "/remove", query_string={"path": repo}, headers=AJAX).status_code == 400
    assert client.post(BASE + "/merge", query_string={"branch": "nonexistent"}, headers=AJAX).status_code == 400
    r = client.post(BASE, data={"branch": "-bad"})  # non-AJAX: flash + redirect
    assert r.status_code == 302


def test_merge_route(client, app, repo, provider):
    client.post(BASE, data={"branch": "m1", "register": "0"}, headers=AJAX)
    path = os.path.realpath(repo) + ".worktrees/m1"
    Path(path, "m.txt").write_text("m\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "m")
    r = client.post(BASE + "/merge", query_string={"branch": "m1"}, headers=AJAX)
    assert r.status_code == 200 and Path(repo, "m.txt").exists()
