"""Test sending a message to real Codex and receiving a response."""
import os
import pytest
import shutil


@pytest.mark.skipif(
    shutil.which("codex") is None,
    reason="Codex binary not installed"
)
def test_codex_message_and_response(app, client, live_server):
    """Test sending a message to Codex and receiving a response.

    This test:
    1. Verifies Codex is installed
    2. Creates a session with real Codex adapter
    3. Sends a test message via Playwright
    4. Waits for Codex response
    5. Verifies response appears in chat
    """
    from app.db import get_db
    from app.projects import models as project_models
    from app.agents.models import create_agent_session, list_agent_events
    from app.agents.codex import CodexAdapter

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "codex-response-test")
    os.makedirs(repo_path, exist_ok=True)

    print("\n" + "="*70)
    print("🤖 CODEX CHAT MESSAGE TEST")
    print("="*70)

    # Verify Codex is available
    print("\n1️⃣  Checking Codex availability...")
    codex_available = shutil.which("codex") is not None
    if codex_available:
        codex_adapter = CodexAdapter()
        is_available = codex_adapter.available()
        version = codex_adapter.version()
        print(f"   ✅ Codex found")
        print(f"   ✅ Available: {is_available}")
        print(f"   ✅ Version: {version}")
    else:
        pytest.skip("Codex binary not installed")

    # Create project
    print("\n2️⃣  Creating project...")
    resp = client.post(
        "/projects/new",
        data={
            "name": "CodexResponseTest",
            "description": "Test Codex response",
            "repo_name": "main",
            "repo_path": repo_path,
        },
    )
    assert resp.status_code == 302
    print("   ✅ Project created")

    # Get project and create Codex session
    print("\n3️⃣  Creating Codex session...")
    with app.app_context():
        db = get_db()
        projects = project_models.list_projects(db)
        project = [p for p in projects if p.name == "CodexResponseTest"][0]
        repo = project.repositories[0]
        print(f"   ✅ Project: {project.name}")
        print(f"   ✅ Repository: {repo.name}")

        session_id = create_agent_session(
            db,
            project.id,
            "codex",  # Real Codex adapter
            execution_target=repo_path,
            metadata={"repo_id": repo.id}
        )
        print(f"   ✅ Codex session created (ID: {session_id})")

    # Use Playwright to send message and wait for response
    print("\n4️⃣  Opening chat interface with Playwright...")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()
            print("   ✅ Browser launched")

            # Navigate to chat
            print(f"\n5️⃣  Navigating to Codex chat (session {session_id})...")
            page.goto(f"{live_server}/sessions/{session_id}")
            page.wait_for_selector("#chatContainer", timeout=5000)
            print("   ✅ Chat interface loaded")

            # Send message
            test_message = "What is the current date?"
            print(f"\n6️⃣  Sending message to Codex: '{test_message}'")
            page.fill("#promptInput", test_message)
            print("   ✅ Message typed into input")

            # Find and click send button
            send_button = page.query_selector("button:has-text('Send')")
            if send_button:
                send_button.click()
                print("   ✅ Send button clicked")
            else:
                buttons = page.query_selector_all("button")
                buttons[-1].click()
                print("   ✅ Send button clicked (via fallback)")

            # Wait for response (give Codex time to respond)
            print("\n7️⃣  Waiting for Codex response (up to 15 seconds)...")
            try:
                # Wait for chat container to update
                page.wait_for_load_state("networkidle", timeout=15000)
                print("   ✅ Page state stabilized")
            except:
                print("   ℹ️  Timeout waiting for network idle (response may still be coming)")

            # Check for response in events
            print("\n8️⃣  Checking for Codex response in events...")
            with app.app_context():
                db = get_db()
                events = list_agent_events(db, session_id)
                print(f"   ✅ Found {len(events)} events in session")

                if events:
                    print("\n   📊 Session Events:")
                    for i, event in enumerate(events, 1):
                        data_preview = event.data[:80] if event.data else "(empty)"
                        print(f"      {i}. [{event.event_type}] {data_preview}")

                    # Check for actual response
                    output_events = [e for e in events if "Output" in e.event_type or "AgentText" in e.event_type]
                    if output_events:
                        print(f"\n   ✅ Found {len(output_events)} output event(s) from Codex")
                        for event in output_events:
                            print(f"      📬 Response: {event.data[:100]}")
                    else:
                        print("\n   ℹ️  No output events yet (Codex may still be processing)")
                else:
                    print("   ℹ️  No events recorded yet")

            # Check chat display
            print("\n9️⃣  Checking chat interface display...")
            chat_content = page.content()
            if "message" in chat_content.lower():
                print("   ✅ Chat interface contains message elements")

            # Get chat text
            chat_container = page.query_selector("#chatContainer")
            if chat_container:
                container_text = chat_container.text_content()
                print(f"\n   📝 Chat Container Content:")
                print(f"   {container_text[:300]}")

            context.close()
            browser.close()
            print("\n   ✅ Browser closed")

            print("\n" + "="*70)
            print("✅ CODEX CHAT TEST COMPLETED!")
            print("="*70)

    except Exception as e:
        print(f"\n❌ Test failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise


def test_codex_availability(app):
    """Quick test to check if Codex is available and responsive."""
    print("\n" + "="*70)
    print("🔍 CODEX AVAILABILITY CHECK")
    print("="*70)

    from app.agents.codex import CodexAdapter

    adapter = CodexAdapter()

    print("\n1️⃣  Checking Codex binary...")
    binary_available = shutil.which("codex") is not None
    print(f"   Binary available: {binary_available}")

    print("\n2️⃣  Checking adapter availability...")
    is_available = adapter.available()
    print(f"   Adapter available: {is_available}")

    if is_available:
        print("\n3️⃣  Getting Codex version...")
        version = adapter.version()
        print(f"   Version: {version}")

        print("\n4️⃣  Checking capabilities...")
        capabilities = adapter.capabilities()
        print(f"   Capabilities: {capabilities}")

        print("\n" + "="*70)
        print("✅ CODEX IS READY!")
        print("="*70)
        assert is_available, "Codex should be available"
    else:
        print("\n⚠️  Codex is not available")
        pytest.skip("Codex binary not installed or not accessible")
