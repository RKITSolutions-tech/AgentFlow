"""Research report formatting with layer breakdowns (task 49.5).

Formats research reports to highlight which layer each source came from
(repository, knowledge_base, web_search, web_fetch) for transparency in
research findings.
"""
from __future__ import annotations

import json
from typing import Any

from app.agents.research_report import ResearchReport


def format_report_with_layers(report: ResearchReport) -> dict[str, Any]:
    """Format a ResearchReport with layer breakdown information.

    Enhances the standard report with:
    - layer_breakdown counts per source type
    - findings grouped by layer in the report
    - visual metadata for UI rendering

    Returns a dict suitable for JSON serialization and display.
    """
    base_dict = report.to_dict()

    # Add layer breakdown if not already present
    if not base_dict.get("layer_breakdown"):
        base_dict["layer_breakdown"] = _compute_layer_breakdown(report)

    # Group findings by their source layer for UI display
    base_dict["findings_by_layer"] = _group_findings_by_layer(report)

    return base_dict


def _compute_layer_breakdown(report: ResearchReport) -> dict[str, int]:
    """Compute finding counts per layer from source references.

    If layer_breakdown is already set on the report, use it.
    Otherwise, infer layers from source metadata (if available).
    """
    if report.layer_breakdown:
        return report.layer_breakdown

    # Default breakdown: assume all sources are from repository layer
    # (task 49.5 will add layer metadata to sources when web research is integrated)
    return {
        "repository": len(report.sources),
        "knowledge_base": 0,
        "web_search": 0,
        "web_fetch": 0,
    }


def _group_findings_by_layer(report: ResearchReport) -> dict[str, list[dict[str, Any]]]:
    """Group findings by their inferred source layer.

    For now, all findings are assumed to be from repository unless explicitly
    marked with layer metadata. Task 49.5 will enhance this when web research
    is integrated and findings carry layer information.
    """
    grouped: dict[str, list[dict[str, Any]]] = {
        "repository": [],
        "knowledge_base": [],
        "web_search": [],
        "web_fetch": [],
    }

    for finding in report.findings:
        # Default to repository layer
        # In the future, finding.layer will carry this info
        layer = getattr(finding, "layer", "repository")
        grouped[layer].append(finding.to_dict())

    return grouped


def format_report_html(report: ResearchReport) -> str:
    """Format a ResearchReport as HTML with layer breakdown sections.

    Creates a readable HTML representation with:
    - Summary section
    - Layer-organized findings
    - Source references grouped by layer

    Returns HTML string (escaped for safe display).
    """
    import html

    lines = [
        '<div class="research-report">',
        '<h2>Research Report</h2>',
    ]

    # Summary section
    if report.summary:
        lines.append(f'<section class="summary"><h3>Summary</h3><p>{html.escape(report.summary)}</p></section>')

    # Layer breakdown section
    if report.layer_breakdown:
        lines.append('<section class="layer-breakdown"><h3>Sources by Layer</h3><ul>')
        for layer, count in sorted(report.layer_breakdown.items()):
            if count > 0:
                layer_name = layer.replace("_", " ").title()
                lines.append(f"<li>{layer_name}: {count}</li>")
        lines.append("</ul></section>")

    # Findings section
    if report.findings:
        lines.append('<section class="findings"><h3>Findings</h3><ol>')
        for finding in report.findings:
            confidence_class = f"confidence-{finding.confidence}"
            text = html.escape(finding.text)
            sources_text = ""
            if finding.source_ids:
                sources_text = f' <span class="sources">(Sources: {", ".join(finding.source_ids)})</span>'
            lines.append(
                f'<li class="{confidence_class}">{text}{sources_text}</li>'
            )
        lines.append("</ol></section>")

    # Sources section
    if report.sources:
        lines.append('<section class="sources"><h3>Sources</h3><ol>')
        for source in report.sources:
            source_text = f'<a href="#">{html.escape(source.file_path)}</a>'
            if source.line_range:
                source_text += f' <code>{html.escape(source.line_range)}</code>'
            excerpt = html.escape(source.excerpt) if source.excerpt else ""
            excerpt_section = f' <blockquote>{excerpt}</blockquote>' if excerpt else ""
            lines.append(f"<li>{source_text}{excerpt_section}</li>")
        lines.append("</ol></section>")

    lines.append("</div>")
    return "\n".join(lines)


def format_report_markdown(report: ResearchReport) -> str:
    """Format a ResearchReport as Markdown with layer breakdown.

    Creates a readable Markdown document with sections for:
    - Summary
    - Layer breakdown counts
    - Findings organized by confidence
    - Source references

    Returns Markdown string.
    """
    lines = ["# Research Report\n"]

    # Summary section
    if report.summary:
        lines.append(f"## Summary\n\n{report.summary}\n")

    # Layer breakdown section
    if report.layer_breakdown:
        lines.append("## Sources by Layer\n")
        for layer, count in sorted(report.layer_breakdown.items()):
            if count > 0:
                layer_name = layer.replace("_", " ").title()
                lines.append(f"- **{layer_name}:** {count}")
        lines.append("")

    # Findings section
    if report.findings:
        # Group by confidence
        verified = [f for f in report.findings if f.source_ids]
        unverified = [f for f in report.findings if not f.source_ids]

        if verified:
            lines.append("## Verified Findings\n")
            for finding in verified:
                source_str = f" (Sources: {', '.join(finding.source_ids)})"
                lines.append(f"- {finding.text}{source_str}")
            lines.append("")

        if unverified:
            lines.append("## Unverified Findings\n")
            for finding in unverified:
                lines.append(f"- {finding.text} (unverified)")
            lines.append("")

    # Sources section
    if report.sources:
        lines.append("## Sources\n")
        for i, source in enumerate(report.sources, start=1):
            lines.append(f"\n### Source {source.id or i}: {source.file_path}")
            if source.line_range:
                lines.append(f"**Line Range:** `{source.line_range}`")
            if source.excerpt:
                lines.append(f"\n```\n{source.excerpt}\n```")

    return "\n".join(lines)
