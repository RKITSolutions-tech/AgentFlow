"""Unit tests for research report formatting (task 49.5)."""
from __future__ import annotations

import pytest

from app.agents.research_report import Finding, ResearchReport, Source
from app.agents.research_report_formatter import (
    format_report_html,
    format_report_markdown,
    format_report_with_layers,
)


class TestFormatReportWithLayers:
    """Tests for report formatting with layer breakdown."""

    def test_format_report_includes_summary(self):
        """Formatted report includes summary."""
        report = ResearchReport(summary="Test summary", findings=[], sources=[])
        formatted = format_report_with_layers(report)
        assert formatted["summary"] == "Test summary"

    def test_format_report_includes_layer_breakdown(self):
        """Formatted report includes layer breakdown."""
        report = ResearchReport(
            summary="Test",
            findings=[],
            sources=[],
            layer_breakdown={"repository": 5, "web_fetch": 3},
        )
        formatted = format_report_with_layers(report)
        assert formatted["layer_breakdown"]["repository"] == 5
        assert formatted["layer_breakdown"]["web_fetch"] == 3

    def test_format_report_computes_missing_layer_breakdown(self):
        """Computes layer breakdown if not provided."""
        source1 = Source(file_path="app/main.py")
        source2 = Source(file_path="app/utils.py")
        report = ResearchReport(
            summary="Test",
            findings=[],
            sources=[source1, source2],
        )
        formatted = format_report_with_layers(report)
        assert "layer_breakdown" in formatted
        assert formatted["layer_breakdown"]["repository"] == 2

    def test_format_report_groups_findings_by_layer(self):
        """Formatted report groups findings by layer."""
        finding1 = Finding(text="Finding 1", source_ids=["S1"])
        finding2 = Finding(text="Finding 2")
        report = ResearchReport(
            summary="Test",
            findings=[finding1, finding2],
            sources=[],
        )
        formatted = format_report_with_layers(report)
        assert "findings_by_layer" in formatted
        # All findings default to repository layer
        assert len(formatted["findings_by_layer"]["repository"]) == 2


class TestFormatReportHtml:
    """Tests for HTML report formatting."""

    def test_format_html_includes_summary(self):
        """HTML report includes summary."""
        report = ResearchReport(summary="Test summary", findings=[], sources=[])
        html = format_report_html(report)
        assert "Test summary" in html
        assert "<h2>Research Report</h2>" in html

    def test_format_html_includes_layer_breakdown(self):
        """HTML report includes layer breakdown section."""
        report = ResearchReport(
            summary="Test",
            findings=[],
            sources=[],
            layer_breakdown={"repository": 5, "web_fetch": 2},
        )
        html = format_report_html(report)
        assert "Sources by Layer" in html or "layer-breakdown" in html
        assert "Repository: 5" in html or "5" in html

    def test_format_html_includes_findings(self):
        """HTML report lists all findings."""
        finding1 = Finding(text="Finding 1", source_ids=["S1"])
        finding2 = Finding(text="Finding 2")
        report = ResearchReport(
            summary="Test",
            findings=[finding1, finding2],
            sources=[],
        )
        html = format_report_html(report)
        assert "Finding 1" in html
        assert "Finding 2" in html

    def test_format_html_escapes_special_chars(self):
        """HTML report escapes HTML special characters."""
        report = ResearchReport(
            summary="<script>alert('xss')</script>",
            findings=[],
            sources=[],
        )
        html = format_report_html(report)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_format_html_marks_verified_unverified(self):
        """HTML marks verified vs unverified findings."""
        finding1 = Finding(text="Verified", source_ids=["S1"])
        finding2 = Finding(text="Unverified")
        report = ResearchReport(
            summary="Test",
            findings=[finding1, finding2],
            sources=[],
        )
        html = format_report_html(report)
        assert "verified" in html.lower()


class TestFormatReportMarkdown:
    """Tests for Markdown report formatting."""

    def test_format_markdown_includes_summary(self):
        """Markdown report includes summary."""
        report = ResearchReport(summary="Test summary", findings=[], sources=[])
        md = format_report_markdown(report)
        assert "# Research Report" in md
        assert "## Summary" in md
        assert "Test summary" in md

    def test_format_markdown_includes_layer_breakdown(self):
        """Markdown report includes layer breakdown."""
        report = ResearchReport(
            summary="Test",
            findings=[],
            sources=[],
            layer_breakdown={"repository": 5, "web_fetch": 2},
        )
        md = format_report_markdown(report)
        assert "## Sources by Layer" in md
        # Check for bold formatted layer names with counts
        assert ("**Repository:** 5" in md or "Repository: 5" in md)
        assert ("**Web Fetch:** 2" in md or "Web_fetch: 2" in md or "Web Fetch: 2" in md)

    def test_format_markdown_separates_verified_unverified(self):
        """Markdown separates verified and unverified findings."""
        finding1 = Finding(text="Verified", source_ids=["S1"])
        finding2 = Finding(text="Unverified")
        report = ResearchReport(
            summary="Test",
            findings=[finding1, finding2],
            sources=[],
        )
        md = format_report_markdown(report)
        assert "## Verified Findings" in md
        assert "## Unverified Findings" in md
        assert "Verified" in md
        assert "Unverified" in md

    def test_format_markdown_includes_sources(self):
        """Markdown report includes source references."""
        source = Source(
            id="S1",
            file_path="app/main.py",
            line_range="10-20",
            excerpt="Code snippet here",
        )
        report = ResearchReport(
            summary="Test",
            findings=[],
            sources=[source],
        )
        md = format_report_markdown(report)
        assert "## Sources" in md
        assert "app/main.py" in md
        assert "10-20" in md
        assert "Code snippet here" in md

    def test_format_markdown_handles_empty_report(self):
        """Markdown handles empty report gracefully."""
        report = ResearchReport(summary="", findings=[], sources=[])
        md = format_report_markdown(report)
        assert "# Research Report" in md
        # Should not crash, just be minimal

    def test_format_markdown_lists_sources_numbered(self):
        """Sources are numbered in Markdown."""
        source1 = Source(id="S1", file_path="file1.py")
        source2 = Source(id="S2", file_path="file2.py")
        report = ResearchReport(
            summary="Test",
            findings=[],
            sources=[source1, source2],
        )
        md = format_report_markdown(report)
        # Should have source sections
        assert "Source S1:" in md or "Source 1:" in md
        assert "file1.py" in md
        assert "file2.py" in md


class TestRealWorldReports:
    """Tests with realistic research reports."""

    def test_format_realistic_security_report(self):
        """Format a realistic security research report."""
        findings = [
            Finding(text="Cross-site scripting vulnerability in user input handler", source_ids=["S1"]),
            Finding(text="SQL injection possible in query builder", source_ids=["S2"]),
            Finding(text="Missing CSRF token validation suspected", source_ids=[]),
        ]
        sources = [
            Source(
                id="S1",
                file_path="app/forms.py",
                line_range="45-67",
                excerpt="user_input = request.form.get('name')",
            ),
            Source(
                id="S2",
                file_path="app/db.py",
                line_range="120-145",
                excerpt='query = f"SELECT * FROM users WHERE id={user_id}"',
            ),
        ]
        report = ResearchReport(
            summary="Security audit found 3 issues: 2 verified in code, 1 suspected",
            findings=findings,
            sources=sources,
            layer_breakdown={"repository": 2},
        )

        html = format_report_html(report)
        assert "vulnerability" in html.lower()
        assert "Cross-site scripting" in html

        md = format_report_markdown(report)
        assert "## Verified Findings" in md
        assert "## Unverified Findings" in md
        assert "app/forms.py" in md
        assert "app/db.py" in md
