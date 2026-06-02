"""Polished learning notes for tech news items."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from aurora.models import SignalItem


URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
COMMENT_PREFIX_PATTERN = re.compile(r"(?:^|\s)\[[^\]]{1,40}\]:\s*")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
HTML_ENTITY_PATTERN = re.compile(r"&(?:#x?[0-9a-fA-F]+|[a-zA-Z]+);")


@dataclass(frozen=True)
class TechNewsNotes:
    why_it_matters: str
    learning_value: str
    action_items: list[str]


def build_tech_news_notes(item: SignalItem) -> TechNewsNotes:
    """Build deterministic, digest-ready notes for one tech news item."""
    if _looks_like_hacker_news(item):
        return _hacker_news_notes(item)
    return _rss_notes(item)


def ensure_polished_tech_news_notes(item: SignalItem) -> SignalItem:
    """Replace raw-looking news notes with deterministic polished notes."""
    notes = build_tech_news_notes(item)
    why_is_bad = is_low_quality_note(item.why_it_matters)
    learning_is_bad = is_low_quality_note(item.learning_value)
    updates = {
        "why_it_matters": notes.why_it_matters if why_is_bad else item.why_it_matters,
        "learning_value": notes.learning_value if learning_is_bad else item.learning_value,
    }
    if why_is_bad or learning_is_bad or not item.action_items:
        updates["action_items"] = notes.action_items
    return item.model_copy(update=updates)


def display_tech_news_why(item: SignalItem) -> str:
    if not is_low_quality_note(item.why_it_matters):
        return item.why_it_matters
    return build_tech_news_notes(item).why_it_matters


def display_tech_news_learning(item: SignalItem) -> str:
    if not is_low_quality_note(item.learning_value):
        return item.learning_value
    return build_tech_news_notes(item).learning_value


def clean_note_text(value: str) -> str:
    """Decode and remove markup, raw URLs, and HN author prefixes."""
    text = html.unescape(value or "")
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = COMMENT_PREFIX_PATTERN.sub(" ", text)
    text = URL_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n-:;,.")


def is_low_quality_note(value: str) -> bool:
    raw = value or ""
    cleaned = clean_note_text(raw)
    if not cleaned:
        return True
    unescaped = html.unescape(raw).strip()
    if HTML_ENTITY_PATTERN.search(raw):
        return True
    if URL_PATTERN.search(unescaped):
        return True
    if COMMENT_PREFIX_PATTERN.search(unescaped):
        return True
    if len(cleaned) < 20:
        return True
    return False


def _hacker_news_notes(item: SignalItem) -> TechNewsNotes:
    score = _int_metadata(item, "score")
    comments = _int_metadata(item, "descendants") or _int_metadata(item, "comment_count")
    engagement = _engagement_phrase(score, comments)
    title = clean_note_text(item.title) or "this story"
    why = (
        f"This Hacker News story is drawing {engagement}, so it is worth watching "
        "for shifts in developer attention, AI infrastructure, or product strategy."
    )
    learning = (
        f"Use it to understand why \"{title}\" is attracting attention and what "
        "practical bet it may change for builders or researchers."
    )
    return TechNewsNotes(
        why_it_matters=why,
        learning_value=learning,
        action_items=[
            "Read the source article and the Hacker News discussion.",
            "Identify what changed and who is affected.",
            "Decide whether the story changes a tool, research, or product bet you are making.",
        ],
    )


def _rss_notes(item: SignalItem) -> TechNewsNotes:
    metadata = item.metadata
    feed_name = clean_note_text(str(metadata.get("feed_name") or item.source or "the feed"))
    category = clean_note_text(str(metadata.get("category") or "technology"))
    tags = [
        clean_note_text(str(tag))
        for tag in metadata.get("tags", [])
        if clean_note_text(str(tag))
    ][:3]
    topic = ", ".join(tags) if tags else category
    excerpt = clean_note_text(item.raw_content)
    if not excerpt:
        excerpt = clean_note_text(item.title) or "the reported development"
    why = (
        f"{feed_name} flagged this {topic} story as timely, making it useful for "
        "tracking practical technology and AI ecosystem changes."
    )
    learning = f"Use it to understand the concrete change: {_truncate(excerpt, 180)}."
    return TechNewsNotes(
        why_it_matters=why,
        learning_value=learning,
        action_items=[
            "Read the source article.",
            "Identify the practical change and who is affected.",
            "Decide whether it deserves a follow-up experiment or note.",
        ],
    )


def _looks_like_hacker_news(item: SignalItem) -> bool:
    metadata = item.metadata
    return (
        item.source == "hackernews"
        or "discussion_url" in metadata
        or "descendants" in metadata
        or "comment_count" in metadata
        or "score" in metadata
    )


def _int_metadata(item: SignalItem, key: str) -> int:
    try:
        return max(0, int(item.metadata.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _engagement_phrase(score: int, comments: int) -> str:
    parts: list[str] = []
    if score:
        parts.append(f"{score} points")
    if comments:
        parts.append(f"{comments} comments")
    if not parts:
        return "visible community attention"
    return " and ".join(parts)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."
