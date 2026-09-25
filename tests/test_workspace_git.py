import subprocess
from pathlib import Path

import pytest
from app.workspace import git
from app.execution.host import HostExecutionProvider


@pytest.fixture
def provider(app):
    return HostExecutionProvider(
        app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"]
    )


@pytest.fixture
def tmp_repo(app):
    repo_path = Path(app.config["allowed_root"]) / "test-repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path, check=True, capture_output=True,
    )
    return repo_path


class TestGitStatus:
    def test_git_status_in_repo(self, client, tmp_repo, app, provider):
        with app.app_context():
            status = git.status(
                provider, tmp_repo, allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"]
            )

        assert status is not None
        assert status.branch in (None, "main", "master")  # varies by git config
        assert isinstance(status.staged, list)
        assert isinstance(status.unstaged, list)
        assert isinstance(status.untracked, list)

    def test_git_status_with_changes(self, client, tmp_repo, app, provider):
        # Create a new file
        new_file = tmp_repo / "new_file.txt"
        new_file.write_text("test content")

        with app.app_context():
            status = git.status(
                provider, str(tmp_repo), allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"]
            )

        assert any(f.path == "new_file.txt" for f in status.untracked)


class TestGitDiff:
    def test_git_diff_no_changes(self, client, tmp_repo, app, provider):
        with app.app_context():
            diff = git.diff(provider, str(tmp_repo), allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])

        assert diff is None or len(diff.lines) == 0

    def test_git_diff_with_changes(self, client, tmp_repo, app, provider):
        # Create and modify a file
        test_file = tmp_repo / "test.txt"
        test_file.write_text("original content")

        # Initialize git repo and commit file
        import subprocess

        subprocess.run(["git", "init"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "test.txt"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_repo, check=True, capture_output=True)

        # Modify the file
        test_file.write_text("modified content")

        with app.app_context():
            diff = git.diff(
                provider, str(tmp_repo), path="test.txt", allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"]
            )

        assert diff is not None
        assert diff.path == "test.txt"
        assert any(line.type == "removed" for line in diff.lines)
        assert any(line.type == "added" for line in diff.lines)


class TestGitLog:
    def test_git_log_empty_repo(self, client, tmp_repo, app, provider):
        with app.app_context():
            commits = git.log(provider, str(tmp_repo), allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])

        assert commits == []

    def test_git_log_with_commits(self, client, tmp_repo, app, provider):
        import subprocess

        # Initialize repo and create commits
        subprocess.run(["git", "init"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_repo, check=True, capture_output=True)

        (tmp_repo / "file1.txt").write_text("content1")
        subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "first commit"], cwd=tmp_repo, check=True, capture_output=True
        )

        (tmp_repo / "file2.txt").write_text("content2")
        subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "second commit"], cwd=tmp_repo, check=True, capture_output=True
        )

        with app.app_context():
            commits = git.log(
                provider, str(tmp_repo), max_count=10, allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"]
            )

        assert len(commits) >= 2
        assert commits[0].message == "second commit"
        assert commits[1].message == "first commit"
        assert all(c.short_hash and c.hash for c in commits)


class TestGitStaging:
    def test_git_stage_file(self, client, tmp_repo, app, provider):
        import subprocess

        subprocess.run(["git", "init"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_repo, check=True, capture_output=True)

        new_file = tmp_repo / "new.txt"
        new_file.write_text("test")

        with app.app_context():
            git.stage(provider, str(tmp_repo), "new.txt", allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])
            status = git.status(provider, str(tmp_repo), allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])

        assert any(f.path == "new.txt" for f in status.staged)

    def test_git_unstage_file(self, client, tmp_repo, app, provider):
        import subprocess

        subprocess.run(["git", "init"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_repo, check=True, capture_output=True)

        new_file = tmp_repo / "new.txt"
        new_file.write_text("test")
        subprocess.run(["git", "add", "new.txt"], cwd=tmp_repo, check=True, capture_output=True)

        with app.app_context():
            git.unstage(provider, str(tmp_repo), "new.txt", allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])
            status = git.status(provider, str(tmp_repo), allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])

        assert not any(f.path == "new.txt" for f in status.staged)


class TestGitCommit:
    def test_git_commit_empty_message_raises_error(self, client, tmp_repo, app, provider):
        with app.app_context():
            with pytest.raises(ValueError, match="empty"):
                git.commit(provider, str(tmp_repo), "", allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])

    def test_git_commit_message_too_long_raises_error(self, client, tmp_repo, app, provider):
        long_message = "a" * 1001
        with app.app_context():
            with pytest.raises(ValueError, match="1000 characters"):
                git.commit(provider, str(tmp_repo), long_message, allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])

    def test_git_commit_with_staged_changes(self, client, tmp_repo, app, provider):
        import subprocess

        subprocess.run(["git", "init"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_repo, check=True, capture_output=True)

        # Create and stage a file
        new_file = tmp_repo / "new.txt"
        new_file.write_text("test content")
        subprocess.run(["git", "add", "new.txt"], cwd=tmp_repo, check=True, capture_output=True)

        with app.app_context():
            commit_info = git.commit(
                provider, str(tmp_repo), "test: add new file",
                allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"]
            )

        assert commit_info is not None
        assert commit_info.message == "test: add new file"
        assert commit_info.short_hash is not None
        assert commit_info.hash is not None
        assert commit_info.author == "Test User"

    def test_git_commit_amend(self, client, tmp_repo, app, provider):
        import subprocess

        subprocess.run(["git", "init"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_repo, check=True, capture_output=True)

        # Create initial commit
        new_file = tmp_repo / "file.txt"
        new_file.write_text("content")
        subprocess.run(["git", "add", "file.txt"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_repo, check=True, capture_output=True)

        # Modify file
        new_file.write_text("modified content")
        subprocess.run(["git", "add", "file.txt"], cwd=tmp_repo, check=True, capture_output=True)

        with app.app_context():
            commit_info = git.commit(
                provider, str(tmp_repo), "initial: updated message",
                amend=True,
                allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"]
            )
            commits = git.log(provider, str(tmp_repo), max_count=1, allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])

        assert commit_info is not None
        assert commit_info.message == "initial: updated message"
        assert len(commits) == 1
        assert commits[0].message == "initial: updated message"


def _commit_file(repo, name="a.txt"):
    (repo / name).write_text("x")
    subprocess.run(["git", "add", name], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


class TestBranches:
    def _roots(self, app):
        return app.config["ALLOWED_PROJECT_ROOTS"]

    def test_create_list_checkout_delete(self, tmp_repo, app, provider):
        _commit_file(tmp_repo)
        roots = self._roots(app)
        with app.app_context():
            git.create_branch(provider, tmp_repo, "feature/x", checkout=False, allowed_roots=roots)
            names = {b.name for b in git.list_branches(provider, tmp_repo, allowed_roots=roots)}
            assert "feature/x" in names

            git.checkout_branch(provider, tmp_repo, "feature/x", allowed_roots=roots)
            current = [b for b in git.list_branches(provider, tmp_repo, allowed_roots=roots) if b.current]
            assert [b.name for b in current] == ["feature/x"]

            with pytest.raises(git.GitCommandError):
                git.delete_branch(provider, tmp_repo, "feature/x", allowed_roots=roots)

            other = next(b.name for b in git.list_branches(provider, tmp_repo, allowed_roots=roots) if not b.current)
            git.checkout_branch(provider, tmp_repo, other, allowed_roots=roots)
            git.delete_branch(provider, tmp_repo, "feature/x", allowed_roots=roots)
            names = {b.name for b in git.list_branches(provider, tmp_repo, allowed_roots=roots)}
            assert "feature/x" not in names

    @pytest.mark.parametrize(
        "bad", ["", "-D", "--force", "a..b", "a b", "a//b", "x/", "x.lock", ".hidden", "a;rm", "a/.b"]
    )
    def test_invalid_ref_names_rejected(self, bad):
        with pytest.raises(ValueError):
            git.validate_ref_name(bad)

    def test_checkout_missing_branch_fails(self, tmp_repo, app, provider):
        _commit_file(tmp_repo)
        with app.app_context():
            with pytest.raises(git.GitCommandError):
                git.checkout_branch(provider, tmp_repo, "nope", allowed_roots=self._roots(app))


class TestBranchRoutes:
    AJAX = {"X-Requested-With": "XMLHttpRequest"}

    @pytest.fixture
    def urls(self, client, app):
        from tests.conftest import create_project_with_repo

        project_id, repo_path = create_project_with_repo(
            client, app.config["allowed_root"], "branch-repo"
        )
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        for key, value in (("user.email", "t@t.com"), ("user.name", "T")):
            subprocess.run(["git", "config", key, value], cwd=repo_path, check=True)
        _commit_file(Path(repo_path))
        return f"/projects/{project_id}/repos/1/git/branches"

    def test_page_lists_branches(self, client, urls):
        resp = client.get(urls)
        assert resp.status_code == 200
        assert b"Branches" in resp.data
        assert b"(current)" in resp.data

    def test_create_switch_delete_via_ajax(self, client, urls):
        resp = client.post(urls, data={"name": "feature/a"}, headers=self.AJAX)
        assert resp.status_code == 200
        assert b"feature/a" in client.get(urls).data

        resp = client.post(f"{urls}/feature/a/checkout", headers=self.AJAX)
        assert resp.status_code == 200
        # cannot delete the checked-out branch
        resp = client.post(f"{urls}/feature/a/delete", headers=self.AJAX)
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_remote_routes_and_panel(self, client, urls):
        assert b"No remote configured" in client.get(urls).data
        # no remote configured: fetch is refused cleanly, not a 500
        resp = client.post(urls.replace("/branches", "/fetch"), headers=self.AJAX)
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_invalid_name_rejected(self, client, urls):
        resp = client.post(urls, data={"name": "--force"}, headers=self.AJAX)
        assert resp.status_code == 400
        resp = client.post(f"{urls}/-D/delete", headers=self.AJAX)
        assert resp.status_code == 400


class TestRemotes:
    @pytest.fixture
    def cloned(self, tmp_repo, app):
        """tmp_repo with one commit and a local bare 'origin' (no network needed)."""
        _commit_file(tmp_repo)
        bare = Path(app.config["allowed_root"]) / "origin.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=tmp_repo, check=True)
        return tmp_repo

    def test_publish_then_ahead_behind_and_push(self, cloned, app, provider):
        roots = app.config["ALLOWED_PROJECT_ROOTS"]
        with app.app_context():
            st = git.remote_status(provider, cloned, allowed_roots=roots)
            assert st.remotes == ["origin"] and st.upstream is None

            git.publish_branch(provider, cloned, allowed_roots=roots)
            st = git.remote_status(provider, cloned, allowed_roots=roots)
            assert st.upstream and (st.ahead, st.behind) == (0, 0)

            _commit_file(cloned, "b.txt")
            st = git.remote_status(provider, cloned, allowed_roots=roots)
            assert st.ahead == 1
            git.push(provider, cloned, allowed_roots=roots)
            assert git.remote_status(provider, cloned, allowed_roots=roots).ahead == 0

    def test_fetch_and_pull_fast_forward(self, cloned, app, provider, tmp_path):
        roots = app.config["ALLOWED_PROJECT_ROOTS"]
        with app.app_context():
            git.publish_branch(provider, cloned, allowed_roots=roots)
            other = Path(app.config["allowed_root"]) / "other"
            bare = Path(app.config["allowed_root"]) / "origin.git"
            subprocess.run(["git", "clone", str(bare), str(other)], check=True, capture_output=True)
            for key, value in (("user.email", "o@o.com"), ("user.name", "O")):
                subprocess.run(["git", "config", key, value], cwd=other, check=True)
            _commit_file(other, "c.txt")
            subprocess.run(["git", "push"], cwd=other, check=True, capture_output=True)

            git.fetch(provider, cloned, allowed_roots=roots)
            assert git.remote_status(provider, cloned, allowed_roots=roots).behind == 1
            git.pull(provider, cloned, allowed_roots=roots)
            assert (cloned / "c.txt").exists()

    @pytest.mark.parametrize("bad", ["--upload-pack=x", "nope", "-o"])
    def test_bad_remote_rejected(self, cloned, app, provider, bad):
        with app.app_context():
            with pytest.raises(ValueError):
                git.fetch(provider, cloned, remote=bad, allowed_roots=app.config["ALLOWED_PROJECT_ROOTS"])
