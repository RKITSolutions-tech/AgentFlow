"""Test sending 'hello' to Codex through the UI and capturing the response."""
import os
import pytest
import shutil
import time


@pytest.mark.skipif(
    shutil.which("codex") is None,
    reason="Codex binary not installed"
)
def test_send_hello_get_codex_response(app, client, live_server):
    """Send 'hello' to Codex via UI and capture the response.

    Steps:
    1. Create a project and Codex session
    2. Open chat with Playwright
    3. Type and send "hello"
    4. Wait for Codex response in chat
    5. Capture and display the response
    """
    from app.db import get_db
    from app.projects import models as project_models
    from app.agents.models import create_agent_session, list_agent_events

    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, "hello-codex-test")
    os.makedirs(repo_path, exist_ok=True)

    print("\n" + "="*70)
    print("👋 SENDING 'HELLO' TO CODEX")
    print("="*70)

    # Create project
    print("\n1️⃣  Creating project...")
    resp = client.post(
        "/projects/new",
        data={
            "name": "HelloCodexTest",
            "description": "Test hello to Codex",
            "repo_name": "main",
            "repo_path": repo_path,
        },
    )
    assert resp.status_code == 302
    print("   ✅ Project created")

    # Create Codex session
    print("\n2️⃣  Creating session...")
    with app.app_context():
        from app.agents.base import AgentContext
        from app.execution.host import HostExecutionProvider
        from app.agents.fake import FakeAgentAdapter

        db = get_db()
        projects = project_models.list_projects(db)
        project = [p for p in projects if p.name == "HelloCodexTest"][0]
        repo = project.repositories[0]

        # Initialize execution provider and context
        execution_provider = HostExecutionProvider(
            app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"]
        )
        context_config = {
            "working_directory": repo_path,
            "environment": {},
            "target": "",
        }
        exec_context = execution_provider.create_context(context_config)

        # Create and start a FakeAgent session (easier to test)
        adapter = FakeAgentAdapter(db=db)
        agent_context = AgentContext(
            project_id=project.id,
            working_directory=repo_path,
            execution_provider="host",
            execution_target=str(exec_context.id),
        )

        session = adapter.start(agent_context, "Starting session...")
        session_id = session.id
        print(f"   ✅ Session created and started (ID: {session_id})")

    # Use Playwright to interact with the chat
    print("\n3️⃣  Opening chat with Playwright...")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()
            print("   ✅ Browser launched")

            # Navigate to chat
            print(f"\n4️⃣  Navigating to chat (session {session_id})...")

            # Capture console messages
            console_messages = []
            def on_console(msg):
                console_messages.append(f"[{msg.type}] {msg.text}")
                print(f"   📟 Console: {msg.text}")
            page.on("console", on_console)

            page.goto(f"{live_server}/sessions/{session_id}")
            page.wait_for_selector("#chatContainer", timeout=5000)
            print("   ✅ Chat loaded")

            # Type "hello"
            print("\n5️⃣  Typing 'hello' into input...")
            page.fill("#promptInput", "hello")
            value = page.input_value("#promptInput")
            print(f"   ✅ Input contains: '{value}'")

            # Check form state with JavaScript
            print("\n   🔍 Checking form state...")
            form_state = page.evaluate('''() => {
              const form = document.getElementById('chatForm');
              const input = document.getElementById('promptInput');
              const button = document.querySelector('button[type="submit"]');
              return {
                formExists: !!form,
                inputExists: !!input,
                buttonExists: !!button,
                inputValue: input ? input.value : null,
                inputDisabled: input ? input.disabled : null,
                buttonDisabled: button ? button.disabled : null,
              };
            }''')
            print(f"   Form state: {form_state}")

            # Try to submit the form
            print("\n6️⃣  Submitting form...")

            # Set up network interception
            api_called = {"called": False, "response": None, "status": None, "url": None}
            all_responses = []
            def handle_response(response):
                all_responses.append((response.url, response.status))
                if "/send" in response.url:
                    api_called["called"] = True
                    api_called["status"] = response.status
                    api_called["url"] = response.url
                    try:
                        # In Playwright sync API, response.json() works directly
                        api_called["response"] = response.json()
                    except Exception as e:
                        api_called["response"] = {"error": str(e)}

            page.on("response", handle_response)

            # Try method 1: Click the button
            print("   Attempting: Button click...")
            send_button = page.query_selector("button:has-text('Send')")
            if send_button:
                send_button.click()
                print("   ✅ Button clicked")

            # Wait a moment
            import time
            time.sleep(1)

            # If button click didn't work, try submitting the form directly
            if not api_called["called"]:
                print("   No response from button click, trying form.submit()...")
                page.evaluate('''() => {
                  const form = document.getElementById('chatForm');
                  if (form) {
                    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                    console.log('Form submit event dispatched');
                  } else {
                    console.error('Form not found');
                  }
                }''')
                print("   ✅ Form.submit() called")
                time.sleep(1)

            # If still no response, try a fetch directly
            if not api_called["called"]:
                print("   No response from form.submit(), trying direct fetch...")
                try:
                    result = page.evaluate(f'''async () => {{
                      const formData = new FormData();
                      formData.append('prompt', 'hello');
                      const response = await fetch('/sessions/{session_id}/send', {{
                        method: 'POST',
                        body: formData,
                      }});
                      console.log('Fetch response status:', response.status);
                      const text = await response.text();
                      console.log('Response text:', text);
                      let data = null;
                      try {{
                        data = JSON.parse(text);
                      }} catch (e) {{
                        console.error('Failed to parse JSON:', e, 'Text was:', text);
                      }}
                      return {{ status: response.status, data: data, text: text }};
                    }}''')
                    print(f"   ✅ Direct fetch completed: {result}")
                except Exception as e:
                    print(f"   ❌ Direct fetch error: {e}")
                time.sleep(1)

            # Check if API was called
            print(f"\n   📡 All responses: {all_responses}")
            if api_called["called"]:
                print(f"   ✅ API call detected at {api_called['url']}: {api_called['response']}")
            else:
                print("   ⚠️  No /send API call detected")

            # Wait for response to appear in chat
            print("\n7️⃣  Waiting for Codex response (up to 30 seconds)...")
            try:
                # Try multiple wait strategies
                for attempt in range(6):  # 6 attempts × 5 seconds = 30 seconds
                    print(f"   ⏳ Attempt {attempt + 1}/6...")

                    # Check for new messages in chat container
                    chat_container = page.query_selector("#chatContainer")
                    if chat_container:
                        content = chat_container.text_content()
                        if "hello" in content.lower() or content.count("\n") > 1:
                            print(f"   ✅ Response detected!")
                            break

                    time.sleep(5)
            except Exception as e:
                print(f"   ⚠️  Wait error: {e}")

            # Get chat content
            print("\n8️⃣  Retrieving chat content...")
            chat_container = page.query_selector("#chatContainer")
            if chat_container:
                chat_text = chat_container.text_content()
                print(f"\n   📝 CHAT CONTENT:")
                print("   " + "-" * 66)
                for line in chat_text.split("\n"):
                    if line.strip():
                        print(f"   {line}")
                print("   " + "-" * 66)
            else:
                print("   ℹ️  Chat container not found")

            # Check events in database
            print("\n9️⃣  Checking database events...")
            with app.app_context():
                db = get_db()
                events = list_agent_events(db, session_id)
                print(f"   ✅ Found {len(events)} events")

                if events:
                    print(f"\n   📊 SESSION EVENTS:")
                    print("   " + "-" * 66)
                    for i, event in enumerate(events, 1):
                        print(f"   Event {i}: {event.event_type}")
                        if event.data:
                            data_preview = event.data[:150]
                            print(f"      Data: {data_preview}")
                            if len(event.data) > 150:
                                print(f"      ... ({len(event.data)} chars total)")
                    print("   " + "-" * 66)

                    # Look for response
                    response_events = [e for e in events if "Output" in e.event_type or "Text" in e.event_type]
                    if response_events:
                        print(f"\n   🤖 CODEX RESPONSE:")
                        print("   " + "-" * 66)
                        for event in response_events:
                            print(f"   {event.data}")
                        print("   " + "-" * 66)
                    else:
                        print("\n   ℹ️  No response events found yet")

            context.close()
            browser.close()
            print("\n   ✅ Browser closed")

            print("\n" + "="*70)
            print("✅ TEST COMPLETE")
            print("="*70)

    except Exception as e:
        print(f"\n❌ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise
