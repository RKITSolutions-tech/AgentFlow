#!/usr/bin/env python3
"""Replicates a Codex session start/failure via HTTP, like the browser UI does.

Drives the same endpoints the "Start New Session" form on
/sessions/project/<id> posts to (app/sessions/routes.py), then polls
/sessions/<id>/stream until the Codex turn finishes. Useful for reproducing
a FAILED session without clicking through the UI.

Usage: python scripts/replicate_session_failure.py [project_id]
"""
from __future__ import annotations

import re
import sys
import time

import requests
from bs4 import BeautifulSoup

BASE_URL = "http://127.0.0.1:5000"


def main() -> int:
    project_id = int(sys.argv[1]) if len(sys.argv) > 1 else 5  # SAM6

    session = requests.Session()

    project_url = f"{BASE_URL}/sessions/project/{project_id}"
    resp = session.get(project_url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    project_name = soup.h1.text.strip() if soup.h1 else "?"
    print(f"Project page: {project_name} ({project_url})")

    form = soup.find("form", action=re.compile(r"/create$"))
    if form is None:
        print("No 'Start New Session' form found (project may have no repositories).")
        return 1

    create_url = BASE_URL + form["action"]
    repo_select = form.find("select", {"name": "repo_id"})
    repo_option = repo_select.find("option", selected=True) if repo_select else None
    if repo_option is None and repo_select is not None:
        repo_option = repo_select.find("option")

    data = {
        "agent_type": "codex",
        "repo_id": repo_option["value"] if repo_option else "",
    }
    print(f"POST {create_url} data={data}")

    resp = session.post(create_url, data=data, allow_redirects=True)
    resp.raise_for_status()

    match = re.search(r"/sessions/(\d+)$", resp.url)
    if not match:
        print(f"Unexpected redirect target: {resp.url}")
        return 1
    session_id = int(match.group(1))

    soup = BeautifulSoup(resp.text, "html.parser")
    flash = soup.find("li") or soup.find(class_=re.compile("flash"))
    status_line = soup.find("p", string=re.compile("Status:"))
    print(f"Flash message: {flash.text.strip() if flash else '(none found)'}")
    print(f"Session {session_id} initial status line: "
          f"{status_line.text.strip() if status_line else '(not found)'}")

    stream_url = f"{BASE_URL}/sessions/{session_id}/stream"
    seen_ids: set[int] = set()
    terminal = {"COMPLETED", "FAILED", "STOPPED"}
    final_status = None

    for _ in range(30):
        resp = session.get(stream_url, params={"after_id": 0})
        resp.raise_for_status()
        for line in resp.text.splitlines():
            if not line.startswith("data: "):
                continue
            import json

            event = json.loads(line[len("data: "):])
            if event["id"] in seen_ids:
                continue
            seen_ids.add(event["id"])
            print(f"  event[{event['id']}] {event['event_type']}: {event['data']}")
            if event["event_type"] in ("AgentComplete", "AgentError"):
                final_status = "COMPLETED" if event["event_type"] == "AgentComplete" else "FAILED"

        if final_status in terminal:
            break
        time.sleep(1)

    print(f"\nFinal status: {final_status or 'still running / unknown'}")
    print(f"View in browser: {BASE_URL}/sessions/{session_id}")
    return 0 if final_status == "FAILED" else (0 if final_status else 2)


if __name__ == "__main__":
    raise SystemExit(main())
