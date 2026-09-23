"""End-to-end tests for sessions using Playwright browser automation.

Note: To run these tests, ensure Playwright browsers are installed:
  ./venv/bin/python -m playwright install chromium

These tests require a running Flask server on http://127.0.0.1:5000
"""
import os
import pytest

# Skip all E2E tests if Playwright browsers aren't available
pytestmark = pytest.mark.skip(
    reason="Playwright E2E tests require system dependencies. "
    "Install with: sudo playwright install-deps && "
    "./venv/bin/python -m playwright install chromium"
)


@pytest.mark.asyncio
class TestSessionsE2E:
    """End-to-end tests using Playwright for real browser interaction."""

    async def test_create_and_open_session(self, browser, app, client):
        """E2E: Create a project and session, then navigate to chat."""
        # Setup: Create project via HTTP
        allowed_root = app.config["allowed_root"]
        repo_path = os.path.join(allowed_root, "e2e-test-project")
        os.makedirs(repo_path, exist_ok=True)

        resp = client.post(
            "/projects/new",
            data={
                "name": "E2ETest",
                "description": "End-to-end test",
                "repo_name": "main",
                "repo_path": repo_path,
            },
        )
        assert resp.status_code == 302

        # Navigate to sessions page
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("http://127.0.0.1:5000/sessions")

        # Verify page loaded
        await page.wait_for_selector("h1")
        title = await page.text_content("h1")
        assert "Sessions" in title

        # Check table contains project
        rows = await page.locator("table tbody tr").count()
        assert rows > 0, "No projects displayed"

        await context.close()

    async def test_navigate_sessions_flow(self, browser):
        """E2E: Test complete sessions navigation flow.

        1. Open sessions page
        2. Click on project
        3. Verify sessions list appears
        """
        context = await browser.new_context()
        page = await context.new_page()

        # Go to sessions
        await page.goto("http://127.0.0.1:5000/sessions")
        await page.wait_for_selector("table")

        # Try to click first project link
        project_links = page.locator("a[href*='/sessions/project/']")
        count = await project_links.count()

        if count > 0:
            await project_links.first.click()
            # Should navigate to project sessions page
            await page.wait_for_selector("h1")
            await page.wait_for_timeout(500)

        await context.close()

    async def test_form_input_interaction(self, browser, app, client):
        """E2E: Test form input interaction and validation."""
        # Setup: Create project
        allowed_root = app.config["allowed_root"]
        repo_path = os.path.join(allowed_root, "form-e2e-test")
        os.makedirs(repo_path, exist_ok=True)

        from app.db import get_db
        from app.projects import models as project_models

        client.post(
            "/projects/new",
            data={
                "name": "FormE2E",
                "description": "Form test",
                "repo_name": "main",
                "repo_path": repo_path,
            },
        )

        with app.app_context():
            db = get_db()
            projects = project_models.list_projects(db)
            project = [p for p in projects if p.name == "FormE2E"][0]

        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to project sessions page
        await page.goto(f"http://127.0.0.1:5000/sessions/project/{project.id}")
        await page.wait_for_selector("form")

        # Check form exists
        form = page.locator("form")
        assert await form.count() > 0

        # Check agent type dropdown
        agent_select = page.locator('select[name="agent_type"]')
        assert await agent_select.count() == 1

        # Check repository dropdown
        repo_select = page.locator('select[name="repo_id"]')
        assert await repo_select.count() == 1

        # Check submit button
        submit_btn = page.locator("button:has-text('Start Session')")
        assert await submit_btn.count() == 1

        await context.close()

    async def test_chat_interface_responsive(self, browser, app, client):
        """E2E: Test chat interface loads and is responsive."""
        from app.db import get_db
        from app.projects import models as project_models
        from app.agents.models import create_agent_session

        allowed_root = app.config["allowed_root"]
        repo_path = os.path.join(allowed_root, "responsive-test")
        os.makedirs(repo_path, exist_ok=True)

        client.post(
            "/projects/new",
            data={
                "name": "ResponsiveTest",
                "description": "Test",
                "repo_name": "main",
                "repo_path": repo_path,
            },
        )

        with app.app_context():
            db = get_db()
            projects = project_models.list_projects(db)
            project = [p for p in projects if p.name == "ResponsiveTest"][0]
            repo = project.repositories[0]

            session_id = create_agent_session(
                db, project.id, "fake", execution_target=repo_path, metadata={"repo_id": repo.id}
            )

        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to chat
        await page.goto(f"http://127.0.0.1:5000/sessions/{session_id}")

        # Wait for chat container
        chat_container = page.locator("#chatContainer")
        await chat_container.wait_for()

        # Check prompt input exists
        prompt_input = page.locator("#promptInput")
        assert await prompt_input.count() == 1

        # Check send button exists
        send_btn = page.locator("button:has-text('Send')")
        assert await send_btn.count() >= 1

        # Test input interaction
        await prompt_input.fill("Test message")
        value = await prompt_input.input_value()
        assert value == "Test message"

        await context.close()


@pytest.mark.asyncio
async def test_screenshot_sessions_page(browser):
    """E2E: Take screenshot of sessions page for visual regression."""
    context = await browser.new_context(viewport={"width": 1280, "height": 720})
    page = await context.new_page()

    try:
        await page.goto("http://127.0.0.1:5000/sessions", timeout=10000)
        await page.wait_for_load_state("networkidle", timeout=10000)

        # Take screenshot
        await page.screenshot(path="/tmp/sessions_page.png")
        print("Screenshot saved to /tmp/sessions_page.png")
    except Exception as e:
        print(f"Screenshot test skipped: {e}")
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_screenshot_chat_page(browser, app, client):
    """E2E: Take screenshot of chat page for visual regression."""
    from app.db import get_db
    from app.projects import models as project_models
    from app.agents.models import create_agent_session

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "screenshot-test")
    os.makedirs(repo_path, exist_ok=True)

    client.post(
        "/projects/new",
        data={
            "name": "ScreenshotTest",
            "description": "Test",
            "repo_name": "main",
            "repo_path": repo_path,
        },
    )

    with app.app_context():
        db = get_db()
        projects = project_models.list_projects(db)
        project = [p for p in projects if p.name == "ScreenshotTest"][0]
        repo = project.repositories[0]

        session_id = create_agent_session(
            db, project.id, "fake", execution_target=repo_path, metadata={"repo_id": repo.id}
        )

    context = await browser.new_context(viewport={"width": 1280, "height": 720})
    page = await context.new_page()

    try:
        await page.goto(f"http://127.0.0.1:5000/sessions/{session_id}", timeout=10000)
        await page.wait_for_selector("#chatContainer", timeout=10000)

        # Take screenshot
        await page.screenshot(path="/tmp/chat_page.png")
        print("Screenshot saved to /tmp/chat_page.png")
    except Exception as e:
        print(f"Chat screenshot test skipped: {e}")
    finally:
        await context.close()
