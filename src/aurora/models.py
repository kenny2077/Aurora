"""Core data contracts for Aurora."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


SignalType = Literal["news", "paper", "repo"]


class SignalItem(BaseModel):
    """Normalized item contract shared by all Aurora modes."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: SignalType
    title: str
    url: HttpUrl
    source: str
    published_at: datetime | None = None
    updated_at: datetime | None = None
    raw_content: str = ""
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    deterministic_score: float | None = Field(default=None, ge=0.0, le=10.0)
    llm_score: float | None = Field(default=None, ge=0.0, le=10.0)
    final_score: float | None = Field(default=None, ge=0.0, le=10.0)
    tags: list[str] = Field(default_factory=list)
    why_it_matters: str = ""
    learning_value: str = ""
    action_items: list[str] = Field(default_factory=list)

    @field_validator("id", "title", "source")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """Trim and reject blank required string fields."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value.strip()

    @model_validator(mode="after")
    def require_item_timestamp(self) -> "SignalItem":
        """Require at least one source time for scoring and digests."""
        if self.published_at is None and self.updated_at is None:
            raise ValueError("published_at or updated_at is required")
        return self


class SourceStatus(BaseModel):
    """Source-level diagnostics emitted by fetch and enrichment stages."""

    model_config = ConfigDict(extra="forbid")

    source: str
    stage: str
    ok: bool = True
    rate_limited: bool = False
    fetched_count: int | None = Field(default=None, ge=0)
    normalized_count: int | None = Field(default=None, ge=0)
    enriched_count: int | None = Field(default=None, ge=0)
    cached_count: int | None = Field(default=None, ge=0)
    skipped_count: int | None = Field(default=None, ge=0)
    failed_count: int | None = Field(default=None, ge=0)
    error: str | None = None

    @field_validator("source", "stage")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value.strip()


class ScoreResult(BaseModel):
    """Scoring output for a single normalized item."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    deterministic_score: float | None = Field(default=None, ge=0.0, le=10.0)
    llm_score: float | None = Field(default=None, ge=0.0, le=10.0)
    final_score: float | None = Field(default=None, ge=0.0, le=10.0)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    reason: str = ""
    tags: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)

    @field_validator("item_id")
    @classmethod
    def validate_item_id(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value.strip()

    @field_validator("score_breakdown")
    @classmethod
    def validate_score_breakdown(cls, value: dict[str, float]) -> dict[str, float]:
        for key, score in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("score_breakdown keys must be non-empty strings")
            if score < 0.0 or score > 10.0:
                raise ValueError("score_breakdown values must be between 0 and 10")
        return {key.strip(): score for key, score in value.items()}


class RenderedDigest(BaseModel):
    """Rendered digest payload passed to delivery stages."""

    model_config = ConfigDict(extra="forbid")

    mode: str
    title: str
    markdown: str
    html: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("mode", "title")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value.strip()


class DeliveryResult(BaseModel):
    """Delivery status for one output channel."""

    model_config = ConfigDict(extra="forbid")

    channel: str
    ok: bool = True
    destination: str | None = None
    message_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value.strip()

