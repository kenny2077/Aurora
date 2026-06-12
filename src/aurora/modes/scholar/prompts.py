"""Prompt constants for scholar LLM analysis."""

from __future__ import annotations

import json

RESEARCH_ANALYSIS_SYSTEM = """You are a rigorous machine learning research paper analyst for Aurora.

Score papers on a 0-10 scale using importance, practical future value, influence, learning value, method clarity, venue relevance, research taste, and timeliness.

Rules:
- Do not overrate hype, brand names, demos, or promotional claims.
- Distinguish accepted top-venue papers from arXiv preprints.
- Mark uncertainty when claims are not peer reviewed, evidence is thin, or status is unknown.
- Prefer high-influence papers with strong evidence, useful implementations, datasets, systems, benchmarks, or clear practical applications.
- Write "summary" as one or two short plain-language sentences for students. Avoid dense jargon when a simpler explanation is accurate.
- Respond with valid JSON only.

Return exactly this JSON object shape:
{
  "score": 8.4,
  "reason": "...",
  "summary": "...",
  "why_it_matters": "...",
  "learning_value": "...",
  "suggested_learning_path": "...",
  "action_items": ["..."],
  "tags": ["..."]
}"""

RESEARCH_ANALYSIS_USER = """Analyze this machine learning research paper.

Paper:
- Title: {title}
- Authors: {authors}
- Source: {source}
- URL: {url}
- PDF: {pdf_url}
- Venue: {venue}
- Venue year: {venue_year}
- Status: {status}
- Published: {published_at}
- Updated: {updated_at}
- Categories: {categories}
- Code URLs: {code_urls}
- Project URLs: {project_urls}
- Citation count: {citation_count}
- Source IDs: {source_ids}
- Deterministic score: {deterministic_score}

Abstract:
{abstract}

Evaluate the paper as a daily research briefing candidate for students/researchers.
Respond with valid JSON only."""


def build_scholar_prompt(item) -> tuple[str, str]:
    """Build an optional LLM analysis prompt for one normalized paper."""
    metadata = item.metadata
    return (
        RESEARCH_ANALYSIS_SYSTEM,
        RESEARCH_ANALYSIS_USER.format(
            title=item.title,
            authors=", ".join(str(author) for author in metadata.get("authors") or []),
            source=item.source,
            url=str(item.url),
            pdf_url=metadata.get("pdf_url") or "",
            venue=metadata.get("venue") or "",
            venue_year=metadata.get("venue_year") or "",
            status=metadata.get("status") or "",
            published_at=item.published_at,
            updated_at=item.updated_at,
            categories=", ".join(str(category) for category in metadata.get("categories") or []),
            code_urls=", ".join(str(url) for url in metadata.get("code_urls") or []),
            project_urls=", ".join(str(url) for url in metadata.get("project_urls") or []),
            citation_count=metadata.get("citation_count") or 0,
            source_ids=json.dumps(metadata.get("source_ids") or {}, sort_keys=True),
            deterministic_score=item.deterministic_score,
            abstract=item.raw_content[:4000],
        ),
    )
