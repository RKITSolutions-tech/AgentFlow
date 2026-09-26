from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field


@dataclass
class Config:
    """Application configuration.

    Binds to loopback by default per the AgentFlow security model
    (docs/HIGH_LEVEL_DESIGN.md §20): the app must not be exposed on all
    interfaces without an explicit opt-in.
    """

    DATABASE_PATH: str = "instance/agentflow.sqlite3"
    SECRET_KEY: str = field(default_factory=lambda: secrets.token_hex(32))
    HOST: str = "127.0.0.1"
    PORT: int = 5000
    ALLOWED_PROJECT_ROOTS: tuple[str, ...] = field(default_factory=tuple)
    TESTING: bool = False
    # Extra regexes masked in persisted Run logs/prompts, on top of the
    # built-in secret rules (AGENTFLOW_REDACT_PATTERNS: a JSON list).
    REDACT_PATTERNS: tuple[str, ...] = field(default_factory=tuple)
    # Where Run artifact content and large logs live; defaults to
    # `artifacts/` beside the database.
    ARTIFACT_DIR: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        roots_env = os.environ.get("AGENTFLOW_ALLOWED_ROOTS", "")
        roots = tuple(
            os.path.abspath(os.path.expanduser(r.strip()))
            for r in roots_env.split(os.pathsep)
            if r.strip()
        )
        if not roots:
            roots = (os.path.abspath(os.path.expanduser("~")),)

        host = os.environ.get("AGENTFLOW_HOST", "127.0.0.1")
        if host not in ("127.0.0.1", "localhost", "::1"):
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
        )
