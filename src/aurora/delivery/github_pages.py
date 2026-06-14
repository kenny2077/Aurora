"""Astro GitHub Pages content delivery."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aurora.config import GitHubPagesDeliveryConfig
from aurora.models import DeliveryResult, RenderedDigest
from aurora.pipeline import StageContext


MODE_LABELS = {
    "unified_digest": "Unified Digest",
    "scholar": "Scholar",
    "repo_learning": "Repo Learning",
    "tech_news": "Tech News",
}


def write_pages_artifact(
    rendered: RenderedDigest,
    context: StageContext,
    config: GitHubPagesDeliveryConfig,
) -> DeliveryResult:
    """Write an AstroPaper-compatible digest content post."""
    content_dir = config.publish_dir
    content_dir.mkdir(parents=True, exist_ok=True)

    digest_time = _site_datetime(context)
    post_path = _write_digest_post(content_dir, rendered, context, digest_time)
    return DeliveryResult(
        channel="github_pages",
        destination=str(post_path),
        metadata={
            "content_dir": str(content_dir),
            "post_path": str(post_path),
            "astro_content": True,
        },
    )


def _write_digest_post(
    content_dir: Path,
    rendered: RenderedDigest,
    context: StageContext,
    digest_time: datetime,
) -> Path:
    post_slug = _post_slug(rendered.mode)
    post_path = content_dir / f"{digest_time:%Y-%m-%d}-{post_slug}.md"
    item_counts = _item_counts(rendered)
    front_matter = _front_matter(
        {
            "title": rendered.title,
            "author": "Aurora System",
            "pubDatetime": digest_time,
            "digest_date": digest_time.strftime("%Y-%m-%d"),
            "featured_repo": rendered.metadata.get("featured_repo") or "",
            "featured_paper": rendered.metadata.get("featured_paper") or "",
            "featured": rendered.mode == "unified_digest",
            "tags": _tags(rendered.mode),
            "description": _description(rendered, item_counts),
        }
    )
    post_path.write_text(f"{front_matter}\n\n{_pages_body(rendered)}\n", encoding="utf-8")
    return post_path


def _pages_body(rendered: RenderedDigest) -> str:
    web_html = rendered.metadata.get("web_html")
    if isinstance(web_html, str) and web_html.strip():
        return web_html.strip()
    return rendered.markdown.rstrip()


def _description(rendered: RenderedDigest, item_counts: dict[str, int]) -> str:
    label = MODE_LABELS.get(rendered.mode, rendered.mode.replace("_", " ").title())
    if item_counts:
        if rendered.mode == "unified_digest":
            return (
                "Daily AI learning radar with "
                f"{_count_phrase(item_counts.get('news'), 'tech news item')}, "
                f"{_count_phrase(item_counts.get('repo'), 'GitHub repo')}, and "
                f"{_count_phrase(item_counts.get('paper'), 'research paper')}."
            )
        details = ", ".join(f"{key}: {value}" for key, value in sorted(item_counts.items()))
        return f"Aurora {label} digest with {details}."
    item_count = _item_count(rendered, item_counts)
    if item_count:
        return f"Aurora {label} digest with {item_count} selected item(s)."
    return f"Aurora {label} digest."


def _count_phrase(value: object, label: str) -> str:
    try:
        count = max(0, int(value or 0))
    except (TypeError, ValueError):
        count = 0
    suffix = "" if count == 1 else "s"
    return f"{count} {label}{suffix}"


def _tags(mode: str) -> list[str]:
    tags = ["digest", "aurora"]
    if mode == "unified_digest":
        tags.extend(["tech-news", "github", "research"])
    else:
        tags.append(mode.replace("_", "-"))
    return tags


def _front_matter(fields: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _yaml_scalar(value: object) -> str:
    if isinstance(value, list):
        return json.dumps([str(item) for item in value])
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(str(value))


def _site_datetime(context: StageContext) -> datetime:
    value = context.until or context.started_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    timezone_name = context.config.run.timezone if context.config is not None else "Asia/Shanghai"
    try:
        target_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        target_timezone = timezone.utc
    return value.astimezone(target_timezone)


def _post_slug(mode: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", mode.lower()).strip("-") or "digest"


def _item_counts(rendered: RenderedDigest) -> dict[str, int]:
    value = rendered.metadata.get("item_counts")
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str) or not key.strip():
            continue
        try:
            counts[key.strip()] = max(0, int(count))
        except (TypeError, ValueError):
            continue
    return counts


def _item_count(rendered: RenderedDigest, item_counts: dict[str, int]) -> int:
    if item_counts:
        return sum(item_counts.values())
    for key in ("selected_item_ids", "recommended_repo_ids"):
        value = rendered.metadata.get(key)
        if isinstance(value, list):
            return len(value)
    match = re.search(r"Selected\s+(\d+)\s+", rendered.markdown)
    if match:
        return int(match.group(1))
    return 0
