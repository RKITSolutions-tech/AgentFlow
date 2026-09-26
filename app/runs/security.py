from __future__ import annotations

import json
import os
import re

REDACTION_MARK = "[REDACTED]"

# Deliberately conservative: each rule keeps the surrounding text (the key
# name, the header) and masks only the secret so logs stay readable.
_BUILTIN_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        REDACTION_MARK,
    ),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTION_MARK}"),
    (
        re.compile(
            r"""(?ix)
            (["']?[\w.-]*(?:api[_-]?key|secret|token|passw(?:or)?d|passwd|credentials?|authorization)[\w.-]*["']?
            \s*[:=]\s*)
            (?:"[^"\n]*"|'[^'\n]*'|[^\s,;&"']+)
            """
        ),
        rf"\1{REDACTION_MARK}",
    ),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), REDACTION_MARK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTION_MARK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTION_MARK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTION_MARK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), REDACTION_MARK),
    (re.compile(r"(?i)(://[^/\s:@]+:)[^/\s@]+(@)"), rf"\1{REDACTION_MARK}\2"),
)


def parse_extra_patterns(raw: str | None) -> tuple[str, ...]:
    """Parse AGENTFLOW_REDACT_PATTERNS: a JSON list of regular expressions."""
    if not raw or not raw.strip():
        return ()
    try:
        patterns = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("AGENTFLOW_REDACT_PATTERNS must be a JSON list of regexes") from exc
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise RuntimeError("AGENTFLOW_REDACT_PATTERNS must be a JSON list of regexes")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RuntimeError(f"Invalid AGENTFLOW_REDACT_PATTERNS entry {pattern!r}: {exc}") from exc
    return tuple(patterns)


def extra_patterns_from_env() -> tuple[str, ...]:
    return parse_extra_patterns(os.environ.get("AGENTFLOW_REDACT_PATTERNS"))


def redact(text: str, extra_patterns: tuple[str, ...] = ()) -> tuple[str, bool]:
    """Mask secrets in `text`. Returns (clean_text, whether anything was masked)."""
    if not text:
        return text, False
    cleaned = text
    for pattern, replacement in _BUILTIN_RULES:
        cleaned = pattern.sub(replacement, cleaned)
    for raw in extra_patterns:
        cleaned = re.sub(raw, REDACTION_MARK, cleaned)
    return cleaned, cleaned != text
