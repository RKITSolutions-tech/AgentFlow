"""The RESEARCH role's structured reply (docs/AGENT_ADAPTER.md §23 "Structured
report"): summary, findings, and a source list, JSON-serializable both ways so
an agent's JSON reply parses straight into it and it serializes straight back
out for storage (`app/agents/models.ResearchSession`) and later Artifact
Library linking (task 43/44 build on this shape; it is not built here).

A `Finding` with no source is `unverified` -- this is derived from
`source_ids`, not a flag the agent sets itself, so an over-confident reply
cannot mark its own claim "verified" without actually citing something.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

CONFIDENCE_VERIFIED = "verified"
CONFIDENCE_UNVERIFIED = "unverified"


@dataclass
class Source:
    """Something a Finding cites. `id` is the stable token (e.g. "S1") a
    Finding's `source_ids` reference; assigned positionally by
    `ResearchReport.from_dict` when the agent didn't supply one."""

    file_path: str
    line_range: str = ""  # e.g. "12-34"; blank when not line-scoped
    excerpt: str = ""  # first ~200 chars of the cited text
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "line_range": self.line_range,
            "excerpt": self.excerpt[:200],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Source":
        return cls(
            id=str(data.get("id", "")).strip(),
            file_path=str(data.get("file_path", "")).strip(),
            line_range=str(data.get("line_range", "")).strip(),
            excerpt=str(data.get("excerpt", ""))[:200],
        )


@dataclass
class Finding:
    text: str
    source_ids: list[str] = field(default_factory=list)
    confidence: str = CONFIDENCE_UNVERIFIED
    category: str = ""

    def __post_init__(self) -> None:
        # Confidence always follows source_ids; a caller cannot construct a
        # Finding claiming "verified" with nothing behind it.
        self.confidence = CONFIDENCE_VERIFIED if self.source_ids else CONFIDENCE_UNVERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source_ids": list(self.source_ids),
            "confidence": self.confidence,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Finding":
        return cls(
            text=str(data.get("text", "")).strip(),
            source_ids=[str(s).strip() for s in (data.get("source_ids") or []) if str(s).strip()],
            category=str(data.get("category", "")).strip(),
        )


@dataclass
class ResearchReport:
    summary: str
    findings: list[Finding] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)

    @property
    def unverified(self) -> list[Finding]:
        """Findings with no source at all -- a derived view, not stored state."""
        return [f for f in self.findings if not f.source_ids]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "sources": [s.to_dict() for s in self.sources],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchReport":
        sources = [Source.from_dict(s) for s in data.get("sources") or [] if isinstance(s, dict)]
        for n, source in enumerate(sources, start=1):
            if not source.id:
                source.id = f"S{n}"
        valid_ids = {s.id for s in sources}

        findings = []
        for raw in data.get("findings") or []:
            if not isinstance(raw, dict) or not str(raw.get("text", "")).strip():
                continue
            finding = Finding.from_dict(raw)
            # Only a source id that actually resolves counts towards
            # "verified" -- an agent cannot invent a source id.
            finding.source_ids = [sid for sid in finding.source_ids if sid in valid_ids]
            finding.confidence = CONFIDENCE_VERIFIED if finding.source_ids else CONFIDENCE_UNVERIFIED
            findings.append(finding)

        return cls(summary=str(data.get("summary", "")).strip(), findings=findings, sources=sources)

    @classmethod
    def from_json(cls, text: str) -> "ResearchReport":
        return cls.from_dict(json.loads(text))
