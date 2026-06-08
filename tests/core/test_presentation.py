from __future__ import annotations

from datetime import datetime, timezone

from aurora.models import SignalItem
from aurora.pipeline import StageContext
from aurora.presentation import render_repo_card, render_repo_digest_html, render_unified_digest_html, safe_url


def test_repo_card_includes_evidence_warnings_guidance_and_actions() -> None:
    item = _repo(
        "org/product",
        metadata={
            "stars": 5200,
            "forks": 420,
            "open_issues": 12,
            "language": "Python",
            "topics": ["agents", "mcp"],
            "license": "MIT",
            "homepage": "https://product.dev",
            "package_files": ["pyproject.toml", "examples/run.py"],
            "recommendation_evidence": ["5.2k stars", "README found", "examples/run.py"],
            "quality_warnings": ["high issue load"],
        },
        why="Evidence-backed repository.",
        learn="Study pyproject.toml and examples/run.py.",
        actions=["Inspect pyproject.toml.", "Trace examples/run.py."],
    )

    html = render_repo_card(item)

    assert "org/product" in html
    assert "9.2/10" in html
    assert "5.2k stars" in html
    assert "420 forks" in html
    assert "README found" in html
    assert "high issue load" in html
    assert "Study pyproject.toml and examples/run.py." in html
    assert "Trace examples/run.py." in html


def test_repo_card_escapes_text_and_rejects_unsafe_urls() -> None:
    item = _repo(
        "org/<script>",
        url="https://github.com/org/script?x=<bad>",
        metadata={
            "full_name": "org/<script>",
            "language": "<Python>",
            "topics": ["agent<script>"],
            "recommendation_evidence": ["README <found>"],
        },
        why="<b>unsafe</b>",
        learn="Study <script> tags.",
        actions=["Run <unsafe>."],
    )

    html = render_repo_card(item)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;Python&gt;" in html
    assert "<bad>" not in html
    assert "x=&lt;bad&gt;" in safe_url("https://example.com/?x=<bad>")
    assert safe_url("javascript:alert(1)") == "#"


def test_repo_digest_html_handles_missing_optional_metadata() -> None:
    item = _repo(
        "org/minimal",
        metadata={"full_name": "org/minimal"},
        why="Relevant for learning.",
        learn="Inspect the README.",
        actions=[],
    )

    email_html, web_html = render_repo_digest_html(
        "Aurora Repo Learning",
        [item],
        StageContext(
            mode="repo_learning",
            run_id="test",
            until=datetime(2026, 5, 25, tzinfo=timezone.utc),
        ),
    )

    assert "<!doctype html>" in email_html
    assert "aurora-kpi" in web_html
    assert "org/minimal" in web_html
    assert "not enriched" in web_html
    assert "None" not in web_html


def test_unified_digest_html_hides_top_blocks_and_diagnostics() -> None:
    news = SignalItem(
        id="news:1",
        type="news",
        title="Important News",
        url="https://example.com/news",
        source="hackernews",
        published_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
        summary="A concise news summary.",
        metadata={"source_credibility": "Likely true: primary source announcement."},
        final_score=9.0,
    )
    repo = _repo(
        "org/product",
        metadata={"full_name": "org/product", "recommendation_evidence": ["README found"]},
        why="Worth studying.",
        learn="Study the README.",
    )
    paper = SignalItem(
        id="paper:1",
        type="paper",
        title="Useful Paper",
        url="https://example.com/paper",
        source="arxiv",
        published_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
        summary="Paper summary.",
        learning_value="Learn the evaluation setup.",
        metadata={"venue": "NeurIPS 2025", "status": "spotlight"},
        final_score=8.0,
    )

    _email_html, web_html = render_unified_digest_html(
        "Aurora Unified Digest",
        [news, repo, paper],
        StageContext(
            mode="unified_digest",
            run_id="test",
            metadata={
                "run_summary": {"source_health": {"ok": 1, "failed": 0, "rate_limited": 0}},
                "unified_child_run_summaries": [
                    {"mode": "scholar", "warnings": ["Semantic Scholar rate-limited"]}
                ],
            },
        ),
        [{"theme": "agents", "reason": "shared signal", "evidence_terms": ["agents"]}],
        ["news", "repo", "paper"],
    )

    assert "aurora-kpi" not in web_html
    assert "Today's Learning Path" not in web_html
    assert "Connections" not in web_html
    assert "Run diagnostics" not in web_html
    assert "Semantic Scholar rate-limited" not in web_html
    assert web_html.index("Tech News") < web_html.index("GitHub Repos") < web_html.index("Research Papers")
    assert "Important News" in web_html
    assert "Likely true: primary source announcement." in web_html
    assert "org/product" in web_html
    assert "Useful Paper" in web_html


def _repo(
    full_name: str,
    *,
    metadata: dict,
    url: str | None = None,
    why: str = "",
    learn: str = "",
    actions: list[str] | None = None,
) -> SignalItem:
    return SignalItem(
        id=f"repo:{full_name}",
        type="repo",
        title=full_name,
        url=url or f"https://github.com/{full_name}",
        source="github_search",
        updated_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
        raw_content="Repository description",
        metadata=metadata,
        final_score=9.2,
        why_it_matters=why,
        learning_value=learn,
        action_items=actions or [],
    )
