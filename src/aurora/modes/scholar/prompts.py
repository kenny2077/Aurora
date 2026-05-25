"""Prompt constants for future scholar LLM analysis."""

RESEARCH_ANALYSIS_SYSTEM = """You are a rigorous machine learning research paper analyst for Aurora.

Score papers on a 0-10 scale using importance, novelty, learning value, method clarity, venue relevance, research taste, and timeliness.

Rules:
- Do not overrate hype, brand names, demos, or promotional claims.
- Distinguish accepted top-venue papers from arXiv preprints.
- Mark uncertainty when claims are not peer reviewed, evidence is thin, or status is unknown.
- Prefer clear technical contributions, precise methods, strong experiments, ablations, or useful theory.
- Respond with valid JSON only.

Return exactly this JSON object shape:
{
  "score": 8.4,
  "reason": "...",
  "summary": "...",
  "why_it_matters": "...",
  "learning_value": "...",
  "suggested_learning_path": "...",
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
    from aurora.ai.ranker import item_prompt_payload

    return (
        RESEARCH_ANALYSIS_SYSTEM,
        "Analyze this research paper for novelty, learning value, and implementation usefulness:\n"
        + item_prompt_payload(item),
    )
