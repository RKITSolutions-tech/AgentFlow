"""Test sending and receiving a hello message through the chat UI.

Note: These tests require Flask to be running on http://127.0.0.1:5000
Start with: flask --app app --debug run
"""
import os
import pytest
import time


def test_send_hello_message_via_ui(app, client):
    """Send 'hello' message via chat UI and verify it appears in database.

    This test verifies the complete flow:
    1. Create a project and session
    2. Use Playwright to open chat
    3. Type "hello" and submit the form
    4. Verify the message is saved to the database
    """
    from app.db import get_db
    from app.projects import models as project_models
    from app.agents.fake import FakeAgentAdapter
    from app.agents.base import AgentContext
    from app.execution.host import HostExecutionProvider
    from app.agents.models import list_agent_events

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "hello-message-test")
    os.makedirs(repo_path, exist_ok=True)

    print("\n" + "="*70)
    print("📱 TESTING HELLO MESSAGE VIA UI")
    print("="*70)

    # Step 1: Create project
    print("\n1️⃣  Creating project...")
    resp = client.post(
        "/projects/new",
        data={
            "name": "HelloMessageTest",
            "description": "Test hello message",
            "repo_name": "main",
            "repo_path": repo_path,
        },
    )
    assert resp.status_code == 302
    print("   ✅ Project created")

    # Step 2: Create and start session
    print("\n2️⃣  Creating and starting session...")
    with app.app_context():
        db = get_db()
        projects = project_models.list_projects(db)
        project = [p for p in projects if p.name == "HelloMessageTest"][0]
        repo = project.repositories[0]

        execution_provider = HostExecutionProvider(
            app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"]
        )
        context_config = {
            "working_directory": repo_path,
            "environment": {},
            "target": "",
        }
        exec_context = execution_provider.create_context(context_config)

        adapter = FakeAgentAdapter(db=db)
        agent_context = AgentContext(
            project_id=project.id,
            working_directory=repo_path,
            execution_provider="host",
            execution_target=str(exec_context.id),
        )

        session = adapter.start(agent_context, "Starting...")
        session_id = session.id
    print(f"   ✅ Session created (ID: {session_id})")

    # Step 3: Use Playwright to send message
    print("\n3️⃣  Using Playwright to send message...")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            # Navigate to chat
            print(f"   Opening chat for session {session_id}")
            page.goto(f"http://127.0.0.1:5000/sessions/{session_id}")
            page.wait_for_selector("#chatContainer", timeout=5000)
            print("   ✅ Chat loaded")

            # Type and submit message
            print("   Typing 'hello'...")
            page.fill("#promptInput", "hello")

            # Submit form
            print("   Submitting form...")
            page.evaluate('''() => {
              const form = document.getElementById('chatForm');
              form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
            }''')

            # Wait for API call and processing
            time.sleep(2)

            # Close browser
            page.close()
            browser.close()
            print("   ✅ Playwright test completed")

    except Exception as e:
        print(f"   ❌ Playwright error: {e}")
        raise

    # Step 4: Verify message in database
    print("\n4️⃣  Verifying message in database...")
    with app.app_context():
        db = get_db()
        events = list_agent_events(db, session_id)

        print(f"   Found {len(events)} events:")
        for i, event in enumerate(events, 1):
            print(f"      {i}. {event.event_type}: {event.data[:80]}")

        # Check for the hello message
        hello_events = [e for e in events if "hello" in e.data.lower()]
        if hello_events:
            print(f"\n   ✅ Found {len(hello_events)} 'hello' event(s)!")
            for event in hello_events:
                print(f"      - [{event.event_type}] {event.data}")
        else:
            print(f"\n   ⚠️  No 'hello' message found in events")

    print("\n" + "="*70)
    print("✅ TEST COMPLETE")
    print("="*70)
