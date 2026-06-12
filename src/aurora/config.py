"""Configuration contracts for Aurora PR 1."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from aurora.interests import REPO_INTEREST_PRESETS, SCHOLAR_FIELD_PRESETS, clean_preset_names


ModeName = Literal["tech_news", "scholar", "repo_learning", "unified_digest"]
SignalSection = Literal["news", "paper", "repo"]


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
    publish_dir: Path = Path("web/src/content/posts")


class DeliveryConfig(BaseModel):
    """Delivery channel configuration."""

    model_config = ConfigDict(extra="forbid")

    filesystem: FilesystemDeliveryConfig = Field(default_factory=FilesystemDeliveryConfig)
    email: EmailDeliveryConfig = Field(default_factory=EmailDeliveryConfig)
    webhook: WebhookDeliveryConfig = Field(default_factory=WebhookDeliveryConfig)
    github_pages: GitHubPagesDeliveryConfig = Field(default_factory=GitHubPagesDeliveryConfig)


class HackerNewsSourceConfig(BaseModel):
    """Hacker News source configuration for tech_news mode."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    fetch_top_stories: int = Field(default=60, ge=1)
    min_score: int = Field(default=100, ge=0)
    top_comments_limit: int = Field(default=5, ge=0)


class RSSSourceConfig(BaseModel):
    """RSS/Atom source configuration for tech_news mode."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: HttpUrl
    enabled: bool = True
    category: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("name must be a non-empty string")
        return value.strip()


class TechNewsSourcesConfig(BaseModel):
    """Source configuration for tech_news mode."""

    model_config = ConfigDict(extra="forbid")

    hackernews: HackerNewsSourceConfig = Field(default_factory=HackerNewsSourceConfig)
    rss: list[RSSSourceConfig] = Field(default_factory=list)


class TechNewsFiltersConfig(BaseModel):
    """Filtering controls for tech_news mode."""

    model_config = ConfigDict(extra="forbid")

    min_source_score: float = Field(default=0.0, ge=0.0)
    include_keywords: list[str] = Field(
        default_factory=lambda: [
            "ai",
            "ml",
            "llm",
            "agent",
            "open source",
            "developer",
            "security",
            "python",
        ]
    )
    exclude_keywords: list[str] = Field(default_factory=list)

    @field_validator("include_keywords", "exclude_keywords")
    @classmethod
    def clean_keywords(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            keyword = str(raw).strip().lower()
            if not keyword or keyword in seen:
                continue
            cleaned.append(keyword)
            seen.add(keyword)
        return cleaned


class TechNewsScoringConfig(BaseModel):
    """Deterministic scoring weights for tech_news mode."""

    model_config = ConfigDict(extra="forbid")

    source_authority_weight: float = Field(default=0.15, ge=0.0)
    engagement_weight: float = Field(default=0.40, ge=0.0)
    recency_weight: float = Field(default=0.35, ge=0.0)
    topic_relevance_weight: float = Field(default=0.10, ge=0.0)


class TechNewsModeConfig(BaseModel):
    """Configuration for Aurora's tech_news MVP mode."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    item_type: str = "news"
    llm_analysis_top_n: int = Field(default=12, ge=0)
    sources: TechNewsSourcesConfig = Field(default_factory=TechNewsSourcesConfig)
    filters: TechNewsFiltersConfig = Field(default_factory=TechNewsFiltersConfig)
    scoring: TechNewsScoringConfig = Field(default_factory=TechNewsScoringConfig)

    @field_validator("item_type")
    @classmethod
    def validate_item_type(cls, value: str) -> str:
        if value != "news":
            raise ValueError("tech_news item_type must be 'news'")
        return value


class ArxivSourceConfig(BaseModel):
    """arXiv source configuration for scholar mode."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    categories: list[str] = Field(
        default_factory=lambda: ["cs.LG", "cs.AI", "stat.ML", "cs.CL", "cs.RO", "cs.NE"]
    )
    max_results: int | None = Field(default=None, ge=1)


class OpenReviewSourceConfig(BaseModel):
    """OpenReview source configuration for scholar mode."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    venue_ids: list[str] = Field(
        default_factory=lambda: [
            "ICLR.cc/2026/Conference",
            "ICLR.cc/2025/Conference",
            "ICML.cc/2026/Conference",
            "ICML.cc/2025/Conference",
            "NeurIPS.cc/2025/Conference",
            "NeurIPS.cc/2026/Conference",
        ]
    )

    @field_validator("venue_ids")
    @classmethod
    def validate_venue_ids(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if not cleaned:
            raise ValueError("at least one OpenReview venue id is required")
        return cleaned


class SemanticScholarSourceConfig(BaseModel):
    """Optional Semantic Scholar enrichment configuration for scholar mode."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    api_key_env: str = "SEMANTIC_SCHOLAR_API_KEY"
    cache_ttl_hours: int = Field(default=168, ge=1)
    max_requests_per_run: int = Field(default=40, ge=0)
    rate_limit_interval_sec: float = Field(default=1.25, ge=0.0)
    max_retries: int = Field(default=3, ge=1)
    retry_delay_sec: float = Field(default=1.0, ge=0.0)

    @field_validator("api_key_env")
    @classmethod
    def validate_api_key_env(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("api_key_env must be a non-empty string")
        return value.strip()


class ScholarSourcesConfig(BaseModel):
    """Source configuration for scholar mode."""

    model_config = ConfigDict(extra="forbid")

    arxiv: ArxivSourceConfig = Field(default_factory=ArxivSourceConfig)
    openreview: OpenReviewSourceConfig = Field(default_factory=OpenReviewSourceConfig)
    semantic_scholar: SemanticScholarSourceConfig = Field(default_factory=SemanticScholarSourceConfig)


class ScholarModeConfig(BaseModel):
    """Configuration for Aurora's scholar MVP mode."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    item_type: str = "paper"
    max_candidates: int = Field(default=200, ge=1)
    final_item_count: int = Field(default=10, ge=1)
    llm_analysis_top_n: int = Field(default=12, ge=0)
    min_year: int = Field(default=2025, ge=1900, le=2100)
    max_year: int = Field(default=2026, ge=1900, le=2100)
    score_threshold: float = Field(default=7.0, ge=0.0, le=10.0)
    fallback_cache_enabled: bool = True
    fallback_cache_ttl_hours: int = Field(default=168, ge=1)
    fields: list[str] = Field(default_factory=lambda: ["ml"])
    venue_allowlist: list[str] = Field(
        default_factory=lambda: ["ICML", "NeurIPS", "ICLR", "AISTATS", "COLT", "UAI", "MLSys", "TMLR"]
    )
    keyword_allowlist: list[str] = Field(
        default_factory=lambda: [
            "representation learning",
            "self-supervised learning",
            "generative modeling",
            "diffusion models",
            "reinforcement learning",
            "interpretability",
            "large language models",
            "multimodal learning",
            "llm agents",
            "reasoning",
            "alignment",
            "retrieval augmented generation",
            "tool use",
            "efficient training",
            "inference optimization",
            "learning theory",
            "optimization",
        ]
    )
    keyword_blocklist: list[str] = Field(
        default_factory=lambda: [
            "medical case report",
            "pure computer vision application",
            "remote sensing only",
            "hardware-only",
            "non-ml survey",
        ]
    )
    sources: ScholarSourcesConfig = Field(default_factory=ScholarSourcesConfig)

    @field_validator("item_type")
    @classmethod
    def validate_item_type(cls, value: str) -> str:
        if value != "paper":
            raise ValueError("scholar item_type must be 'paper'")
        return value

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, values: list[str]) -> list[str]:
        return clean_preset_names(values, SCHOLAR_FIELD_PRESETS, label="research field")

    @field_validator("keyword_allowlist", "keyword_blocklist", "venue_allowlist")
    @classmethod
    def clean_text_list(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw).strip()
            key = value.lower()
            if not value or key in seen:
                continue
            cleaned.append(value)
            seen.add(key)
        return cleaned


class RepoLearningGitHubSearchConfig(BaseModel):
    """GitHub search source configuration for repo_learning mode."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    token_env: str = "GH_SEARCH_TOKEN"
    domains: list[str] = Field(
        default_factory=lambda: ["ai-agents", "mcp-ecosystem", "workflow-automation"]
    )
    min_stars: int = Field(default=500, ge=0)
    recent_years: int = Field(default=2, ge=0)
    active_within_days: int = Field(default=180, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)
    custom_keywords: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)

    @field_validator("token_env")
    @classmethod
    def validate_token_env(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("token_env must be a non-empty string")
        return value.strip()

    @field_validator("domains")
    @classmethod
    def clean_domains(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw).strip()
            key = value.lower()
            if not value or key in seen:
                continue
            cleaned.append(value)
            seen.add(key)
        if not cleaned:
            raise ValueError("at least one repo learning domain is required")
        return cleaned

    @field_validator("custom_keywords", "languages")
    @classmethod
    def clean_text_values(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw).strip()
            key = value.lower()
            if not value or key in seen:
                continue
            cleaned.append(value)
            seen.add(key)
        return cleaned


class RepoLearningFirecrawlConfig(BaseModel):
    """Placeholder Firecrawl config; no client implementation is part of PR 5."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    api_key_env: str = "FIRECRAWL_API_KEY"


class RepoLearningSourcesConfig(BaseModel):
    """Source configuration for repo_learning mode."""

    model_config = ConfigDict(extra="forbid")

    github_search: RepoLearningGitHubSearchConfig = Field(
        default_factory=RepoLearningGitHubSearchConfig
    )
    firecrawl: RepoLearningFirecrawlConfig = Field(default_factory=RepoLearningFirecrawlConfig)


class RepoLearningRankingConfig(BaseModel):
    """Ranking and state controls for repo_learning mode."""

    model_config = ConfigDict(extra="forbid")

    final_item_count: int = Field(default=6, ge=1)
    enrich_top_n: int = Field(default=12, ge=0)
    llm_analysis_top_n: int = Field(default=12, ge=0)
    history_lookback_days: int = Field(default=14, ge=1)


class RepoLearningModeConfig(BaseModel):
    """Configuration for Aurora's repo_learning MVP mode."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    item_type: str = "repo"
    interests: list[str] = Field(default_factory=lambda: ["agents", "mcp", "workflow-automation"])
    sources: RepoLearningSourcesConfig = Field(default_factory=RepoLearningSourcesConfig)
    ranking: RepoLearningRankingConfig = Field(default_factory=RepoLearningRankingConfig)

    @field_validator("item_type")
    @classmethod
    def validate_item_type(cls, value: str) -> str:
        if value != "repo":
            raise ValueError("repo_learning item_type must be 'repo'")
        return value

    @field_validator("interests")
    @classmethod
    def validate_interests(cls, values: list[str]) -> list[str]:
        return clean_preset_names(values, REPO_INTEREST_PRESETS, label="repo interest")


class UnifiedDigestModeConfig(BaseModel):
    """Configuration for Aurora's unified_digest mode."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    include_modes: list[Literal["tech_news", "scholar", "repo_learning"]] = Field(
        default_factory=lambda: ["tech_news", "scholar", "repo_learning"]
    )
    max_items_per_type: int = Field(default=8, ge=1)
    max_total_items: int = Field(default=20, ge=1)
    section_limits: dict[SignalSection, int] = Field(
        default_factory=lambda: {"news": 5, "repo": 3, "paper": 3}
    )
    cross_mode_clusters: bool = True
    section_order: list[SignalSection] = Field(default_factory=lambda: ["news", "repo", "paper"])

    @field_validator("include_modes")
    @classmethod
    def validate_include_modes(
        cls, values: list[Literal["tech_news", "scholar", "repo_learning"]]
    ) -> list[Literal["tech_news", "scholar", "repo_learning"]]:
        if not values:
            raise ValueError("at least one included mode is required")
        if len(values) != len(set(values)):
            raise ValueError("include_modes must not contain duplicates")
        return values

    @field_validator("section_order")
    @classmethod
    def validate_section_order(cls, values: list[SignalSection]) -> list[SignalSection]:
        if sorted(values) != ["news", "paper", "repo"]:
            raise ValueError("section_order must contain news, paper, and repo exactly once")
        return values

    @field_validator("section_limits")
    @classmethod
    def validate_section_limits(cls, values: dict[SignalSection, int]) -> dict[SignalSection, int]:
        for section, limit in values.items():
            if section not in {"news", "paper", "repo"}:
                raise ValueError("section_limits keys must be news, paper, or repo")
            if limit < 1:
                raise ValueError("section_limits values must be at least 1")
        return values


class ModesConfig(BaseModel):
    """Mode-specific configuration branches."""

    model_config = ConfigDict(extra="forbid")

    tech_news: TechNewsModeConfig = Field(default_factory=TechNewsModeConfig)
    scholar: ScholarModeConfig = Field(default_factory=ScholarModeConfig)
    repo_learning: RepoLearningModeConfig = Field(default_factory=RepoLearningModeConfig)
    unified_digest: UnifiedDigestModeConfig = Field(default_factory=UnifiedDigestModeConfig)


class AuroraConfig(BaseModel):
    """Root Aurora configuration."""

    model_config = ConfigDict(extra="forbid")

    run: RunConfig = Field(default_factory=RunConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    modes: ModesConfig = Field(default_factory=ModesConfig)
