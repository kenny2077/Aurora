"""Research-field expansion helpers for scholar mode."""

from __future__ import annotations

from aurora.config import ScholarModeConfig
from aurora.interests import SCHOLAR_FIELD_PRESETS, unique_text


def expanded_arxiv_categories(config: ScholarModeConfig) -> list[str]:
    """Return arXiv categories from selected fields plus explicit config."""
    return unique_text(
        [
            *config.sources.arxiv.categories,
            *_preset_values(config.fields, "categories"),
        ]
    )


def expanded_keyword_allowlist(config: ScholarModeConfig) -> list[str]:
    """Return keywords from selected fields plus explicit allowlist."""
    return unique_text([*config.keyword_allowlist, *_preset_values(config.fields, "keywords")])


def expanded_venue_allowlist(config: ScholarModeConfig) -> list[str]:
    """Return venues from selected fields plus explicit venue allowlist."""
    return unique_text([*config.venue_allowlist, *_preset_values(config.fields, "venues")])


def field_tags(config: ScholarModeConfig) -> list[str]:
    """Return selected field labels suitable for SignalItem tags."""
    return list(config.fields)


def _preset_values(fields: list[str], key: str) -> list[str]:
    values: list[str] = []
    for field in fields:
        values.extend(str(value) for value in SCHOLAR_FIELD_PRESETS[field][key])
    return values
