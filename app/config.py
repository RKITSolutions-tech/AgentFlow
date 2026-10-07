from __future__ import annotations

import ipaddress
import logging
import os
import secrets
import socket
from dataclasses import dataclass, field


def _resolves_to_loopback_only(host: str) -> bool:
    """True if every address `host` resolves to is in the loopback range
    (127.0.0.0/8 or ::1) -- not just the literal strings "127.0.0.1"/
    "localhost"/"::1". This is what actually matters for the safety check
    below: a machine's own hostname (e.g. Debian's /etc/hosts convention of
    mapping it to 127.0.1.1) is exactly as unreachable from the network as
    "localhost" is, so it should not need AGENTFLOW_ALLOW_UNSAFE_BIND=1
    either. An unresolvable host is treated as *not* loopback -- refuse by
    default rather than silently accept something that won't actually bind.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_loopback for info in infos)


def allowed_roots_from_env() -> tuple[str, ...]:
    """AGENTFLOW_ALLOWED_ROOTS (os.pathsep-separated), defaulting to the home
    directory. Shared with the MCP server subprocess (app/mcp/server.py),
    which has no Flask app/Config of its own."""
    roots = tuple(
        os.path.abspath(os.path.expanduser(r.strip()))
        for r in os.environ.get("AGENTFLOW_ALLOWED_ROOTS", "").split(os.pathsep)
        if r.strip()
    )
    return roots or (os.path.abspath(os.path.expanduser("~")),)


@dataclass
class Config:
    """Application configuration.

    Binds to loopback by default per the AgentFlow security model
    (docs/HIGH_LEVEL_DESIGN.md §20): the app must not be exposed on all
    interfaces without an explicit opt-in. The default host is this
    machine's own hostname (`socket.gethostname()`, e.g. "devserver" here)
    rather than the literal "127.0.0.1", so the app is reachable at a
    stable, memorable URL (http://<hostname>:5000/) without extra
    configuration; it still has to resolve to a loopback address
    (`_resolves_to_loopback_only`) to be accepted without
    AGENTFLOW_ALLOW_UNSAFE_BIND=1, same guarantee as before -- this is
    portable across machines rather than a literal "devserver" baked into
    the code, since only this box's own hostname happens to be that.
    """

    DATABASE_PATH: str = "instance/agentflow.sqlite3"
    SECRET_KEY: str = field(default_factory=lambda: secrets.token_hex(32))
    HOST: str = field(default_factory=socket.gethostname)
    PORT: int = 5000
    ALLOWED_PROJECT_ROOTS: tuple[str, ...] = field(default_factory=tuple)
    TESTING: bool = False
    # Extra regexes masked in persisted Run logs/prompts, on top of the
    # built-in secret rules (AGENTFLOW_REDACT_PATTERNS: a JSON list).
    REDACT_PATTERNS: tuple[str, ...] = field(default_factory=tuple)
    # Where Run artifact content and large logs live; defaults to
    # `artifacts/` beside the database.
    ARTIFACT_DIR: str = ""
    # Agent used for sprint planning: "codex"/"claude" (real), "local" (a
    # Settings model_catalog "local" entry, driven through whichever of those
    # two CLIs PLANNING_LOCAL_ADAPTER names), or "fake" (deterministic, one
    # task per backlog item; AGENTFLOW_PLANNING_AGENT).
    PLANNING_AGENT: str = "codex"
    # model_id of the Settings "local" catalog entry PLANNING_AGENT=local uses
    # (AGENTFLOW_PLANNING_MODEL). A `local` catalog row only carries a
    # base_url/api_key (app/settings/models.py), not which CLI wire protocol
    # it speaks, so PLANNING_LOCAL_ADAPTER says that separately.
    PLANNING_MODEL: str = ""
    # Which real adapter's wire protocol a PLANNING_AGENT=local model speaks:
    # "codex" (default, OpenAI-compatible `-c model_providers.local...`) or
    # "claude" (Anthropic-API-compatible, ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN).
    # AGENTFLOW_PLANNING_LOCAL_ADAPTER.
    PLANNING_LOCAL_ADAPTER: str = "codex"
    # Bearer token required on this instance's federation API
    # (app/federation/routes.py), so a second AgentFlow instance can list/drive
    # sessions here. Unset (default) = federation routes refuse every request;
    # this is the only part of AgentFlow that gets an auth check, since making
    # an instance reachable at all already requires AGENTFLOW_ALLOW_UNSAFE_BIND=1
    # and a private network (docs/CLOUDCLI_GAP_ANALYSIS.md G11).
    FEDERATION_TOKEN: str = ""
    # Default state of the "Include recent project context" checkbox on the
    # session start form (docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md §5.5,
    # AGENTFLOW_SESSION_WARM_START). The checkbox submission still decides the
    # actual session, same as `mcp_tools` -- this only sets the pre-checked state.
    SESSION_WARM_START_DEFAULT: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        roots = allowed_roots_from_env()

        host = os.environ.get("AGENTFLOW_HOST", socket.gethostname())
        if not _resolves_to_loopback_only(host):
            if os.environ.get("AGENTFLOW_ALLOW_UNSAFE_BIND") != "1":
                raise RuntimeError(
                    "Refusing to bind to a non-loopback address "
                    f"({host!r}) without AGENTFLOW_ALLOW_UNSAFE_BIND=1. "
                    "See docs/HIGH_LEVEL_DESIGN.md section 20."
                )
            logging.getLogger(__name__).warning(
                "AGENTFLOW_ALLOW_UNSAFE_BIND=1: binding to %s exposes AgentFlow, "
                "which can run commands on this host, beyond loopback and has no "
                "authentication.",
                host,
            )

        from app.runs.security import extra_patterns_from_env

        return cls(
            DATABASE_PATH=os.environ.get(
                "AGENTFLOW_DATABASE_PATH", "instance/agentflow.sqlite3"
            ),
            SECRET_KEY=os.environ.get("AGENTFLOW_SECRET_KEY", secrets.token_hex(32)),
            HOST=host,
            PORT=int(os.environ.get("AGENTFLOW_PORT", "5000")),
            ALLOWED_PROJECT_ROOTS=roots,
            REDACT_PATTERNS=extra_patterns_from_env(),
            ARTIFACT_DIR=os.environ.get("AGENTFLOW_ARTIFACT_DIR", ""),
            PLANNING_AGENT=os.environ.get("AGENTFLOW_PLANNING_AGENT", "codex").lower(),
            PLANNING_MODEL=os.environ.get("AGENTFLOW_PLANNING_MODEL", ""),
            PLANNING_LOCAL_ADAPTER=os.environ.get(
                "AGENTFLOW_PLANNING_LOCAL_ADAPTER", "codex"
            ).lower(),
            FEDERATION_TOKEN=os.environ.get("AGENTFLOW_FEDERATION_TOKEN", ""),
            SESSION_WARM_START_DEFAULT=os.environ.get("AGENTFLOW_SESSION_WARM_START") == "1",
        )
