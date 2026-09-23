# Testing Guide

This project uses multiple testing frameworks to ensure comprehensive coverage:

## Current Testing Setup

### 1. **pytest** - Core Testing Framework
- **Location:** Tests in `tests/` directory
- **Usage:** `./venv/bin/python -m pytest tests/`
- **Coverage:** Unit tests, integration tests, API tests

### 2. **BeautifulSoup** - HTML Parsing & Structure Testing ✅
- **Status:** Installed and active
- **File:** `tests/test_ui_sessions.py`
- **What it tests:**
  - HTML page structure validation
  - Form field presence and attributes
  - Content assertions
  - Layout validation

**Example:**
```python
from bs4 import BeautifulSoup

resp = client.get("/sessions")
soup = BeautifulSoup(resp.data, "html.parser")

# Validate structure
table = soup.find("table")
assert table is not None

# Find specific elements
heading = soup.find("h1")
assert "Sessions" in heading.text
```

**Run BeautifulSoup tests:**
```bash
./venv/bin/python -m pytest tests/test_ui_sessions.py -v
```

### 3. **Playwright** - End-to-End Browser Testing
- **Status:** Package installed, browsers need setup
- **File:** `tests/test_e2e_sessions.py`
- **What it tests:**
  - Real browser interactions
  - Form submissions
  - Navigation flows
  - Visual regression (screenshots)
  - Responsive design

**Setup Playwright Browsers:**
```bash
# Install chromium browser (requires sudo/system access)
./venv/bin/python -m playwright install chromium

# For headless testing:
export PLAYWRIGHT_HEADLESS=true
./venv/bin/python -m pytest tests/test_e2e_sessions.py -v
```

**Example E2E Test:**
```python
async def test_chat_interface(browser):
    context = await browser.new_context()
    page = await context.new_page()
    
    await page.goto("http://127.0.0.1:5000/sessions/1")
    await page.wait_for_selector("#chatContainer")
    
    # Interact with form
    await page.locator("#promptInput").fill("Test message")
    await page.locator("button:has-text('Send')").click()
    
    # Verify response
    await page.wait_for_selector(".message")
    await context.close()
```

**Run E2E tests (once browsers installed):**
```bash
./venv/bin/python -m pytest tests/test_e2e_sessions.py -v
```

## Test Statistics

```
Total Tests: 78 passing + 6 skipped
├── test_agents.py: 18 tests
├── test_codex_adapter.py: 17 tests
├── test_execution.py: 6 tests
├── test_projects.py: 4 tests
├── test_sessions.py: 16 tests
├── test_ui_sessions.py: 6 tests (BeautifulSoup) ✅
├── test_workspace.py: 10 tests
└── test_workspace_search.py: 10 tests
```

## Running Tests

### All tests:
```bash
./venv/bin/python -m pytest tests/ -v
```

### Specific test file:
```bash
./venv/bin/python -m pytest tests/test_ui_sessions.py -v
```

### Specific test:
```bash
./venv/bin/python -m pytest tests/test_sessions.py::test_codex_session_with_chat_interaction -v
```

### With output:
```bash
./venv/bin/python -m pytest tests/ -v -s
```

### Coverage report (if coverage installed):
```bash
./venv/bin/pip install coverage
./venv/bin/python -m pytest tests/ --cov=app --cov-report=html
```

## Test Types

### 1. **Unit Tests**
- Test individual functions
- Examples: `test_agents.py`, `test_execution.py`

### 2. **Integration Tests**
- Test components working together
- Examples: `test_sessions.py`, `test_projects.py`

### 3. **HTML/UI Tests** (BeautifulSoup)
- Test page structure and forms
- Examples: `test_ui_sessions.py`
- Better than string matching (b"Chat" in resp.data)
- Validates actual HTML structure

### 4. **E2E Tests** (Playwright)
- Test complete user workflows
- Examples: `test_e2e_sessions.py`
- Requires running Flask server
- Captures screenshots for regression testing

## Debugging Failed Tests

### View detailed output:
```bash
./venv/bin/python -m pytest tests/test_sessions.py -v -s
```

### Run specific test with print statements:
```bash
./venv/bin/python -m pytest tests/test_sessions.py::test_codex_session_with_chat_interaction -v -s --tb=short
```

### Run with pdb (debugger):
```bash
./venv/bin/python -m pytest tests/test_sessions.py -v -s --pdb
```

## Adding New Tests

### BeautifulSoup test template:
```python
def test_my_page_structure(client):
    """Test page has correct structure."""
    resp = client.get("/my-page")
    soup = BeautifulSoup(resp.data, "html.parser")
    
    # Verify elements exist
    heading = soup.find("h1")
    assert heading is not None
    
    # Verify content
    assert "Expected text" in heading.text
    
    # Verify form fields
    form = soup.find("form")
    inputs = form.find_all("input")
    assert len(inputs) == 3
```

### E2E test template:
```python
async def test_user_flow(browser):
    """Test complete user workflow."""
    context = await browser.new_context()
    page = await context.new_page()
    
    # Navigate
    await page.goto("http://127.0.0.1:5000/")
    
    # Interact
    await page.fill("input[name=username]", "testuser")
    await page.click("button:has-text('Login')")
    
    # Verify
    await page.wait_for_url("**/dashboard")
    assert "Welcome" in await page.text_content("h1")
    
    await context.close()
```

## Best Practices

1. **Use BeautifulSoup for HTML validation** - More reliable than string matching
2. **Use E2E tests for critical user flows** - Catch integration issues
3. **Keep tests focused** - One assertion per test is ideal
4. **Use descriptive names** - Test name should describe what's tested
5. **Clean up resources** - Close browser contexts and pages in E2E tests
6. **Mock external services** - Don't rely on real Codex during tests

## Troubleshooting

### "playwright: command not found"
```bash
./venv/bin/python -m playwright install chromium
```

### "Timed out waiting for page to load"
- Increase timeout: `await page.goto(url, timeout=30000)`
- Check if Flask server is running: `curl http://127.0.0.1:5000`

### BeautifulSoup not finding elements
- Check HTML structure: `print(soup.prettify())`
- Use correct selectors: `soup.find("div", {"class": "container"})`

### E2E test hangs
- Use `--timeout` flag
- Check browser isn't blocked
- Try headless mode: `PLAYWRIGHT_HEADLESS=true`

## Continuous Integration

To run tests in CI/CD:

```yaml
# Example GitHub Actions
- name: Run tests
  run: |
    ./venv/bin/pip install -r requirements.txt
    ./venv/bin/python -m pytest tests/ -v
```

## Resources

- [pytest documentation](https://docs.pytest.org/)
- [BeautifulSoup docs](https://www.crummy.com/software/BeautifulSoup/)
- [Playwright docs](https://playwright.dev/python/)
