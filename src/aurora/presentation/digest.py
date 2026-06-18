"""Clean SaaS-style HTML presentation helpers for Aurora digests."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone
from html import escape
from typing import Any
from urllib.parse import urlparse

from aurora.models import SignalItem
from aurora.modes.scholar.display import format_paper_description, format_paper_source_status
from aurora.modes.tech_news.notes import display_tech_news_source, display_tech_news_summary
from aurora.pipeline import StageContext
from aurora.public_copy import format_repo_value


EMAIL_CSS = """
body{margin:0;background:#f5f7fb;color:#172033;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
a{color:#0b63ce;text-decoration:none;}
.aurora-email{max-width:760px;margin:0 auto;padding:24px 16px;}
.aurora-shell{background:#ffffff;border:1px solid #d9e2ee;border-radius:8px;overflow:hidden;}
.aurora-hero{background:#111827;color:#ffffff;padding:26px;}
.aurora-hero h1{font-size:24px;line-height:1.2;margin:0 0 8px;}
.aurora-hero p{color:#cbd5e1;margin:0;}
.aurora-body{padding:22px;}
.aurora-section{margin:0 0 26px;}
.aurora-section h2{font-size:17px;color:#172033;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid #e5edf6;}
.aurora-kpis{display:block;margin:0 0 20px;}
.aurora-kpi{display:inline-block;vertical-align:top;width:22%;min-width:118px;margin:0 8px 8px 0;padding:12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;}
.aurora-kpi b{display:block;color:#172033;font-size:18px;}
.aurora-kpi span{color:#64748b;font-size:12px;}
.aurora-card{border:1px solid #dce4ee;border-radius:8px;background:#ffffff;margin:0 0 14px;padding:16px;}
.aurora-card h3{font-size:16px;line-height:1.3;margin:0 0 8px;}
.aurora-meta{color:#64748b;font-size:12px;margin:0 0 10px;}
.aurora-badge{display:inline-block;background:#f1f5f9;color:#334155;border:1px solid #e2e8f0;border-radius:6px;padding:3px 7px;margin:0 4px 4px 0;font-size:12px;}
.aurora-badge-good{background:#ecfdf5;color:#047857;border-color:#bbf7d0;}
.aurora-callout{background:#f8fafc;color:#334155;border:1px solid #dce4ee;border-left:4px solid #0b63ce;border-radius:8px;padding:10px 12px;margin:10px 0;font-size:13px;line-height:1.5;}
.aurora-callout b{color:#172033;}
.aurora-card p{line-height:1.5;margin:8px 0;color:#334155;}
.aurora-row{border:1px solid #e2e8f0;border-radius:8px;background:#ffffff;margin:0 0 12px;padding:14px;}
.aurora-row h3{font-size:15px;margin:0 0 4px;}
"""


def email_document(title: str, body_html: str, *, subtitle: str = "") -> str:
    """Wrap a digest fragment in a conservative email-safe HTML document."""
    subtitle_html = f"<p>{escape(subtitle)}</p>" if subtitle else ""
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{EMAIL_CSS}</style></head>"
        "<body><div class=\"aurora-email\"><div class=\"aurora-shell\">"
        f"<header class=\"aurora-hero\"><h1>{escape(title)}</h1>{subtitle_html}</header>"
        f"<main class=\"aurora-body\">{body_html}</main>"
        "</div></div></body></html>\n"
    )


def render_repo_digest_html(
    title: str,
    items: Sequence[SignalItem],
    context: StageContext,
) -> tuple[str, str]:
    """Return full email HTML and Pages-safe fragment for a repo digest."""
    warnings = sum(1 for item in items if _text_list(item.metadata.get("quality_warnings")))
    languages = _top_terms(item.metadata.get("language") for item in items)
    topics = _top_terms(topic for item in items for topic in item.metadata.get("topics") or [])
    stats = [
        ("Repos", str(len(items))),
        ("Warnings", str(warnings)),
        ("Top signal", languages[0] if languages else (topics[0] if topics else "none")),
    ]
    body = [
        render_stat_band(stats),
        '<section class="aurora-section"><h2>Repository recommendations</h2>',
        *(render_repo_card(item) for item in items),
        "</section>",
    ]
    fragment = "".join(body)
    subtitle = f"Generated {format_datetime(context.until or context.started_at)}"
    return email_document(title, fragment, subtitle=subtitle), fragment


def render_unified_digest_html(
    title: str,
    selected: Sequence[SignalItem],
    context: StageContext,
    connections: Sequence[dict[str, Any]],
    section_order: Sequence[str],
) -> tuple[str, str]:
    """Return full email HTML and Pages-safe fragment for a unified digest."""
    sections = []
    for item_type in section_order:
        items = [item for item in selected if item.type == item_type]
        if not items:
            continue
        if item_type == "repo":
            sections.append(
                '<section class="aurora-section"><h2>GitHub Repos</h2>'
                + "".join(render_repo_card(item) for item in items)
                + "</section>"
            )
        else:
            title_text = "Research Papers" if item_type == "paper" else "Tech News"
            sections.append(
                f'<section class="aurora-section"><h2>{title_text}</h2>'
                + "".join(render_item_row(item) for item in items)
                + "</section>"
            )
    fragment = "".join(section for section in sections if section)
    return email_document(title, fragment), fragment


def render_stat_band(stats: Sequence[tuple[str, str]]) -> str:
    cards = "".join(
        f'<div class="aurora-kpi"><b>{escape(value)}</b><span>{escape(label)}</span></div>'
        for label, value in stats
    )
    return f'<section class="aurora-kpis">{cards}</section>'


def render_repo_card(item: SignalItem) -> str:
    metadata = item.metadata
    title = str(metadata.get("full_name") or item.title)
    stats = _repo_stats(metadata)
    badges = _repo_badges(metadata)
    badge_html = "".join(f'<span class="aurora-badge">{escape(badge)}</span>' for badge in badges)
    return (
        '<article class="aurora-card aurora-repo-card">'
        f'<h3><a {link_attrs(str(item.url))}>{escape(title)}</a></h3>'
        f'<p class="aurora-meta">{escape(stats)}</p>'
        f"{badge_html}"
        f'{_callout("Value", format_repo_value(item), "aurora-callout")}'
        "</article>"
    )


def render_item_row(item: SignalItem) -> str:
    body = item.summary or item.why_it_matters or item.raw_content
    meta = item.source
    extra_html = ""
    if item.type == "paper":
        body = format_paper_description(item)
        meta = format_paper_source_status(item)
    elif item.type == "news":
        meta = display_tech_news_source(item)
        body = display_tech_news_summary(item)
    return (
        '<article class="aurora-row">'
        f'<h3><a {link_attrs(str(item.url))}>{escape(item.title)}</a></h3>'
        f'<p class="aurora-meta">{escape(str(meta))}</p>'
        f"<p>{escape(body)}</p>"
        f"{extra_html}"
        "</article>"
    )


def safe_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return escape(value, quote=True)
    return "#"


def link_attrs(value: str) -> str:
    href = safe_url(value)
    if href == "#":
        return 'href="#"'
    return f'href="{href}" target="_blank" rel="noopener noreferrer"'


def format_count(value: object) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return "0"
    if number < 1000:
        return str(number)
    compact = f"{number / 1000:.1f}".rstrip("0").rstrip(".")
    return f"{compact}k"


def format_datetime(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M %Z").strip()


def _learning_path_html(items: Sequence[SignalItem]) -> str:
    cards = []
    for item_type, label in (("paper", "Paper to understand"), ("repo", "Repo to study"), ("news", "News to watch")):
        item = _top_item(items, item_type)
        if item is None:
            cards.append(
                '<article class="aurora-card"><h3>'
                f"{escape(label)}</h3><p>No candidate available.</p></article>"
            )
            continue
        if item.type == "repo":
            cards.append(render_repo_card(item))
        else:
            description = (
                format_paper_description(item)
                if item.type == "paper"
                else item.why_it_matters or item.summary or item.raw_content
            )
            cards.append(
                '<article class="aurora-card">'
                f'<h3>{escape(label)}</h3>'
                f'<p><a href="{safe_url(str(item.url))}">{escape(item.title)}</a></p>'
                f"<p>{escape(description)}</p>"
                "</article>"
            )
    return '<section class="aurora-section"><h2>Today\'s Learning Path</h2>' + "".join(cards) + "</section>"


def _connections_html(connections: Sequence[dict[str, Any]]) -> str:
    if not connections:
        return ""
    rows = []
    for connection in connections[:8]:
        theme = str(connection.get("theme") or "connection")
        reason = str(connection.get("reason") or "")
        evidence = ", ".join(str(term) for term in connection.get("evidence_terms") or [])
        rows.append(
            '<article class="aurora-row">'
            f"<h3>{escape(theme)}</h3>"
            f"<p>{escape(reason)}</p>"
            f'<p class="aurora-meta">Evidence: {escape(evidence)}</p>'
            "</article>"
        )
    return '<section class="aurora-section"><h2>Connections</h2>' + "".join(rows) + "</section>"


def _diagnostics_html(context: StageContext) -> str:
    lines: list[str] = []
    run_summary = context.metadata.get("run_summary")
    if isinstance(run_summary, dict):
        health = run_summary.get("source_health")
        if isinstance(health, dict):
            lines.append(
                "Sources: "
                f"{int(health.get('ok') or 0)} ok, "
                f"{int(health.get('failed') or 0)} failed, "
                f"{int(health.get('rate_limited') or 0)} rate limited."
            )
    for summary in context.metadata.get("unified_child_run_summaries") or []:
        if not isinstance(summary, dict):
            continue
        mode = str(summary.get("mode") or "unknown")
        for warning in summary.get("warnings") or []:
            lines.append(f"{mode}: {warning}")
    for failure in context.metadata.get("unified_mode_failures") or []:
        if isinstance(failure, dict):
            lines.append(f"{failure.get('mode') or 'unknown'} failed: {failure.get('error') or 'unknown error'}")
    if not lines:
        return ""
    return (
        '<section class="aurora-section"><h2>Run diagnostics</h2>'
        + "".join(f'<div class="aurora-warning">{escape(line)}</div>' for line in lines)
        + "</section>"
    )


def _repo_stats(metadata: dict[str, Any]) -> str:
    return " | ".join(
        [
            f"{format_count(metadata.get('stars'))} stars",
            f"{format_count(metadata.get('forks'))} forks",
            f"{format_count(metadata.get('open_issues'))} open issues",
        ]
    )


def _repo_badges(metadata: dict[str, Any]) -> list[str]:
    badges: list[str] = [_repo_quality_label(metadata)]
    language = str(metadata.get("language") or "").strip()
    if language and language not in badges:
        badges.append(language)
    for topic in metadata.get("topics") or []:
        topic_text = str(topic).strip()
        if topic_text and topic_text not in badges:
            badges.append(topic_text)
        if len(badges) >= 4:
            break
    return badges


def _repo_quality_label(metadata: dict[str, Any]) -> str:
    configured = str(metadata.get("quality_label") or "").strip()
    if configured.lower() == "fallback":
        return "Learning Pick"
    if configured:
        return configured.replace("_", " ").title()
    stars = _int_metadata(metadata, "stars")
    if stars >= 10_000:
        return "Classic"
    if 500 <= stars <= 5_000:
        return "High potential"
    return "Learning pick"


def _int_metadata(metadata: dict[str, Any], key: str) -> int:
    try:
        return int(metadata.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _callout(label: str, value: str, class_name: str) -> str:
    text = value.strip()
    if not text:
        return ""
    return f'<div class="{class_name}"><b>{escape(label)}:</b> {escape(text)}</div>'


def _top_item(items: Sequence[SignalItem], item_type: str) -> SignalItem | None:
    matching = [item for item in items if item.type == item_type]
    matching.sort(key=_score, reverse=True)
    return matching[0] if matching else None


def _top_terms(values) -> list[str]:
    terms: list[str] = []
    for value in values:
        term = str(value).strip()
        if term and term.lower() != "none":
            terms.append(term)
    counter = Counter(terms)
    return [value for value, _count in counter.most_common(3)]


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _score(item: SignalItem) -> float:
    return item.final_score if item.final_score is not None else item.deterministic_score or 0.0
