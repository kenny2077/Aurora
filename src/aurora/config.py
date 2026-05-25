"""Configuration contracts for Aurora PR 1."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ModeName = Literal["tech_news", "scholar", "repo_learning", "unified_digest"]


class RunConfig(BaseModel):
    """Top-level run controls that are independent of any mode."""

    model_config = ConfigDict(extra="forbid")

    enabled_modes: list[ModeName] = Field(
        default_factory=lambda: ["tech_news", "scholar", "repo_learning"]
    )
    timezone: str = "Asia/Shanghai"
    time_window_hours: int = Field(default=24, ge=1)
    max_items: int = Field(default=50, ge=1)
    dry_run: bool = False
    state_path: Path = Path("data/aurora_state.json")
    cache_dir: Path = Path("data/cache")
    output_dir: Path = Path("data/runs")

    @field_validator("enabled_modes")
    @classmethod
    def validate_enabled_modes(cls, value: list[ModeName]) -> list[ModeName]:
        if not value:
            raise ValueError("at least one mode must be enabled")
        if len(value) != len(set(value)):
            raise ValueError("enabled_modes must not contain duplicates")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("timezone must be a non-empty string")
        return value.strip()


class DedupConfig(BaseModel):
    """Shared deduplication knobs."""

    model_config = ConfigDict(extra="forbid")

    title_similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    url_canonicalization: bool = True
    cross_mode_dedup: bool = True


class FinalScoreWeights(BaseModel):
    """Weights used by the shared final-score combiner."""

    model_config = ConfigDict(extra="forbid")

    deterministic: float = Field(default=0.45, ge=0.0, le=1.0)
    llm: float = Field(default=0.55, ge=0.0, le=1.0)


class ScoringConfig(BaseModel):
    """Shared scoring controls."""

    model_config = ConfigDict(extra="forbid")

    default_final_weights: FinalScoreWeights = Field(default_factory=FinalScoreWeights)
    score_threshold: float = Field(default=7.0, ge=0.0, le=10.0)


class EnrichmentConfig(BaseModel):
    """Shared enrichment controls."""

    model_config = ConfigDict(extra="forbid")

    top_n: int = Field(default=20, ge=0)
    allow_network_enrichment: bool = True


class PipelineConfig(BaseModel):
    """Configuration for shared pipeline behavior."""

    model_config = ConfigDict(extra="forbid")

    dedup: DedupConfig = Field(default_factory=DedupConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)


class AIConfig(BaseModel):
    """AI configuration only; no AI client implementation is part of PR 1."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "deepseek"
    model: str = "deepseek-chat"
    base_url: str | None = None
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)
    analysis_concurrency: int = Field(default=2, ge=1)
    enrichment_concurrency: int = Field(default=2, ge=1)
    throttle_sec: float = Field(default=0.0, ge=0.0)
    languages: list[str] = Field(default_factory=lambda: ["en"])

    @field_validator("provider", "model", "api_key_env")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value.strip()

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, value: list[str]) -> list[str]:
        cleaned = [language.strip() for language in value if language.strip()]
        if not cleaned:
            raise ValueError("at least one language is required")
        return cleaned


class FilesystemDeliveryConfig(BaseModel):
    """Filesystem delivery settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    reports_dir: Path = Path("reports")
    site_dir: Path = Path("site")


class EmailDeliveryConfig(BaseModel):
    """SMTP/email delivery settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_username_env: str = "SMTP_USERNAME"
    password_env: str = "EMAIL_PASSWORD"
    sender_name: str = "Aurora"
    recipients_env: str = "AURORA_EMAIL_RECIPIENTS"
    subscribers_path: Path = Path("data/subscribers.json")


class WebhookDeliveryConfig(BaseModel):
    """Webhook delivery settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    targets: list[dict[str, Any]] = Field(default_factory=list)


class GitHubPagesDeliveryConfig(BaseModel):
    """GitHub Pages delivery settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    publish_dir: Path = Path("site")


class DeliveryConfig(BaseModel):
    """Delivery channel configuration."""

    model_config = ConfigDict(extra="forbid")

    filesystem: FilesystemDeliveryConfig = Field(default_factory=FilesystemDeliveryConfig)
    email: EmailDeliveryConfig = Field(default_factory=EmailDeliveryConfig)
    webhook: WebhookDeliveryConfig = Field(default_factory=WebhookDeliveryConfig)
    github_pages: GitHubPagesDeliveryConfig = Field(default_factory=GitHubPagesDeliveryConfig)


class AuroraConfig(BaseModel):
    """Root Aurora configuration for PR 1."""

    model_config = ConfigDict(extra="forbid")

    run: RunConfig = Field(default_factory=RunConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)

