from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem, SourceStatus


def _timestamp() -> datetime:
    return datetime(2026, 5, 25, tzinfo=timezone.utc)


@pytest.mark.parametrize("item_type", ["news", "paper", "repo"])
def test_signal_item_accepts_supported_types(item_type: str) -> None:
    item = SignalItem(
        id=f"{item_type}:1",
        type=item_type,
        title="  Useful Signal  ",
        url="https://example.com/signal",
        source="  example  ",
        published_at=_timestamp(),
        deterministic_score=7.5,
        llm_score=8.0,
        final_score=7.8,
    )

    assert item.id == f"{item_type}:1"
    assert item.title == "Useful Signal"
    assert item.source == "example"


def test_signal_item_requires_a_timestamp() -> None:
    with pytest.raises(ValidationError):
        SignalItem(
            id="news:1",
            type="news",
            title="Missing Time",
            url="https://example.com/missing-time",
            source="example",
        )


@pytest.mark.parametrize("field", ["id", "title", "source"])
def test_signal_item_rejects_blank_required_text(field: str) -> None:
    payload = {
        "id": "news:1",
        "type": "news",
        "title": "Signal",
        "url": "https://example.com/signal",
        "source": "example",
        "published_at": _timestamp(),
    }
    payload[field] = "  "

    with pytest.raises(ValidationError):
        SignalItem(**payload)


def test_signal_item_rejects_invalid_type_and_scores() -> None:
    with pytest.raises(ValidationError):
        SignalItem(
            id="invalid:1",
            type="video",
            title="Invalid",
            url="https://example.com/invalid",
            source="example",
            published_at=_timestamp(),
        )

    with pytest.raises(ValidationError):
        SignalItem(
            id="news:2",
            type="news",
            title="Bad Score",
            url="https://example.com/bad-score",
            source="example",
            published_at=_timestamp(),
            final_score=10.1,
        )


def test_signal_item_default_collections_are_independent() -> None:
    first = SignalItem(
        id="news:1",
        type="news",
        title="First",
        url="https://example.com/first",
        source="example",
        published_at=_timestamp(),
    )
    second = SignalItem(
        id="news:2",
        type="news",
        title="Second",
        url="https://example.com/second",
        source="example",
        published_at=_timestamp(),
    )

    first.metadata["key"] = "value"
    first.tags.append("tag")
    first.action_items.append("act")

    assert second.metadata == {}
    assert second.tags == []
    assert second.action_items == []


def test_score_result_and_source_status_validate_bounds() -> None:
    result = ScoreResult(
        item_id="news:1",
        deterministic_score=7.0,
        llm_score=8.0,
        final_score=7.6,
        score_breakdown={"recency": 8.0},
    )
    status = SourceStatus(source="rss", stage="fetch", fetched_count=3, ok=True)

    assert result.score_breakdown == {"recency": 8.0}
    assert status.fetched_count == 3

    with pytest.raises(ValidationError):
        ScoreResult(item_id="news:1", score_breakdown={"bad": 11.0})

    with pytest.raises(ValidationError):
        SourceStatus(source="rss", stage="fetch", failed_count=-1)


def test_rendered_digest_and_delivery_result_validate_required_text() -> None:
    digest = RenderedDigest(mode="tech_news", title="Daily", markdown="# Daily")
    delivery = DeliveryResult(channel="filesystem", destination="reports/today.md")

    assert digest.markdown == "# Daily"
    assert delivery.ok is True

    with pytest.raises(ValidationError):
        RenderedDigest(mode="", title="Daily", markdown="# Daily")

    with pytest.raises(ValidationError):
        DeliveryResult(channel="")

