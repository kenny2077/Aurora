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


def display_tech_news_source(item: SignalItem) -> str:
    if item.source == "hackernews":
        return "Hacker News"
    if item.source == "github_releases":
        return "GitHub Releases"
    if item.source == "reddit":
        return "Reddit"
    if item.source == "rss":
        feed_name = clean_note_text(str(item.metadata.get("feed_name") or ""))
        if feed_name:
            return feed_name
    return clean_note_text(item.source.replace("_", " ")).title() or item.source


def display_tech_news_summary(item: SignalItem) -> str:
    """Return the public one-sentence summary for a news item."""
    if _has_incomplete_source_sentence(item.summary):
        return display_tech_news_why(item)
    if not is_low_quality_note(item.summary):
        return _public_sentence(clean_note_text(item.summary), item.title)
    return display_tech_news_why(item)


def clean_note_text(value: str) -> str:
    """Decode and remove markup, raw URLs, and HN author prefixes."""
    text = html.unescape(value or "")
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = COMMENT_PREFIX_PATTERN.sub(" ", text)
    text = URL_PATTERN.sub(" ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?m)^\s*[*-]\s+", " ", text)
    text = text.replace("`", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    return text.strip(" \t\r\n#-:;,")


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
    lowered = cleaned.lower()
    if "flagged this" in lowered and "story as timely" in lowered:
        return True
    if re.search(r"\bRelease Notes\b", cleaned):
        return True
    if _has_dangling_end(cleaned):
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
        f"Developers are discussing \"{title}\" with {engagement}, making it a useful "
        "signal for tool adoption, AI infrastructure, or product strategy shifts."
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
    if item.source == "github_releases":
        return _github_release_notes(item)

    metadata = item.metadata
    excerpt = clean_note_text(item.raw_content)
    if not excerpt:
        excerpt = clean_note_text(item.title)
    why = _source_public_sentence(excerpt, item.title)
    learning = "Use it to understand the concrete change: " + _with_terminal_punctuation(
        _truncate(_strip_title_prefix(excerpt, item.title), 180)
    )
    return TechNewsNotes(
        why_it_matters=why,
        learning_value=learning,
        action_items=[
            "Read the source article.",
            "Identify the practical change and who is affected.",
            "Decide whether it deserves a follow-up experiment or note.",
        ],
    )


def _github_release_notes(item: SignalItem) -> TechNewsNotes:
    title = clean_note_text(item.title) or "this release"
    excerpt = clean_note_text(item.raw_content)
    if not excerpt:
        excerpt = f"{title} ships a new project release."
        why = _public_sentence(excerpt, "")
    else:
        why = _source_public_sentence(excerpt, item.title)
    learning = "Use it to identify the practical release changes: " + _with_terminal_punctuation(
        _truncate(_strip_title_prefix(excerpt, item.title), 180)
    )
    return TechNewsNotes(
        why_it_matters=why,
        learning_value=learning,
        action_items=[
            "Read the release notes.",
            "Identify the changed APIs, performance behavior, or migration steps.",
            "Decide whether the release affects a project or dependency you use.",
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
    trimmed = value[: limit - 3].rstrip(" \t\r\n.,;:")
    if trimmed and len(trimmed) < len(value) and not value[len(trimmed)].isspace():
        trimmed = trimmed.rsplit(" ", 1)[0].rstrip(" \t\r\n.,;:")
    return trimmed + "..."


def _with_terminal_punctuation(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if text.endswith(("...", ".", "!", "?")):
        return text
    return f"{text}."


def _source_public_sentence(value: str, title: str) -> str:
    text = _public_sentence(_strip_title_prefix(value, title), "")
    if not text or _has_dangling_end(text):
        clean_title = clean_note_text(title) or "this update"
        return f"The source points to \"{clean_title}\" as a practical AI tooling or research update worth checking."
    return text


def _public_sentence(value: str, title: str) -> str:
    text = _strip_title_prefix(value, title)
    if len(text) > 220:
        complete: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            sentence = sentence.strip()
            if not sentence.endswith((".", "!", "?")):
                break
            candidate = " ".join([*complete, sentence])
            if len(candidate) > 220:
                break
            complete.append(sentence)
        text = " ".join(complete) if complete else _truncate(text, 220)
    text = text.strip(" \t\r\n#-:;,")
    while _has_dangling_end(text) and " " in text:
        text = text.rsplit(" ", 1)[0].strip(" \t\r\n#-:;,")
    if not text:
        return ""
    return text if text[-1] in ".!?" else f"{text}."


def _strip_title_prefix(value: str, title: str) -> str:
    text = clean_note_text(value)
    text = re.sub(r"^\s*release\s*[:\-]\s*", "", text, flags=re.IGNORECASE)
    title_text = clean_note_text(title)
    if title_text:
        text = re.sub(
            rf"^\s*{re.escape(title_text)}(?:\s*[:\-]\s*|\s+)",
            "",
            text,
            flags=re.IGNORECASE,
        )
    return text.strip(" \t\r\n#-:;,.")


def _has_dangling_end(value: str) -> bool:
    stripped = value.strip()
    if _has_unbalanced_delimiters(stripped) or _has_short_trailing_sentence(stripped):
        return True
    lowered = stripped.lower().rstrip(".")
    if not lowered:
        return True
    endings = (
        " who need",
        " they enable",
        " a vs",
        " b vs",
        " and",
        " or",
        " of",
        " to",
        " for",
        " from",
        " by",
        " as",
        " in",
        " on",
        " with",
        " that",
        " which",
        " because",
        " similar i",
    )
    if lowered.endswith(endings):
        return True
    words = re.findall(r"[a-z0-9]+", lowered)
    return bool(words and len(words[-1]) <= 2 and words[-1] not in {"ai", "ml", "rl", "ui", "ux", "os", "go", "js", "m3"})


def _has_unbalanced_delimiters(text: str) -> bool:
    return (
        text.count("(") > text.count(")")
        or text.count("[") > text.count("]")
        or text.count("{") > text.count("}")
    )


def _has_short_trailing_sentence(text: str) -> bool:
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]
    if len(sentences) < 2:
        return False
    words = re.findall(r"[A-Za-z0-9]+", sentences[-1])
    all_words = re.findall(r"[A-Za-z0-9]+", text)
    return 0 < len(words) <= 2 and len(all_words) > 6


def _has_incomplete_source_sentence(value: str) -> bool:
    """Reject substantial source excerpts that end without a sentence boundary."""
    text = html.unescape(value or "").strip()
    words = re.findall(r"[A-Za-z0-9]+", text)
    return len(words) >= 5 and not text.endswith((".", "!", "?"))
