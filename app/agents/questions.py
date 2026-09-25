"""Turn agent output into structured clarifying questions.

Claude Code has a native structured-question tool; Codex (and other CLIs
without one) only produce text. Guessing at questions in free prose ("which
would you prefer?") would misfire on ordinary replies, so detection is
explicit: an agent that wants to ask emits a fenced block

    ```agentflow-question
    {"header": "Database", "question": "Which database?",
     "options": [{"label": "Postgres", "description": "Robust"}, "SQLite"]}
    ```

(a JSON object, or a list of them). ``QUESTION_PROTOCOL_INSTRUCTIONS`` is the
reusable prompt fragment that teaches the format; prompt composition, not the
adapter, decides when to include it (docs/AGENT_ADAPTER.md section 8).

Anything that is not a well-formed block is left in the text untouched, so
detection never swallows or alters a normal message.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agents.models import normalize_options

QUESTION_FENCE = "agentflow-question"

QUESTION_PROTOCOL_INSTRUCTIONS = f"""\
If you need the user to choose between options before you can continue, ask
with a fenced block instead of prose, then stop and wait for the answer:

```{QUESTION_FENCE}
{{"header": "short label", "question": "the question", "multi_select": false,
 "options": [{{"label": "Option A", "description": "what it means"}}, {{"label": "Option B"}}]}}
```

Do not add an "Other" option; the user can always type their own answer."""

_BLOCK_RE = re.compile(rf"```{QUESTION_FENCE}[ \t]*\n(.*?)```[ \t]*\n?", re.DOTALL)


def _parse_spec(raw: Any) -> dict[str, Any]:
    """Validate one question object; raise ValueError if it is malformed."""
    if not isinstance(raw, dict):
        raise ValueError("question must be an object")
    question = raw.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question text is required")
    options = raw.get("options")
    if not isinstance(options, list):
        raise ValueError("options must be a list")
    return {
        "question": question,
        "options": normalize_options(options),
        "header": str(raw.get("header") or ""),
        "multi_select": bool(raw.get("multi_select", False)),
    }


def extract_questions(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Split ``text`` into (remaining prose, question specs).

    Valid blocks are removed from the prose; malformed ones stay as written.
    """
    specs: list[dict[str, Any]] = []

    def replace(match: re.Match[str]) -> str:
        try:
            payload = json.loads(match.group(1))
            found = [_parse_spec(item) for item in (payload if isinstance(payload, list) else [payload])]
        except (ValueError, TypeError):
            return match.group(0)
        specs.extend(found)
        return ""

    return _BLOCK_RE.sub(replace, text).strip(), specs
