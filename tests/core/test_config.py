from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aurora.config import AuroraConfig
from aurora.storage.config_loader import expand_env_vars, load_config


def test_aurora_config_defaults_match_pr1_contract() -> None:
    config = AuroraConfig()

    assert config.run.enabled_modes == ["tech_news", "scholar", "repo_learning"]
    assert config.run.timezone == "Asia/Shanghai"
    assert config.run.quality_tier == "balanced"
    assert config.ai.provider == "deepseek"
    assert config.ai.model == "deepseek-chat"
    assert config.ai.api_key_env == "DEEPSEEK_API_KEY"
    assert config.ai.max_requests_per_run is None
    assert config.ai.max_tokens_per_run is None
    assert config.ai.request_timeout_sec == 25.0
    assert config.ai.fail_open_on_budget_exceeded is True
    assert config.pipeline.scoring.score_threshold == 7.0
    assert config.delivery.filesystem.enabled is True
    assert config.delivery.github_pages.enabled is True
    assert config.modes.tech_news.enabled is True
    assert config.modes.tech_news.sources.hackernews.fetch_top_stories == 60
    assert config.modes.tech_news.sources.hackernews.min_score == 100
    assert config.modes.tech_news.sources.rss == []
    assert config.modes.tech_news.sources.curated_rss_groups == []
    assert config.modes.tech_news.sources.reddit.enabled is False
    assert config.modes.tech_news.sources.github_releases.enabled is False
    assert config.modes.tech_news.llm_analysis_top_n == 12
    assert config.modes.tech_news.scoring.engagement_weight == 0.40
    assert config.modes.tech_news.scoring.recency_weight == 0.35
    assert config.modes.scholar.enabled is True
    assert config.modes.scholar.fields == ["ml"]
    assert config.modes.scholar.max_candidates == 200
    assert config.modes.scholar.final_item_count == 10
    assert config.modes.scholar.llm_analysis_top_n == 12
    assert config.modes.scholar.score_threshold == 7.0
    assert config.modes.scholar.fallback_cache_enabled is True
    assert config.modes.scholar.fallback_cache_ttl_hours == 168
    assert config.modes.scholar.sources.arxiv.enabled is True
    assert config.modes.scholar.sources.openreview.enabled is True
    assert config.modes.scholar.sources.semantic_scholar.enabled is True
    assert config.modes.scholar.sources.semantic_scholar.api_key_env == "SEMANTIC_SCHOLAR_API_KEY"
    assert config.modes.scholar.sources.semantic_scholar.cache_ttl_hours == 168
    assert config.modes.scholar.sources.semantic_scholar.max_requests_per_run == 40
    assert config.modes.scholar.sources.semantic_scholar.rate_limit_interval_sec == 1.25
    assert config.modes.scholar.sources.semantic_scholar.max_retries == 3
    assert config.modes.scholar.sources.semantic_scholar.retry_delay_sec == 1.0
    assert config.modes.repo_learning.enabled is True
    assert config.modes.repo_learning.item_type == "repo"
    assert config.modes.repo_learning.interests == ["agents", "mcp", "workflow-automation"]
    assert config.modes.repo_learning.sources.github_search.enabled is True
    assert config.modes.repo_learning.sources.github_search.token_env == "GH_SEARCH_TOKEN"
    assert config.modes.repo_learning.sources.github_search.domains == [
        "ai-agents",
        "mcp-ecosystem",
        "workflow-automation",
    ]
    assert config.modes.repo_learning.sources.github_search.min_stars == 500
    assert config.modes.repo_learning.sources.github_search.recent_years == 2
    assert config.modes.repo_learning.sources.github_search.active_within_days == 180
    assert config.modes.repo_learning.sources.github_search.per_page == 20
    assert config.modes.repo_learning.sources.github_search.request_timeout_sec == 15.0
    assert config.modes.repo_learning.ranking.final_item_count == 6
    assert config.modes.repo_learning.ranking.enrich_top_n == 12
    assert config.modes.repo_learning.ranking.llm_analysis_top_n == 12
    assert config.modes.repo_learning.ranking.history_lookback_days == 14
    assert config.modes.unified_digest.enabled is True
    assert config.modes.unified_digest.include_modes == ["tech_news", "scholar", "repo_learning"]
    assert config.modes.unified_digest.max_items_per_type == 8
    assert config.modes.unified_digest.max_total_items == 20
    assert config.modes.unified_digest.section_order == ["news", "repo", "paper"]
    assert config.modes.unified_digest.section_limits == {"news": 5, "repo": 3, "paper": 3}


@pytest.mark.parametrize(
    "payload",
    [
        {"run": {"time_window_hours": 0}},
        {"run": {"max_items": 0}},
        {"run": {"enabled_modes": []}},
        {"pipeline": {"scoring": {"score_threshold": 10.1}}},
        {"ai": {"analysis_concurrency": 0}},
        {"ai": {"max_requests_per_run": -1}},
        {"ai": {"max_tokens_per_run": -1}},
        {"delivery": {"email": {"smtp_port": 70000}}},
        {"modes": {"tech_news": {"item_type": "paper"}}},
        {"modes": {"tech_news": {"llm_analysis_top_n": -1}}},
        {"modes": {"tech_news": {"sources": {"hackernews": {"fetch_top_stories": 0}}}}},
        {"modes": {"tech_news": {"sources": {"curated_rss_groups": ["unknown"]}}}},
        {"modes": {"tech_news": {"sources": {"reddit": {"subreddits": []}}}}},
        {"modes": {"tech_news": {"sources": {"github_releases": {"repositories": ["bad slug"]}}}}},
        {"modes": {"scholar": {"item_type": "news"}}},
        {"modes": {"scholar": {"fields": ["unknown-field"]}}},
        {"modes": {"scholar": {"max_candidates": 0}}},
        {"modes": {"scholar": {"llm_analysis_top_n": -1}}},
        {"modes": {"scholar": {"sources": {"openreview": {"venue_ids": []}}}}},
        {"modes": {"scholar": {"sources": {"semantic_scholar": {"api_key_env": ""}}}}},
        {"modes": {"scholar": {"sources": {"semantic_scholar": {"cache_ttl_hours": 0}}}}},
        {"modes": {"scholar": {"fallback_cache_ttl_hours": 0}}},
        {"modes": {"repo_learning": {"item_type": "paper"}}},
        {"modes": {"repo_learning": {"interests": ["unknown-interest"]}}},
        {"modes": {"repo_learning": {"sources": {"github_search": {"domains": []}}}}},
        {"modes": {"repo_learning": {"sources": {"github_search": {"per_page": 0}}}}},
        {"modes": {"repo_learning": {"ranking": {"final_item_count": 0}}}},
        {"modes": {"repo_learning": {"ranking": {"llm_analysis_top_n": -1}}}},
        {"modes": {"unified_digest": {"include_modes": []}}},
        {"modes": {"unified_digest": {"include_modes": ["tech_news", "tech_news"]}}},
        {"modes": {"unified_digest": {"section_order": ["paper", "repo"]}}},
        {"modes": {"unified_digest": {"section_limits": {"news": 0}}}},
        {"modes": {"unified_digest": {"section_limits": {"unknown": 3}}}},
    ],
)
def test_aurora_config_rejects_invalid_ranges(payload: dict) -> None:
    with pytest.raises(ValidationError):
        AuroraConfig.model_validate(payload)


def test_expand_env_vars_recurses_and_preserves_missing_or_literal_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AURORA_ROOT", "/tmp/aurora")

    expanded = expand_env_vars(
        {
            "path": "${AURORA_ROOT}/state.json",
            "missing": "${MISSING_AURORA_VAR}/state.json",
            "list": ["${AURORA_ROOT}", {"nested": "${AURORA_ROOT}/cache"}],
            "api_key_env": "DEEPSEEK_API_KEY",
        }
    )

    assert expanded["path"] == "/tmp/aurora/state.json"
    assert expanded["missing"] == "${MISSING_AURORA_VAR}/state.json"
    assert expanded["list"] == ["/tmp/aurora", {"nested": "/tmp/aurora/cache"}]
    assert expanded["api_key_env"] == "DEEPSEEK_API_KEY"


def test_load_config_reads_json_expands_env_and_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AURORA_STATE_DIR", str(tmp_path))
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """
        {
          "run": {
            "state_path": "${AURORA_STATE_DIR}/state.json"
          },
          "ai": {
            "api_key_env": "DEEPSEEK_API_KEY"
          }
        }
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.run.state_path == tmp_path / "state.json"
    assert config.ai.api_key_env == "DEEPSEEK_API_KEY"


def test_example_config_validates() -> None:
    config = load_config(Path("data/config.example.json"))

    assert config.run.enabled_modes == ["unified_digest"]
    assert config.modes.repo_learning.interests == ["agents", "mcp", "workflow-automation"]
    assert config.modes.scholar.fields == ["ml", "agents"]
    assert config.modes.scholar.sources.semantic_scholar.api_key_env == "SEMANTIC_SCHOLAR_API_KEY"
    assert config.ai.max_tokens == 900
    assert config.ai.max_requests_per_run == 24
    assert config.ai.max_tokens_per_run == 150000
    assert config.modes.tech_news.llm_analysis_top_n == 8
    assert config.modes.scholar.llm_analysis_top_n == 10
    assert config.modes.repo_learning.ranking.llm_analysis_top_n == 8
    assert {source.name for source in config.modes.tech_news.sources.rss} >= {
        "Simon Willison",
        "OpenAI News",
        "Google DeepMind Blog",
    }


def test_local_llm_example_config_uses_ollama_without_cloud_access() -> None:
    config = load_config(Path("data/local-llm.config.example.json"))

    assert config.ai.provider == "ollama"
    assert config.ai.model == "qwen2.5:3b"
    assert config.ai.local_only is True
    assert config.ai.task_models == {"repair": "qwen2.5:3b"}


def test_actions_config_enables_email_and_content_window() -> None:
    config = load_config(Path("data/actions.config.json"))

    assert config.run.topic == "agents"
    assert config.run.enabled_modes == ["unified_digest"]
    assert config.run.time_window_hours == 168
    assert config.delivery.email.enabled is True
    assert config.delivery.email.smtp_username_env == "SMTP_USERNAME"
    assert config.delivery.email.password_env == "EMAIL_PASSWORD"
    assert config.delivery.email.recipients_env == "AURORA_EMAIL_RECIPIENTS"
    assert config.ai.max_tokens == 650
    assert config.ai.max_requests_per_run == 18
    assert config.ai.max_network_attempts_per_run == 26
    assert config.ai.max_tokens_per_run == 240000
    assert config.ai.request_timeout_sec == 20.0
    assert config.ai.fail_open_on_budget_exceeded is True
    assert config.ai.analysis_concurrency == 2
    assert config.ai.transient_retry_attempts == 2
    assert config.ai.retry_backoff_sec == 1.0
    assert config.release_gate.enabled is True
    assert config.release_gate.required_clean_runs == 7
    assert config.modes.tech_news.filters.require_include_keyword is True
    assert "agent" in config.modes.tech_news.filters.include_keywords
    assert "llm" in config.modes.tech_news.filters.include_keywords
    assert "developer" not in config.modes.tech_news.filters.include_keywords
    assert "security" not in config.modes.tech_news.filters.include_keywords
    assert "python" not in config.modes.tech_news.filters.include_keywords
    assert config.modes.tech_news.llm_analysis_top_n == 2
    assert config.modes.scholar.llm_analysis_top_n == 2
    assert config.modes.repo_learning.ranking.llm_analysis_top_n == 2
    assert config.modes.tech_news.sources.hackernews.fetch_top_stories == 100
    assert config.modes.tech_news.sources.hackernews.min_score == 30
    assert config.modes.tech_news.sources.reddit.enabled is False
    assert config.modes.tech_news.sources.reddit.subreddits == ["MachineLearning", "LocalLLaMA"]
    assert config.modes.tech_news.sources.github_releases.enabled is True
    assert "openai/openai-python" in config.modes.tech_news.sources.github_releases.repositories
    assert {source.name for source in config.modes.tech_news.sources.rss} >= {
        "Simon Willison",
        "OpenAI News",
        "Google DeepMind Blog",
    }
    assert config.modes.scholar.score_threshold == 5.5
    assert config.modes.scholar.sources.semantic_scholar.max_requests_per_run == 20
    assert config.modes.scholar.sources.semantic_scholar.rate_limit_interval_sec == 2.0
    assert config.modes.scholar.sources.semantic_scholar.max_retries == 4
    assert config.modes.scholar.sources.semantic_scholar.retry_delay_sec == 5.0
    assert config.modes.repo_learning.sources.github_search.min_stars == 100
    assert config.modes.repo_learning.sources.github_search.request_timeout_sec == 12.0
    assert config.modes.repo_learning.ranking.enrich_top_n == 6
    assert config.modes.unified_digest.section_order == ["news", "repo", "paper"]
    assert config.modes.unified_digest.section_limits == {"news": 5, "repo": 3, "paper": 3}
    assert config.modes.unified_digest.minimum_section_items == {"news": 5, "repo": 3, "paper": 3}
    assert config.modes.unified_digest.max_total_items == 11


def test_public_topic_presets_validate_for_config_users() -> None:
    config = AuroraConfig(
        run={"topic": "robots"},
        modes={
            "repo_learning": {"interests": ["llm", "agents", "robots"]},
            "scholar": {"fields": ["llm", "agents", "robots"]},
        },
    )

    assert config.run.topic == "robots"
    assert config.modes.repo_learning.interests == ["llm", "agents", "robots"]
    assert config.modes.scholar.fields == ["llm", "agents", "robots"]


def test_unified_digest_rejects_minimum_section_count_above_limit() -> None:
    with pytest.raises(ValidationError, match="minimum_section_items"):
        AuroraConfig(
            modes={
                "unified_digest": {
                    "section_limits": {"news": 1, "repo": 1, "paper": 1},
                    "minimum_section_items": {"news": 2, "repo": 1, "paper": 1},
                }
            }
        )
