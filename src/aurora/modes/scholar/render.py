"""Markdown summary and render stages for scholar mode."""

from __future__ import annotations

from collections.abc import Sequence

from aurora.config import ScholarModeConfig
from aurora.modes.scholar.display import format_paper_description, format_paper_source_status
from aurora.models import RenderedDigest, SignalItem
from aurora.pipeline import StageContext


class ScholarSummarizer:
    """Generate a concise Markdown summary for scholar papers."""

    def __init__(self, config: ScholarModeConfig) -> None:
        self.config = config

    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str:
        selected = sorted(items, key=lambda item: item.final_score or 0.0, reverse=True)
        selected = [item for item in selected if (item.final_score or 0.0) >= self.config.score_threshold]
        selected = selected[: self.config.final_item_count]
        lines = ["# Aurora Scholar", "", f"Selected {len(selected)} research paper(s).", ""]
        status_lines = _source_status_lines(context)
        if status_lines:
            lines.extend(["## Source Status", "", *status_lines, ""])
        if not selected:
            lines.append("No research papers met the scholar score threshold.")
            return "\n".join(lines)
        for index, item in enumerate(selected, start=1):
            meta = item.metadata
            authors = ", ".join(meta.get("authors") or []) or "unknown authors"
            summary = format_paper_description(item)
            links = _paper_links(item)
            actions = item.action_items or _fallback_actions(item)
            lines.extend(
                [
                    f"## {index}. [{item.title}]({item.url}) - {item.final_score}/10",
                    "",
                    f"- Source: {format_paper_source_status(item)}",
                    f"- Authors: {authors}",
                    f"- Description: {summary}",
                    f"- Why: {item.why_it_matters or _excerpt(item.raw_content, 180)}",
                    "",
                ]
            )
            if links:
                lines.extend(["Links: " + " | ".join(links), ""])
            if actions:
                lines.extend(["Suggested learning path:", *[f"- {action}" for action in actions], ""])
        return "\n".join(lines).rstrip()


class ScholarRenderer:
    """Render scholar Markdown into a digest payload."""

    async def render(
        self, summary: str, items: Sequence[SignalItem], context: StageContext
    ) -> RenderedDigest:
        return RenderedDigest(mode="scholar", title="Aurora Scholar", markdown=summary)


def _excerpt(value: str, limit: int = 240) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _source_status_lines(context: StageContext) -> list[str]:
    lines: list[str] = []
    run_summary = context.metadata.get("run_summary")
    if isinstance(run_summary, dict):
        source_health = run_summary.get("source_health")
        if isinstance(source_health, dict):
            lines.append(
                "- Sources: "
                f"{int(source_health.get('ok') or 0)} ok, "
                f"{int(source_health.get('failed') or 0)} failed, "
                f"{int(source_health.get('rate_limited') or 0)} rate limited."
            )
    enriched = int(context.metadata.get("semantic_scholar_enriched_count") or 0)
    cached = int(context.metadata.get("semantic_scholar_cached_count") or 0)
    failed = int(context.metadata.get("semantic_scholar_failed_count") or 0)
    requests = int(context.metadata.get("semantic_scholar_requests_made") or 0)
    if enriched or cached or failed or requests or context.metadata.get("semantic_scholar_rate_limited"):
        lines.append(
            "- Semantic Scholar: "
            f"{enriched} enriched, {cached} cached, {failed} failed, {requests} request(s)."
        )
    requested = context.metadata.get("llm_analysis_requested_count")
    if requested is not None:
        succeeded = int(context.metadata.get("llm_analysis_succeeded_count") or 0)
        skipped = int(context.metadata.get("llm_analysis_skipped_count") or 0)
        failed_count = int(context.metadata.get("llm_analysis_failed_count") or 0)
        lines.append(
            "- LLM analysis: "
            f"{succeeded} succeeded, {skipped} skipped, {failed_count} failed."
        )
    for warning in _warning_lines(context):
        lines.append(f"- Warning: {warning}")
    return lines


def _warning_lines(context: StageContext) -> list[str]:
    warnings: list[str] = []
    for key in ("warnings", "semantic_scholar_warnings"):
        value = context.metadata.get(key)
        if not isinstance(value, list):
            continue
        for warning in value:
            text = str(warning).strip()
            if text and text not in warnings:
                warnings.append(text)
    return warnings


def _paper_links(item: SignalItem) -> list[str]:
    meta = item.metadata
    links: list[str] = []
    _append_link(links, "PDF", meta.get("pdf_url"))
    _append_link(links, "Semantic Scholar", meta.get("semantic_scholar_url"))
    for index, url in enumerate(meta.get("code_urls") or [], start=1):
        _append_link(links, "Code" if index == 1 else f"Code {index}", url)
    for index, url in enumerate(meta.get("project_urls") or [], start=1):
        _append_link(links, "Project" if index == 1 else f"Project {index}", url)
    return links


def _append_link(links: list[str], label: str, url: object) -> None:
    text = str(url or "").strip()
    if text.startswith(("http://", "https://")):
        links.append(f"[{label}]({text})")


def _fallback_actions(item: SignalItem) -> list[str]:
    actions = ["Read the abstract and identify the core contribution."]
    if item.metadata.get("code_urls") or item.metadata.get("project_urls"):
        actions.append("Inspect the linked implementation or project page.")
    actions.append("Write one question about the method or evaluation.")
    return actions
