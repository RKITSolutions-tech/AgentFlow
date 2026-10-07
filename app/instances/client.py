"""Thin HTTP client for a remote AgentFlow instance's federation API
(app/federation/routes.py). Uses stdlib `urllib.request` -- the only outbound
HTTP client already used anywhere in AgentFlow (app/pipelines/engine.py's
`_h_http`) -- rather than adding a `requests`/`httpx` dependency for this.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from app.instances.models import RemoteInstance

DEFAULT_TIMEOUT = 5.0


class RemoteInstanceError(RuntimeError):
    """A call to a remote instance's federation API failed -- network error,
    non-2xx status, or a response that wasn't the JSON we expected. Callers
    should treat this as "that instance is unreachable/misconfigured right
    now" and degrade (e.g. an offline badge) rather than propagate a 500."""


def _call(instance: RemoteInstance, method: str, path: str, payload: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    url = f"{instance.base_url}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {instance.token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        try:
            detail = json.loads(body).get("error", body)
        except (json.JSONDecodeError, AttributeError):
            detail = body or str(exc)
        raise RemoteInstanceError(f"{instance.name}: HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise RemoteInstanceError(f"{instance.name}: unreachable ({exc})") from exc

    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise RemoteInstanceError(f"{instance.name}: invalid response") from exc


def ping(instance: RemoteInstance) -> dict:
    return _call(instance, "GET", "/federation/api/ping")


def list_projects(instance: RemoteInstance) -> list[dict]:
    return _call(instance, "GET", "/federation/api/projects")["projects"]


def list_sessions(instance: RemoteInstance, project_id: int) -> dict:
    return _call(instance, "GET", f"/federation/api/projects/{project_id}/sessions")


def create_session(instance: RemoteInstance, project_id: int, **fields) -> int:
    return _call(instance, "POST", f"/federation/api/projects/{project_id}/sessions", fields)["id"]


def get_session(instance: RemoteInstance, session_id: int) -> dict:
    return _call(instance, "GET", f"/federation/api/sessions/{session_id}")


def send_prompt(instance: RemoteInstance, session_id: int, prompt: str) -> None:
    _call(instance, "POST", f"/federation/api/sessions/{session_id}/send", {"prompt": prompt})


def stream(instance: RemoteInstance, session_id: int, after_id: int = 0) -> list[dict]:
    return _call(
        instance, "GET", f"/federation/api/sessions/{session_id}/stream?after_id={after_id}"
    )["events"]


def stop(instance: RemoteInstance, session_id: int) -> None:
    _call(instance, "POST", f"/federation/api/sessions/{session_id}/stop")


def wiki_setup(instance: RemoteInstance, path: str, create: bool = False, name: str = "Wiki") -> str:
    """Validate (optionally create) a wiki folder on the remote instance;
    returns the absolute path it resolved to there."""
    return _call(instance, "POST", "/federation/api/wiki/setup", {"path": path, "create": create, "name": name})["path"]


def wiki_pages(instance: RemoteInstance, path: str) -> list[str]:
    return _call(instance, "GET", f"/federation/api/wiki/pages?{urllib.parse.urlencode({'path': path})}")["pages"]


def wiki_page(instance: RemoteInstance, path: str, page: str) -> str:
    query = urllib.parse.urlencode({"path": path, "page": page})
    return _call(instance, "GET", f"/federation/api/wiki/page?{query}")["content"]


def wiki_search(instance: RemoteInstance, path: str, query: str, limit: int = 20) -> list[dict]:
    params = urllib.parse.urlencode({"path": path, "q": query, "limit": limit})
    return _call(instance, "GET", f"/federation/api/wiki/search?{params}")["hits"]


def wiki_write(instance: RemoteInstance, path: str, page: str, content: str) -> str:
    return _call(
        instance, "POST", "/federation/api/wiki/page", {"path": path, "page": page, "content": content},
    )["page"]
