"""Markdown rendering for unified_digest mode."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from aurora.config import UnifiedDigestModeConfig
from aurora.modes.scholar.display import format_paper_description, format_paper_source_status
from aurora.modes.tech_news.notes import (
    build_tech_news_notes,
    display_tech_news_learning,
    display_tech_news_source,
    display_tech_news_why,
)
from aurora.modes.unified_digest.connections import build_connections
from aurora.models import RenderedDigest, SignalItem
from aurora.pipeline import StageContext
from aurora.presentation import render_unified_digest_html


SECTION_TITLES = {
    "paper": "Research Papers",
    "repo": "GitHub Repos",
    "news": "Tech News",
}
TOP_VENUES = {
    "aistats",
    "acl",
    "colm",
    "colt",
    "cvpr",
    "emnlp",
    "iclr",
    "icml",
    "mlsys",
    "neurips",
    "nips",
    "tmlr",
    "uai",
}
TOP_VENUE_STATUSES = {
    "accept",
    "accepted",
    "conference paper",
    "oral",
    "poster",
    "published",
    "spotlight",
}
ESTABLISHED_REPO_MIN_STARS = 10_000
ESTABLISHED_REPO_PREFERRED_MIN_STARS = 50_000
ESTABLISHED_REPO_PREFERRED_MAX_STARS = 100_000
HIGH_POTENTIAL_REPO_MIN_STARS = 500
HIGH_POTENTIAL_REPO_MAX_STARS = 5_000
HIGH_POTENTIAL_REPO_PREFERRED_MIN_STARS = 750
HIGH_POTENTIAL_REPO_PREFERRED_MAX_STARS = 3_000
CURRENT_REPO_MIN_YEAR = 2025
NEWS_DIVERSE_MIN_SCORE = 5.5


class UnifiedDigestSummarizer:
    """Create a combined Markdown digest from enriched SignalItems."""

    def __init__(self, config: UnifiedDigestModeConfig) -> None:
        self.config = config

    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str:
        selected = select_items(items, self.config)
        lines = ["# Aurora Unified Digest", ""]
        if not selected:
            lines.append("No items were available for the unified digest.")
            return "\n".join(lines)
        for item_type in self.config.section_order:
            section_items = [item for item in selected if item.type == item_type]
            if not section_items:
                continue
            lines.extend([f"## {SECTION_TITLES[item_type]}", ""])
            if item_type == "paper" and any(item.metadata.get("cached_fallback") for item in section_items):
                lines.extend(
                    [
                        "Using cached scholar results because live sources returned no papers.",
                        "",
                    ]
                )
            for index, item in enumerate(section_items, start=1):
                lines.extend(_section_item_lines(index, item))
            lines.append("")
        return "\n".join(lines).rstrip()


class UnifiedDigestRenderer:
    """Render unified Markdown into a digest payload."""

    def __init__(self, config: UnifiedDigestModeConfig) -> None:
        self.config = config

    async def render(
        self, summary: str, items: Sequence[SignalItem], context: StageContext
    ) -> RenderedDigest:
        selected = select_items(items, self.config)
        connections = build_connections(selected)
        html, web_html = render_unified_digest_html(
            "Aurora Unified Digest",
            selected,
            context,
            connections,
            self.config.section_order,
        )
        return RenderedDigest(
            mode="unified_digest",
            title="Aurora Unified Digest",
            markdown=summary,
            html=html,
            metadata={
                "selected_item_ids": [item.id for item in selected],
                "recommended_repo_ids": [item.id for item in selected if item.type == "repo"],
                "featured_repo": _featured_title(selected, "repo"),
                "featured_paper": _featured_title(selected, "paper"),
                "item_counts": {
                    item_type: sum(1 for item in selected if item.type == item_type)
                    for item_type in self.config.section_order
                },
                "connections": connections,
                "web_html": web_html,
            },
        )


def select_items(
    items: Sequence[SignalItem], config: UnifiedDigestModeConfig
) -> list[SignalItem]:
    selected: list[SignalItem] = []
    for item_type in config.section_order:
        section_items = [item for item in items if item.type == item_type]
        section_items.sort(key=_item_score, reverse=True)
        limit = _section_limit(config, item_type)
        for item in _select_section_items(section_items, item_type, limit):
            if len(selected) >= config.max_total_items:
                return selected
            selected.append(item)
    return selected


def _select_section_items(
    section_items: Sequence[SignalItem], item_type: str, limit: int
) -> list[SignalItem]:
    if item_type == "news" and limit >= 3:
        return _select_news_items(section_items, limit)
    if item_type == "repo" and limit >= 3:
        return _select_repo_items(section_items, limit)
    if item_type != "paper" or limit < 3:
        return list(section_items[:limit])
    return _select_paper_items(section_items, limit)


def _select_news_items(section_items: Sequence[SignalItem], limit: int) -> list[SignalItem]:
    selected: list[SignalItem] = []
    selected_ids: set[str] = set()
    seen_sources: set[str] = set()

    for item in section_items:
        if len(selected) >= limit:
            break
        source = _news_source_key(item)
        if source in seen_sources or _item_score(item) < NEWS_DIVERSE_MIN_SCORE:
            continue
        selected.append(_annotate_selection(item, "news", "source-diverse news item"))
        selected_ids.add(item.id)
        seen_sources.add(source)

    for item in section_items:
        if len(selected) >= limit:
            break
        if item.id in selected_ids:
            continue
        selected.append(_annotate_selection(item, "news", "fallback news selection"))
        selected_ids.add(item.id)

    return selected


def _select_paper_items(section_items: Sequence[SignalItem], limit: int) -> list[SignalItem]:
    selected: list[SignalItem] = []
    selected_ids: set[str] = set()

    for item in [item for item in section_items if _is_current_top_venue_paper(item)][: min(2, limit)]:
        selected.append(_annotate_selection(item, "top_venue", "current top-venue paper"))
        selected_ids.add(item.id)

    if len(selected) < limit:
        for item in section_items:
            if item.id in selected_ids or not _is_arxiv_preprint(item):
                continue
            selected.append(_annotate_selection(item, "high_potential", "high-potential arXiv preprint"))
            selected_ids.add(item.id)
            break

    for item in section_items:
        if len(selected) >= limit:
            break
        if item.id in selected_ids:
            continue
        selected.append(_annotate_selection(item, "fallback", "fallback paper selection"))
        selected_ids.add(item.id)

    return selected


def _select_repo_items(section_items: Sequence[SignalItem], limit: int) -> list[SignalItem]:
    selected: list[SignalItem] = []
    selected_ids: set[str] = set()

    established = sorted(
        [item for item in section_items if _is_established_current_repo(item)],
        key=_established_repo_key,
    )
    for item in established[: min(2, limit)]:
        selected.append(_annotate_selection(item, "classic", "established current repository"))
        selected_ids.add(item.id)

    if len(selected) < limit:
        high_potential = sorted(
            [
                item
                for item in section_items
                if item.id not in selected_ids and _is_high_potential_repo(item)
            ],
            key=_high_potential_repo_key,
        )
        if high_potential:
            selected.append(
                _annotate_selection(
                    high_potential[0],
                    "high_potential",
                    "new high-potential repository",
                )
            )
            selected_ids.add(high_potential[0].id)

    for item in section_items:
        if len(selected) >= limit:
            break
        if item.id in selected_ids:
            continue
        selected.append(_annotate_selection(item, "fallback", "fallback repository selection"))
        selected_ids.add(item.id)

    return selected


def _annotate_selection(item: SignalItem, quality_label: str, selection_reason: str) -> SignalItem:
    metadata = dict(item.metadata)
    metadata["quality_label"] = quality_label
    metadata["selection_reason"] = selection_reason
    return item.model_copy(update={"metadata": metadata})


def _section_limit(config: UnifiedDigestModeConfig, item_type: str) -> int:
    limit = config.section_limits.get(item_type) if hasattr(config, "section_limits") else None
    return limit if limit is not None else config.max_items_per_type


def _featured_title(items: Sequence[SignalItem], item_type: str) -> str:
    candidates = [item for item in items if item.type == item_type]
    candidates.sort(key=_item_score, reverse=True)
    if not candidates:
        return ""
    item = candidates[0]
    return str(item.metadata.get("full_name") or item.title)


def _excerpt(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _section_item_lines(index: int, item: SignalItem) -> list[str]:
    if item.type == "repo":
        return [
            f"{index}. [{item.title}]({item.url})",
            f"   - {_repo_stats_text(item.metadata)}",
            f"   - Value: {_why_text(item)}",
        ]
    if item.type == "paper":
        return [
            f"{index}. [{item.title}]({item.url})",
            f"   - Source: {format_paper_source_status(item)}",
            f"   - Description: {format_paper_description(item)}",
        ]
    return [
        f"{index}. [{item.title}]({item.url})",
        f"   - Source: {display_tech_news_source(item)}",
        f"   - Summary: {_summary_text(item)}",
    ]


def _learning_path_lines(items: Sequence[SignalItem]) -> list[str]:
    paper = _top_item(items, "paper")
    repo = _top_item(items, "repo")
    news_items = _top_items(items, "news", limit=3)
    lines = ["## Today's Learning Path", ""]

    lines.extend(["### Paper to Understand", ""])
    if paper is None:
        lines.extend(["No paper candidate is available for today's learning path.", ""])
    else:
        lines.extend(_learning_item_lines(paper))

    lines.extend(["### Repo to Study", ""])
    if repo is None:
        lines.extend(["No repository candidate is available for today's learning path.", ""])
    else:
        lines.extend(_learning_item_lines(repo))

    lines.extend(["### News to Watch", ""])
    if not news_items:
        lines.extend(["No news item is available for today's learning path.", ""])
    else:
        for item in news_items:
            lines.extend(_learning_item_lines(item))

    return lines


def _run_summary_lines(context: StageContext) -> list[str]:
    run_summary = context.metadata.get("run_summary")
    child_summaries = context.metadata.get("unified_child_run_summaries")
    mode_failures = context.metadata.get("unified_mode_failures")
    if not isinstance(run_summary, dict) and not child_summaries and not mode_failures:
        return []

    lines = ["## Run Summary", ""]
    if isinstance(run_summary, dict):
        counts = run_summary.get("counts")
        if isinstance(counts, dict):
            lines.append(
                "Items: "
                f"{int(counts.get('raw') or 0)} raw -> "
                f"{int(counts.get('normalized') or 0)} normalized -> "
                f"{int(counts.get('deduplicated') or 0)} deduplicated -> "
                f"{int(counts.get('enriched') or 0)} enriched."
            )
        health = run_summary.get("source_health")
        if isinstance(health, dict):
            lines.append(
                "Sources: "
                f"{int(health.get('ok') or 0)} ok, "
                f"{int(health.get('failed') or 0)} failed, "
                f"{int(health.get('rate_limited') or 0)} rate limited."
            )
        source_lines = _failed_source_lines(run_summary.get("sources"))
        if source_lines:
            lines.extend(source_lines)

    child_line = _child_mode_summary_line(child_summaries)
    if child_line:
        lines.append(child_line)
    child_warning_lines = _child_warning_lines(child_summaries)
    if child_warning_lines:
        lines.extend(child_warning_lines)

    if isinstance(mode_failures, list):
        for failure in mode_failures:
            if not isinstance(failure, dict):
                continue
            mode = str(failure.get("mode") or "unknown")
            error = str(failure.get("error") or "unknown error")
            lines.append(f"Mode {mode} failed: {error}")

    lines.append("")
    return lines


def _failed_source_lines(sources: object) -> list[str]:
    if not isinstance(sources, list):
        return []
    lines: list[str] = []
    for source in sources:
        if not isinstance(source, dict) or source.get("ok", True):
            continue
        name = str(source.get("source") or "unknown")
        error = str(source.get("error") or "unknown error")
        lines.append(f"{name} failed: {error}")
    return lines


def _child_mode_summary_line(child_summaries: object) -> str:
    if not isinstance(child_summaries, list):
        return ""
    parts: list[str] = []
    for summary in child_summaries:
        if not isinstance(summary, dict):
            continue
        mode = str(summary.get("mode") or "unknown")
        counts = summary.get("counts")
        enriched = int(counts.get("enriched") or 0) if isinstance(counts, dict) else 0
        parts.append(f"{mode} {enriched} item(s)")
    if not parts:
        return ""
    return f"Child modes: {', '.join(parts)}."


def _child_warning_lines(child_summaries: object) -> list[str]:
    if not isinstance(child_summaries, list):
        return []
    lines: list[str] = []
    for summary in child_summaries:
        if not isinstance(summary, dict):
            continue
        mode = str(summary.get("mode") or "unknown")
        warnings = summary.get("warnings")
        if not isinstance(warnings, list):
            continue
        for warning in warnings:
            text = str(warning).strip()
            if text:
                lines.append(f"{mode} warning: {text}")
    return lines


def _connection_lines(items: Sequence[SignalItem]) -> list[str]:
    connections = build_connections(items)
    if not connections:
        return []
    lines = ["## Connections", ""]
    by_id = {item.id: item for item in items}
    for connection in connections:
        item_ids = connection["item_ids"]
        connected_items = [by_id[item_id] for item_id in item_ids if item_id in by_id]
        if len(connected_items) != 2:
            continue
        first, second = connected_items
        lines.append(
            f"- {connection['theme']}: [{first.title}]({first.url}) + "
            f"[{second.title}]({second.url}): {connection['reason']} "
            f"(evidence: {', '.join(connection['evidence_terms'])})."
        )
    lines.append("")
    return lines


def _top_item(items: Sequence[SignalItem], item_type: str) -> SignalItem | None:
    top_items = _top_items(items, item_type, limit=1)
    return top_items[0] if top_items else None


def _top_items(items: Sequence[SignalItem], item_type: str, *, limit: int) -> list[SignalItem]:
    matching = [item for item in items if item.type == item_type]
    matching.sort(key=_item_score, reverse=True)
    return matching[:limit]


def _learning_item_lines(item: SignalItem) -> list[str]:
    why = _why_text(item)
    lines = [
        f"- [{item.title}]({item.url})",
        f"  - Source: {item.source}",
        f"  - Why: {why}",
    ]
    if item.type != "paper":
        lines.append(f"  - Learn: {_learning_text(item)}")
    lines.extend(_repo_signal_lines(item, indent="  "))
    action_items = _action_items(item)
    if action_items:
        lines.append(f"  - Action: {'; '.join(action_items)}")
    lines.append("")
    return lines


def _repo_signal_lines(item: SignalItem, *, indent: str) -> list[str]:
    return []


def _paper_signal_lines(item: SignalItem, *, indent: str) -> list[str]:
    if item.type != "paper":
        return []
    lines: list[str] = []
    action_items = _action_items(item)
    if action_items:
        lines.append(f"{indent}- Action: {'; '.join(action_items)}")
    semantic_url = str(item.metadata.get("semantic_scholar_url") or "").strip()
    if semantic_url:
        lines.append(f"{indent}- Semantic Scholar: {semantic_url}")
    return lines


def _metadata_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _news_source_key(item: SignalItem) -> str:
    return display_tech_news_source(item).strip().lower() or item.source


def _is_established_current_repo(item: SignalItem) -> bool:
    return _repo_stars(item) >= ESTABLISHED_REPO_MIN_STARS and _is_current_repo(item)


def _is_high_potential_repo(item: SignalItem) -> bool:
    stars = _repo_stars(item)
    return (
        HIGH_POTENTIAL_REPO_MIN_STARS <= stars <= HIGH_POTENTIAL_REPO_MAX_STARS
        and _is_current_repo(item)
        and _is_new_repo(item)
    )


def _established_repo_key(item: SignalItem) -> tuple[int, float]:
    stars = _repo_stars(item)
    preferred_band = not (
        ESTABLISHED_REPO_PREFERRED_MIN_STARS
        <= stars
        <= ESTABLISHED_REPO_PREFERRED_MAX_STARS
    )
    return (int(preferred_band), -_item_score(item))


def _high_potential_repo_key(item: SignalItem) -> tuple[int, float]:
    stars = _repo_stars(item)
    preferred_band = not (
        HIGH_POTENTIAL_REPO_PREFERRED_MIN_STARS
        <= stars
        <= HIGH_POTENTIAL_REPO_PREFERRED_MAX_STARS
    )
    return (int(preferred_band), -_item_score(item))


def _repo_stars(item: SignalItem) -> int:
    try:
        return int(item.metadata.get("stars") or 0)
    except (TypeError, ValueError):
        return 0


def _is_current_repo(item: SignalItem) -> bool:
    years = [
        _year_from_datetime_like(item.updated_at),
        _year_from_datetime_like(item.metadata.get("updated_at")),
        _year_from_datetime_like(item.metadata.get("pushed_at")),
    ]
    years = [year for year in years if year is not None]
    return not years or max(years) >= CURRENT_REPO_MIN_YEAR


def _is_new_repo(item: SignalItem) -> bool:
    created_year = _year_from_datetime_like(item.metadata.get("created_at"))
    return created_year is None or created_year >= CURRENT_REPO_MIN_YEAR


def _year_from_datetime_like(value: object) -> int | None:
    if isinstance(value, datetime):
        return value.year
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).year
        except ValueError:
            return None
    return None


def _is_current_top_venue_paper(item: SignalItem) -> bool:
    metadata = item.metadata
    venue = _normalized_text(metadata.get("venue") or "")
    if not any(top_venue in venue for top_venue in TOP_VENUES):
        return False
    try:
        year = int(metadata.get("venue_year") or 0)
    except (TypeError, ValueError):
        year = 0
    if year < 2025:
        return False
    status = _normalized_text(metadata.get("status") or "")
    return any(top_status in status for top_status in TOP_VENUE_STATUSES)


def _is_arxiv_preprint(item: SignalItem) -> bool:
    status = _normalized_text(item.metadata.get("status") or "")
    return item.source == "arxiv" or "preprint" in status


def _normalized_text(value: object) -> str:
    return str(value or "").strip().lower()


def _repo_stats_text(metadata: dict) -> str:
    return " | ".join(
        [
            f"{_format_count(metadata.get('stars'))} stars",
            f"{_format_count(metadata.get('forks'))} forks",
            f"{_format_count(metadata.get('open_issues'))} open issues",
        ]
    )


def _format_count(value: object) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return "0"
    if number < 1000:
        return str(number)
    compact = f"{number / 1000:.1f}".rstrip("0").rstrip(".")
    return f"{compact}k"


def _why_text(item: SignalItem) -> str:
    if item.type == "news":
        return display_tech_news_why(item)
    return item.why_it_matters or item.summary or _excerpt(item.raw_content, 160)


def _summary_text(item: SignalItem) -> str:
    if item.type == "news":
        return item.summary or display_tech_news_why(item)
    return item.summary or item.why_it_matters or _excerpt(item.raw_content, 180)


def _learning_text(item: SignalItem) -> str:
    if item.type == "news":
        return display_tech_news_learning(item)
    return item.learning_value or item.summary or _excerpt(item.raw_content, 160)


def _action_items(item: SignalItem) -> list[str]:
    if item.action_items:
        return list(item.action_items)
    if item.type == "paper":
        actions = ["Read the abstract and identify the core claim."]
        if item.metadata.get("code_urls") or item.metadata.get("project_urls"):
            actions.append("Inspect the linked code or project page.")
        actions.append("Write one implementation question to investigate next.")
        return actions
    if item.type == "news":
        return build_tech_news_notes(item).action_items
    return []


def _item_score(item: SignalItem) -> float:
    return item.final_score if item.final_score is not None else item.deterministic_score
