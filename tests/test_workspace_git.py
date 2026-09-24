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
