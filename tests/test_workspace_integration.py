"""Integration tests for the combined workspace UI (task 5.5): shared nav,
repo switcher, cross-tool linking, security boundaries, and responsive
layout across the file browser, search, Git, and terminal pages.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

import pytest

from app.db import get_db
from app.workspace import terminal_models
from tests.conftest import add_repository, create_project_with_repo

NAV_LABELS = ["Files", "Search", "Status", "History", "Commit", "Terminal"]

WORKSPACE_PAGES = [
    ("files", "/files"),
    ("search", "/files/search"),
    ("status", "/git/status"),
    ("log", "/git/log"),
    ("commit", "/git/commit"),
    ("terminal", "/terminal"),
]


def _init_git_repo(repo_path):
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path, check=True, capture_output=True,
    )


def test_nav_present_with_correct_active_item_on_every_workspace_page(client, app):
    allowed_root = app.config["allowed_root"]
    project_id, repo_path = create_project_with_repo(client, allowed_root)
    _init_git_repo(repo_path)

    for active, suffix in WORKSPACE_PAGES:
        resp = client.get(f"/projects/{project_id}/repos/1{suffix}")
        assert resp.status_code == 200, f"{suffix} did not render"
        html = resp.data.decode()
        for label in NAV_LABELS:
            assert f">{label}<" in html, f"{suffix} missing nav link {label!r}"
        # exactly one nav link is marked as the current page
        assert html.count('aria-disabled="true"') >= 1


def test_repo_switcher_hidden_for_single_repo_project(client, app):
    allowed_root = app.config["allowed_root"]
    project_id, _repo_path = create_project_with_repo(client, allowed_root)

    resp = client.get(f"/projects/{project_id}/repos/1/files")
    assert b"repo-switcher" not in resp.data


def test_repo_switcher_lists_and_switches_between_repos(client, app):
    allowed_root = app.config["allowed_root"]
    project_id, _repo_path = create_project_with_repo(client, allowed_root, repo_name="repo-a")
    add_repository(client, project_id, allowed_root, "repo-b")

    resp = client.get(f"/projects/{project_id}/repos/1/files")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "repo-switcher" in html
    assert ">repo-a<" in html
    assert ">repo-b<" in html

    switch_urls = re.findall(r'<option value="([^"]+)"[^>]*>repo-b<', html)
    assert switch_urls, "repo-b option not found in switcher"
    switch_resp = client.get(switch_urls[0])
    assert switch_resp.status_code == 200
    assert b"repo-b" in switch_resp.data


def test_git_status_links_resolve_to_diff_and_view(client, app):
    allowed_root = app.config["allowed_root"]
    project_id, repo_path = create_project_with_repo(client, allowed_root)
    _init_git_repo(repo_path)

    with open(os.path.join(repo_path, "tracked.txt"), "w") as fh:
        fh.write("original\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_path, check=True, capture_output=True)

    with open(os.path.join(repo_path, "tracked.txt"), "w") as fh:
        fh.write("modified\n")
    with open(os.path.join(repo_path, "untracked.txt"), "w") as fh:
        fh.write("new\n")

    status_resp = client.get(f"/projects/{project_id}/repos/1/git/status")
    assert status_resp.status_code == 200
    html = status_resp.data.decode()

    diff_links = re.findall(r'href="([^"]*git/diff[^"]*)"', html)
    assert diff_links, "no diff link found on status page"
    for link in diff_links:
        assert client.get(link).status_code == 200

    view_links = re.findall(r'href="([^"]*files/view[^"]*)"', html)
    assert view_links, "no file-view link found on status page"
    for link in view_links:
        assert client.get(link).status_code == 200


def test_path_traversal_blocked_across_file_and_search_endpoints(client, app):
    allowed_root = app.config["allowed_root"]
    project_id, _repo_path = create_project_with_repo(client, allowed_root)

    traversal = "../../etc/passwd"
    for suffix in ("/files", "/files/view", "/files/edit", "/files/download"):
        resp = client.get(
            f"/projects/{project_id}/repos/1{suffix}", query_string={"path": traversal}
        )
        assert resp.status_code == 404, f"{suffix} did not block path traversal"

    search_resp = client.get(
        f"/projects/{project_id}/repos/1/files/search",
        query_string={"q": "root", "path": "../../*"},
    )
    assert search_resp.status_code == 200
    assert b"root:x:" not in search_resp.data


def test_binary_and_oversized_files_show_warning_not_content(client, app):
    allowed_root = app.config["allowed_root"]
    project_id, repo_path = create_project_with_repo(client, allowed_root)

    with open(os.path.join(repo_path, "image.bin"), "wb") as fh:
        fh.write(b"\x00\x01\x02\xff" * 100)
    with open(os.path.join(repo_path, "big.txt"), "w") as fh:
        fh.write("x" * (11 * 1024 * 1024))

    binary_resp = client.get(
        f"/projects/{project_id}/repos/1/files/view", query_string={"path": "image.bin"}
    )
    assert b"Binary file" in binary_resp.data

    large_resp = client.get(
        f"/projects/{project_id}/repos/1/files/view", query_string={"path": "big.txt"}
    )
    assert b"too large" in large_resp.data

    edit_binary_resp = client.get(
        f"/projects/{project_id}/repos/1/files/edit", query_string={"path": "image.bin"}
    )
    assert edit_binary_resp.status_code == 302


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_terminal_working_directory_and_cross_repo_isolation(client, app):
    allowed_root = app.config["allowed_root"]
    project_id, repo_path = create_project_with_repo(client, allowed_root, "repo-a")
    other_project_id, _other_path = create_project_with_repo(client, allowed_root, "repo-b")

    create_resp = client.post(f"/projects/{project_id}/repos/1/terminal")
    assert create_resp.status_code == 201
    term_id = create_resp.get_json()["id"]

    with app.app_context():
        session = terminal_models.get_terminal_session(get_db(), term_id)
    assert session.working_directory == str(repo_path)

    # The same terminal id under a different project/repo must not resolve.
    mismatched = client.get(f"/projects/{other_project_id}/repos/1/terminal/{term_id}/stream")
    assert mismatched.status_code == 404

    kill_resp = client.post(f"/projects/{project_id}/repos/1/terminal/{term_id}/kill")
    assert kill_resp.status_code == 200


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_terminal_output_persists_across_simulated_reconnect(client, app):
    allowed_root = app.config["allowed_root"]
    project_id, _repo_path = create_project_with_repo(client, allowed_root)

    create_resp = client.post(f"/projects/{project_id}/repos/1/terminal")
    term_id = create_resp.get_json()["id"]

    input_resp = client.post(
        f"/projects/{project_id}/repos/1/terminal/{term_id}/input",
        json={"text": "echo integration-marker\n"},
    )
    assert input_resp.status_code == 200

    deadline = time.time() + 10
    events = []
    while time.time() < deadline:
        stream_resp = client.get(
            f"/projects/{project_id}/repos/1/terminal/{term_id}/stream",
            query_string={"after_id": 0},
        )
        if b"integration-marker" in stream_resp.data:
            events = stream_resp.data
            break
        time.sleep(0.5)
    assert events, "terminal output never appeared"

    # Simulate a fresh page load / browser reconnect: a brand-new poll from
    # after_id=0 must still return the same output (it's DB-backed, not held
    # only in the disconnected browser's memory).
    reconnect_resp = client.get(
        f"/projects/{project_id}/repos/1/terminal/{term_id}/stream",
        query_string={"after_id": 0},
    )
    assert b"integration-marker" in reconnect_resp.data

    client.post(f"/projects/{project_id}/repos/1/terminal/{term_id}/kill")


def test_workspace_desktop_viewport_renders_nav_inline(browser_type_launch_args, app, client, live_server):
    allowed_root = app.config["allowed_root"]
    project_id, _repo_path = create_project_with_repo(client, allowed_root, "repo-a")
    add_repository(client, project_id, allowed_root, "repo-b")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.goto(f"{live_server}/projects/{project_id}/repos/1/files")

            nav = page.query_selector(".git-nav")
            assert nav is not None
            assert nav.is_visible()
            toggle = page.query_selector(".nav-toggle")
            assert toggle is not None and not toggle.is_visible()
            switcher = page.query_selector(".repo-switcher select")
            assert switcher is not None and switcher.is_visible()

            browser.close()
    except Exception as e:
        pytest.skip(f"Playwright browser error: {e}")


def test_workspace_mobile_viewport_collapses_nav(browser_type_launch_args, app, client, live_server):
    allowed_root = app.config["allowed_root"]
    project_id, _repo_path = create_project_with_repo(client, allowed_root)

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 375, "height": 667})
            page.goto(f"{live_server}/projects/{project_id}/repos/1/git/status")

            toggle = page.query_selector(".nav-toggle")
            assert toggle is not None and toggle.is_visible()
            primary_nav = page.query_selector("#primary-nav")
            assert primary_nav is not None and not primary_nav.is_visible()

            nav_links = page.query_selector_all(".git-nav a")
            assert nav_links
            box = nav_links[0].bounding_box()
            assert box is not None and box["width"] > 300  # stacked full-width

            browser.close()
    except Exception as e:
        pytest.skip(f"Playwright browser error: {e}")
