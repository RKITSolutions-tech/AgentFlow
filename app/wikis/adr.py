"""Architecture Decision Records and decision logs
(docs/WIKI_INTEGRATION_AND_PRESENTATION.md §4.5, Phase 4 rendering).

A page is a decision when it lives under `ADRs/`/`adr/`/`DECISIONS/`... or its
frontmatter says `type: adr|decision` (scanner.page_type). Its fields come from
frontmatter first, then from a "**Status:** Accepted"-style metadata line near
the top, then from `## Problem`/`## Decision`/... sections. The browser renders
it as a structured card; a folder of them renders as a timeline, newest first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from app.wikis import renderer, scanner
from app.wikis.models import WikiPageMeta

STATUSES = ("proposed", "accepted", "rejected", "deprecated", "superseded")
# Section heading (lower-cased, first word(s)) -> field.
_SECTIONS = {
    "problem": "problem", "context": "problem", "context and problem statement": "problem",
    "problem statement": "problem", "decision": "decision", "decision outcome": "decision",
    "reasoning": "reasoning", "rationale": "reasoning", "justification": "reasoning",
    "consequences": "consequences", "implications": "consequences",
    "alternatives": "alternatives", "alternatives considered": "alternatives",
    "considered options": "alternatives", "options considered": "alternatives",
    "status": "status_section",
}
FIELD_ORDER = (
    ("problem", "Problem"), ("decision", "Decision"), ("reasoning", "Reasoning"),
    ("consequences", "Consequences"), ("alternatives", "Alternatives"),
)
_META_LINE = re.compile(r"^\s*[-*]?\s*\**(status|date|author|authors|deciders|stakeholders)\**\s*:\s*\**\s*(.+?)\s*$", re.I | re.M)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SECTION_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$", re.M)


@dataclass
class DecisionRecord:
    title: str
    status: str = ""
    date: str = ""
    author: str = ""
    stakeholders: list[str] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)  # field -> markdown
    other: str = ""  # anything not in a recognised section

    @property
    def status_key(self) -> str:
        word = (self.status.split() or [""])[0].lower().strip("*_.,()")
        return word if word in STATUSES else "unknown"


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def parse(meta: WikiPageMeta, text: str) -> DecisionRecord:
    frontmatter, body = scanner.parse_frontmatter(text)
    record = DecisionRecord(title=meta.title)
    head = body[:2000]
    found = {m.group(1).lower(): m.group(2).strip().strip("*_ ") for m in _META_LINE.finditer(head)}

    record.status = str(frontmatter.get("status") or found.get("status", "")).strip()
    record.date = str(frontmatter.get("date") or found.get("date", "")).strip()
    record.author = str(
        frontmatter.get("author") or frontmatter.get("authors") or found.get("author") or found.get("authors", "")
    ).strip()
    record.stakeholders = _as_list(
        frontmatter.get("stakeholders") or frontmatter.get("deciders")
        or found.get("stakeholders") or found.get("deciders", "")
    )

    # Split the body into `##`/`###` sections; recognised ones become fields.
    matches = list(_SECTION_HEADING.finditer(body))
    other = [body[: matches[0].start()] if matches else body]
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = body[match.end():end].strip()
        name = re.sub(r"[*_`:]", "", match.group(2)).strip().lower()
        key = _SECTIONS.get(name) or next((v for k, v in _SECTIONS.items() if name.startswith(k)), None)
        if key == "status_section":
            if not record.status and content:
                record.status = content.splitlines()[0].strip("*_ ")
        elif key and key not in record.sections:
            record.sections[key] = content
        else:
            other.append(body[match.start():end])
    for key in ("problem", "decision", "reasoning", "consequences", "alternatives"):
        if not record.sections.get(key) and frontmatter.get(key):
            record.sections[key] = str(frontmatter[key])
    # Drop the H1 and the metadata lines from the leftover intro.
    intro = re.sub(r"^#\s+.+$", "", other[0], count=1, flags=re.M)
    intro = _META_LINE.sub("", intro)
    record.other = "\n\n".join(p.strip() for p in [intro, *other[1:]] if p.strip())
    if not record.date:
        date = _DATE.search(meta.name) or _DATE.search(head)
        record.date = date.group(0) if date else meta.modified_at.strftime("%Y-%m-%d")
    return record


def render_sections(record: DecisionRecord, page_path: str, link_for: Callable[[str], str | None]) -> list[tuple[str, str]]:
    """[(label, rendered HTML)] for the card, in §4.5 order, plus any other content."""
    rendered = [
        (label, renderer.render_markdown(record.sections[key], page_path, link_for))
        for key, label in FIELD_ORDER if record.sections.get(key)
    ]
    if record.other:
        rendered.append(("Notes", renderer.render_markdown(record.other, page_path, link_for)))
    return rendered


def timeline(records: list[tuple[WikiPageMeta, DecisionRecord]]) -> list[tuple[WikiPageMeta, DecisionRecord]]:
    """Newest first by decision date, then by file name (ADR numbers)."""
    return sorted(records, key=lambda pair: (pair[1].date, pair[0].name), reverse=True)
