# Aurora Merge Design

Aurora is a clean integrated product, not a folder-level merge of the three
reference repositories. The implementation should port selected ideas and
small modules into one shared pipeline:

```text
fetch -> normalize -> deduplicate -> score -> enrich -> summarize -> render -> deliver
```

The four product modes are:

- `tech_news`: daily technology and AI news radar.
- `scholar`: machine-learning research paper radar.
- `repo_learning`: GitHub repository learning radar.
- `unified_digest`: combined cross-mode digest using the same normalized item
  model and pipeline runner.

## Canonical Item Model

All source adapters must normalize into `SignalItem`. Mode-specific fields go
inside `metadata`; pipeline stages must not depend on source-native models.

```python
class SignalItem(BaseModel):
    id: str
    type: Literal["news", "paper", "repo"]
    title: str
    url: HttpUrl
    source: str
    published_at: datetime | None = None
    updated_at: datetime | None = None
    raw_content: str = ""
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    deterministic_score: float | None = None
    llm_score: float | None = None
    final_score: float | None = None
    tags: list[str] = Field(default_factory=list)
    why_it_matters: str = ""
    learning_value: str = ""
    action_items: list[str] = Field(default_factory=list)
```

Validation rules:

- `id`, `type`, `title`, `url`, and `source` are required.
- At least one of `published_at` or `updated_at` is required.
- `metadata` carries mode-specific fields such as authors, DOI, citations,
  stars, forks, repo tree, venue, subreddit, feed name, or source categories.
- Score fields are normalized to `0.0..10.0`.
- `final_score` is computed by a shared score combiner after mode-specific
  deterministic and LLM scoring.

## 1. Final Repo Structure

Proposed structure after implementation:

```text
target/
  README.md
  LICENSE
  NOTICE
  pyproject.toml
  data/
    config.example.json
    presets/
      tech_news.json
      scholar.json
      repo_learning.json
  docs/
    merge_design.md
  src/
    aurora/
      __init__.py
      cli.py
      config.py
      models.py
      security.py
      telemetry.py
      pipeline/
        __init__.py
        context.py
        runner.py
        stages.py
        registry.py
        dedup.py
        scoring.py
      ai/
        __init__.py
        client.py
        json.py
        prompts.py
        tokens.py
        ranker.py
      storage/
        __init__.py
        config_loader.py
        state.py
        cache.py
        files.py
      render/
        __init__.py
        markdown.py
        html.py
        email_html.py
        pages.py
      delivery/
        __init__.py
        email.py
        webhook.py
        filesystem.py
        github_pages.py
      modes/
        __init__.py
        tech_news/
          __init__.py
          config.py
          sources.py
          normalize.py
          scoring.py
          prompts.py
          enrich.py
          summarize.py
          render.py
        scholar/
          __init__.py
          config.py
          sources.py
          normalize.py
          scoring.py
          prompts.py
          enrich.py
          summarize.py
          render.py
          clients/
            arxiv.py
            openreview.py
            semantic_scholar.py
            paperswithcode.py
            firecrawl.py
        repo_learning/
          __init__.py
          config.py
          github_client.py
          sources.py
          normalize.py
          scoring.py
          prompts.py
          enrich.py
          summarize.py
          render.py
        unified_digest/
          __init__.py
          config.py
          sources.py
          scoring.py
          summarize.py
          render.py
  tests/
    core/
    modes/
      tech_news/
      scholar/
      repo_learning/
      unified_digest/
```

Important boundaries:

- `aurora.pipeline` owns the shared stage order and run lifecycle.
- `aurora.models.SignalItem` is the only cross-mode item contract.
- `aurora.modes.*` owns source-specific fetchers, normalizers, scorers,
  prompts, enrichers, and mode-specific digest sections.
- `aurora.delivery` never knows whether an item came from news, papers, or
  repos; it receives rendered Markdown/HTML and delivery metadata.
- `unified_digest` is a mode, but not a separate pipeline. It registers multiple
  source groups and runs through the same runner.

## 2. Reusable Modules From Each Project

### Horizon-TechNews

Reuse with small adaptation:

- `src/ai/client.py`: provider-agnostic `AIClient` abstraction and factory.
  Port into `aurora.ai.client`.
- `src/ai/utils.py`: robust JSON parsing. Port into `aurora.ai.json`.
- `src/ai/tokens.py`: lightweight token accounting. Port into
  `aurora.ai.tokens`.
- `src/scrapers/base.py`: simple async fetch contract. Convert into
  `SourceAdapter` or `FetchAdapter` protocol.
- `src/storage/manager.py`: environment variable expansion and config loading
  pattern. Port the utility, not the whole storage manager.
- `src/ai/analyzer.py`: keep the concurrency, retry, and per-item failure
  isolation pattern. Parameterize prompts by mode.
- Selected scrapers from `src/scrapers/`: RSS, Hacker News, Reddit, Telegram,
  Twitter/Apify, OpenBB, and OSS Insight can be ported one by one into
  `modes.tech_news.sources`, each returning `SignalItem`.
- `src/ai/summarizer.py`: keep the principle of programmatic final digest
  rendering after LLM analysis. Rewrite templates for Aurora.

### Horizon-Scholar

Reuse with adaptation:

- `src/research/clients/arxiv.py`: arXiv Atom fetch and rate-limit behavior.
- `src/research/clients/openreview.py`: OpenReview venue fetching and status
  extraction.
- `src/research/clients/semantic_scholar.py`: enrichment client, cache TTL, API
  key rate-limit adjustment, title matching fallback.
- `src/research/clients/paperswithcode.py`: code repository enrichment.
- `src/research/clients/firecrawl.py`: optional expensive page/README
  enrichment.
- `src/research/scoring.py`: scholar deterministic signal design. Convert it
  from `ResearchPaperItem` mutation to a pure `SignalItem -> ScoreResult`.
- `src/research/prompts.py`: scholar scoring and learning-value prompt content.
  Keep mode-specific under `modes.scholar.prompts`.
- `src/research/analyzer.py`: batch analysis pattern with semaphore,
  throttling, retries, and per-paper fallback.
- `src/research/models.py`: field vocabulary for paper metadata. Do not keep a
  separate public item model; map fields into `SignalItem.metadata`.
- `ResearchSourceStatus`: useful run diagnostics for fetch/enrichment status.
  Generalize to `SourceStatus`.

### Horizon-Github / RepoRadar

Reuse with adaptation:

- `src/reporadar/github_client.py`: GitHub search, README, tree fetch, and
  owner/repo/ref validation. Port as `modes.repo_learning.github_client`.
- `src/reporadar/config.py`: preset-driven query construction and environment
  override pattern. Adapt into `RepoLearningConfig`.
- `src/reporadar/storage.py`: JSON state store concepts for
  recently-recommended suppression. Generalize keys as
  `{type}:{stable_id}`.
- `src/reporadar/scoring.py`: repo deterministic pre-score, score breakdown,
  weighted score, package-file extraction, and deterministic fallback learning
  plan. Convert to `SignalItem` input/output.
- `src/reporadar/llm.py`: strict JSON ranker contract and merge-with-fallback
  pattern. Use Aurora's shared AI client instead of keeping a DeepSeek-only
  client in core.
- `src/reporadar/models.py`: repo metadata vocabulary and recommendation
  output fields. Map to `SignalItem.metadata`, `learning_value`, and
  `action_items`.
- `src/reporadar/emailer.py`: recipient parsing and multipart email mechanics
  are reusable after removing RepoRadar branding.
- `src/reporadar/renderer.py`: report sections and learning-plan card ideas are
  useful, but should be rewritten against Aurora render primitives.

## 3. Modules To Rewrite

Rewrite these rather than porting them directly:

- All top-level entry points:
  - `Horizon-TechNews/src/main.py`
  - `Horizon-Scholar/src/main.py`
  - `Horizon-Github/src/reporadar/main.py`
  - Replacement: `aurora.cli` plus a mode registry.
- All monolithic orchestrators:
  - `Horizon-TechNews/src/orchestrator.py`
  - `Horizon-Scholar/src/research/orchestrator.py`
  - `Horizon-Github/src/reporadar/main.py::run`
  - Replacement: `aurora.pipeline.runner.PipelineRunner`.
- Public item models:
  - `ContentItem`, `ResearchPaperItem`, and `RepoCandidate` become internal
    source-native inputs or mapping references. `SignalItem` is the public
    item contract.
- Delivery HTML:
  - Horizon email/webhook templates and RepoRadar site HTML are branded and
    tightly coupled. Rewrite around shared Markdown, shared HTML layout, and
    mode-specific card fragments.
- Webhook layer:
  - Horizon webhook code is too large and mixed. Split into `WebhookClient`,
    platform formatters, and template rendering.
- Config envelope:
  - Replace flat `sources` plus optional `research` plus RepoRadar dataclass
    config with one Pydantic `AuroraConfig`.
- Prompt wiring:
  - Use one shared LLM caller, but separate prompt sets by mode.
- Deduplication:
  - Rewrite as strategy-driven dedup:
    - URL canonicalization for news.
    - DOI/arXiv/OpenReview/Semantic Scholar/title keys for papers.
    - `owner/name` and GitHub node ID for repos.
    - Cross-mode URL/title semantic clusters for `unified_digest`.
- Score combiner:
  - Implement one shared combiner with mode-specific calibration instead of
    keeping separate final-score formulas scattered across modes.

## 4. Modules To Avoid Copying

Do not copy:

- Whole `src/` folders from any reference repo.
- Generated reports, state, subscribers, summaries, Pages output, RSS feed XML,
  screenshots, or docs assets from reference repos.
- Reference `AGENTS.md` files, Docker files, or setup wizards.
- Existing `.github/workflows/*` files as-is. They are single-product workflows
  and will produce duplicated behavior.
- Horizon `src/orchestrator.py` and Scholar `src/research/orchestrator.py`.
  Use them as behavioral references only.
- Horizon `src/search.py`. It is tied to HN and Reddit search and should become
  source adapters instead.
- Horizon `src/services/webhook.py` as one file. Its platform logic should be
  decomposed if webhook delivery is implemented.
- Horizon GitHub scraper for repo learning. RepoRadar's GitHub client is the
  better base for `repo_learning`; keep Horizon GitHub event/release scraping
  only if needed for `tech_news`.
- RepoRadar `site/`, `reports/`, and generated CSS/HTML output.
- RepoRadar's DeepSeek-only client as the shared LLM layer. Keep the ranker
  contract, but use `aurora.ai.client`.
- Any config files containing real secrets or historical run state.

## 5. Unified Config Schema

Use one Pydantic-validated JSON config. JSON keeps migration close to the
reference repos and avoids adding a YAML dependency.

```json
{
  "version": "0.1",
  "run": {
    "enabled_modes": ["tech_news", "scholar", "repo_learning"],
    "timezone": "Asia/Shanghai",
    "time_window_hours": 24,
    "max_items": 50,
    "dry_run": false,
    "state_path": "data/aurora_state.json",
    "cache_dir": "data/cache",
    "output_dir": "data/runs"
  },
  "pipeline": {
    "dedup": {
      "title_similarity_threshold": 0.92,
      "url_canonicalization": true,
      "cross_mode_dedup": true
    },
    "scoring": {
      "default_final_weights": {
        "deterministic": 0.45,
        "llm": 0.55
      },
      "score_threshold": 7.0
    },
    "enrichment": {
      "top_n": 20,
      "allow_network_enrichment": true
    }
  },
  "ai": {
    "provider": "deepseek",
    "model": "deepseek-chat",
    "base_url": null,
    "api_key_env": "DEEPSEEK_API_KEY",
    "temperature": 0.2,
    "max_tokens": 4096,
    "analysis_concurrency": 2,
    "enrichment_concurrency": 2,
    "throttle_sec": 0.0,
    "languages": ["en"]
  },
  "delivery": {
    "filesystem": {
      "enabled": true,
      "reports_dir": "reports",
      "site_dir": "site"
    },
    "email": {
      "enabled": false,
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 465,
      "smtp_username_env": "SMTP_USERNAME",
      "password_env": "EMAIL_PASSWORD",
      "sender_name": "Aurora",
      "recipients_env": "AURORA_EMAIL_RECIPIENTS",
      "subscribers_path": "data/subscribers.json"
    },
    "webhook": {
      "enabled": false,
      "targets": []
    },
    "github_pages": {
      "enabled": true,
      "publish_dir": "site"
    }
  },
  "modes": {
    "tech_news": {
      "enabled": true,
      "item_type": "news",
      "sources": {
        "hackernews": {"enabled": true, "fetch_top_stories": 30, "min_score": 100},
        "rss": [{"name": "ArXiv CS.AI", "url": "https://example.com/feed.xml", "enabled": true}],
        "reddit": {"enabled": false, "subreddits": []},
        "telegram": {"enabled": false, "channels": []},
        "twitter": {"enabled": false, "apify_token_env": "APIFY_TOKEN"},
        "openbb": {"enabled": false, "watchlists": []},
        "ossinsight": {"enabled": false, "languages": ["All", "Python", "TypeScript"]}
      },
      "filters": {
        "min_source_score": 0,
        "include_keywords": [],
        "exclude_keywords": []
      },
      "scoring": {
        "source_authority_weight": 0.25,
        "engagement_weight": 0.25,
        "recency_weight": 0.20,
        "topic_relevance_weight": 0.30
      }
    },
    "scholar": {
      "enabled": true,
      "item_type": "paper",
      "time_window_hours": 48,
      "max_candidates": 200,
      "final_item_count": 10,
      "sources": {
        "arxiv": {
          "enabled": true,
          "categories": ["cs.LG", "cs.AI", "stat.ML", "cs.CL", "cs.RO", "cs.NE"],
          "max_results": 200
        },
        "openreview": {
          "enabled": true,
          "venue_ids": ["ICLR.cc/2026/Conference", "ICLR.cc/2025/Conference"]
        },
        "semantic_scholar": {
          "enabled": true,
          "api_key_env": "SEMANTIC_SCHOLAR_API_KEY",
          "cache_ttl_hours": 168,
          "max_requests_per_run": 40,
          "rate_limit_interval_sec": 1.1
        },
        "paperswithcode": {"enabled": true},
        "firecrawl": {"enabled": false, "api_key_env": "FIRECRAWL_API_KEY", "top_n": 10}
      },
      "filters": {
        "min_year": 2025,
        "max_year": 2026,
        "venue_allowlist": ["ICML", "NeurIPS", "ICLR", "AISTATS", "COLT", "UAI", "MLSys", "TMLR"],
        "keyword_allowlist": [],
        "keyword_blocklist": []
      }
    },
    "repo_learning": {
      "enabled": true,
      "item_type": "repo",
      "sources": {
        "github_search": {
          "enabled": true,
          "token_env": "GH_SEARCH_TOKEN",
          "domains": ["ai-agents", "mcp-ecosystem", "workflow-automation"],
          "min_stars": 500,
          "recent_years": 2,
          "active_within_days": 180,
          "per_page": 20
        },
        "firecrawl": {
          "enabled": false,
          "api_key_env": "FIRECRAWL_API_KEY"
        }
      },
      "ranking": {
        "final_item_count": 6,
        "enrich_top_n": 12,
        "history_lookback_days": 14
      }
    },
    "unified_digest": {
      "enabled": false,
      "include_modes": ["tech_news", "scholar", "repo_learning"],
      "max_items_per_type": 8,
      "max_total_items": 20,
      "cross_mode_clusters": true,
      "section_order": ["paper", "repo", "news"]
    }
  }
}
```

Schema rules:

- Secret values are never stored directly; config stores environment variable
  names.
- Mode sections are independently optional and independently validated.
- `run.enabled_modes` selects what the CLI executes.
- `unified_digest.include_modes` controls which source groups feed the combined
  digest.
- Mode-specific source configuration is allowed, but normalized output is always
  `SignalItem`.

## 6. CLI Command Design

Primary command:

```bash
aurora run --mode tech_news --config data/config.json --hours 24
```

Commands:

- `aurora run`
  - Runs the full shared pipeline.
  - Options:
    - `--mode tech_news|scholar|repo_learning|unified_digest|all`
    - `--config PATH`
    - `--hours N`
    - `--since ISO_DATETIME`
    - `--limit N`
    - `--dry-run`
    - `--skip-llm`
    - `--skip-delivery`
    - `--output-dir PATH`
- `aurora fetch`
  - Runs `fetch -> normalize` only and writes JSONL for inspection.
  - Useful for source debugging without LLM calls or delivery.
- `aurora score`
  - Reads a JSONL run file, executes deterministic and optional LLM scoring,
    and writes scored JSONL.
- `aurora render`
  - Renders a stored run to Markdown/HTML/email preview.
- `aurora deliver`
  - Delivers an existing rendered run through configured channels.
- `aurora config validate`
  - Validates config and reports enabled modes, required environment variables,
    and missing optional dependencies.
- `aurora sources list`
  - Prints available source adapters and their required config keys.
- `aurora doctor`
  - Checks Python version, optional packages, network credentials, writable
    output directories, and GitHub Actions-friendly environment variables.

Compatibility aliases can be added later:

- `aurora news` -> `aurora run --mode tech_news`
- `aurora scholar` -> `aurora run --mode scholar`
- `aurora repos` -> `aurora run --mode repo_learning`
- `aurora digest` -> `aurora run --mode unified_digest`

Exit behavior:

- Source-level failures do not fail the process if at least one source succeeds.
- Config validation failures return non-zero.
- Test failures in CI return non-zero.
- Delivery failures are reported per channel; `--strict-delivery` can make them
  non-zero.

## 7. GitHub Actions Design

Use one workflow for scheduled and manual Aurora runs.

```yaml
name: aurora-digest

on:
  schedule:
    - cron: "30 0 * * *" # 08:30 Asia/Shanghai
  workflow_dispatch:
    inputs:
      mode:
        description: "Aurora mode to run"
        required: false
        default: "unified_digest"
        type: choice
        options:
          - tech_news
          - scholar
          - repo_learning
          - unified_digest
          - all

permissions:
  contents: read
  pages: write
  id-token: write

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install uv
        uses: astral-sh/setup-uv@v5
      - name: Install dependencies
        run: uv sync --all-extras
      - name: Test
        run: uv run pytest
      - name: Validate config
        run: uv run aurora config validate --config data/config.json
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          SEMANTIC_SCHOLAR_API_KEY: ${{ secrets.SEMANTIC_SCHOLAR_API_KEY }}
          GH_SEARCH_TOKEN: ${{ secrets.GH_SEARCH_TOKEN }}
      - name: Run Aurora
        run: uv run aurora run --mode "${{ inputs.mode || 'unified_digest' }}" --config data/config.json
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          SEMANTIC_SCHOLAR_API_KEY: ${{ secrets.SEMANTIC_SCHOLAR_API_KEY }}
          GH_SEARCH_TOKEN: ${{ secrets.GH_SEARCH_TOKEN }}
          FIRECRAWL_API_KEY: ${{ secrets.FIRECRAWL_API_KEY }}
          APIFY_TOKEN: ${{ secrets.APIFY_TOKEN }}
          SMTP_USERNAME: ${{ secrets.SMTP_USERNAME }}
          EMAIL_PASSWORD: ${{ secrets.EMAIL_PASSWORD }}
          AURORA_EMAIL_RECIPIENTS: ${{ secrets.AURORA_EMAIL_RECIPIENTS }}
      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: site

  deploy:
    needs: run
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

Design notes:

- Prefer GitHub Pages artifact deployment over committing generated HTML back to
  `main`.
- If persistent state is required for recent-item suppression, store it as an
  artifact/cache initially, then add an explicit state persistence strategy.
- The workflow runs tests before live network/API calls.
- Each source adapter must redact tokens in warnings and logs.
- Scheduled runs default to `unified_digest`; manual dispatch can run a single
  mode for debugging.
- CI should include separate unit tests for:
  - `SignalItem` validation.
  - Config parsing and env expansion.
  - Dedup key extraction.
  - Deterministic scoring per mode.
  - Markdown and email rendering.

## 8. Migration Plan

1. Establish Aurora core without porting mode implementations.
   - Add `SignalItem`, `AuroraConfig`, stage protocols, and a no-op
     `PipelineRunner`.
   - Add tests proving the runner calls stages in exactly this order:
     `fetch -> normalize -> deduplicate -> score -> enrich -> summarize ->
     render -> deliver`.

2. Port shared utilities.
   - Bring over the AI client abstraction, JSON parser, token tracker, config
     env expansion, state-store skeleton, and token redaction helpers.
   - Add MIT attribution immediately in `NOTICE`.

3. Implement `tech_news` as the first real mode.
   - Start with RSS and Hacker News only.
   - Normalize into `SignalItem`.
   - Add URL/title dedup and deterministic scoring.
   - Add LLM scoring only after deterministic tests pass.

4. Implement `scholar`.
   - Start with arXiv and OpenReview fetch.
   - Normalize authors, abstract, venue, categories, code URLs, and source IDs
     into `metadata`.
   - Port scholar deterministic scoring as a pure function.
   - Add Semantic Scholar and Papers With Code as enrichment adapters after the
     baseline works.

5. Implement `repo_learning`.
   - Port GitHub search, README fetch, tree preview, query presets, and
     recently-recommended state.
   - Normalize repo stats and README/tree into `SignalItem.metadata`.
   - Port deterministic score breakdown and LLM learning-plan merge.

6. Implement shared rendering and delivery.
   - Render mode-specific Markdown fragments.
   - Wrap fragments in one Aurora page/email layout.
   - Keep email, filesystem, webhook, and Pages as delivery plugins.

7. Implement `unified_digest`.
   - Run selected mode source groups through the same pipeline.
   - Calibrate scores per type so a repo, paper, and news item are comparable.
   - Cluster related items by canonical URL, title similarity, and tags.
   - Render sections by type plus a cross-mode "connections" section.

8. Replace compatibility behavior.
   - Add aliases and migration docs for `horizon`, `horizon-research`, and
     `reporadar` users only after Aurora commands are stable.
   - Do not preserve old entry-point internals.

## 9. First Five PR-Sized Implementation Steps

### PR 1: Core Contracts

- Add package skeleton under `src/aurora`.
- Add `SignalItem`, `SourceStatus`, `ScoreResult`, and stage protocol types.
- Add `AuroraConfig` with only `run`, `pipeline`, `ai`, and `delivery`
  sections.
- Add tests for model validation and config env expansion.
- No network sources yet.

### PR 2: Pipeline Runner And CLI Dry Run

- Implement `PipelineRunner` with the fixed shared stage order.
- Add a fake in-memory mode for tests.
- Add `aurora config validate` and `aurora run --dry-run`.
- Add JSONL run serialization for intermediate `SignalItem` inspection.
- Add tests proving stage order, source failure isolation, and stable output.

### PR 3: Tech News MVP

- Add `modes.tech_news` with RSS and Hacker News adapters.
- Implement news normalization into `SignalItem`.
- Add URL/title dedup strategy.
- Add deterministic news scoring based on source authority, engagement,
  recency, and keyword relevance.
- Add basic Markdown rendering for news items.

### PR 4: Scholar MVP

- Add `modes.scholar` with arXiv and OpenReview fetchers.
- Port paper metadata normalization into `SignalItem.metadata`.
- Port deterministic scholar scoring as a pure scorer.
- Add scholar prompt set and optional LLM scoring.
- Add tests for DOI/arXiv/title dedup and score breakdowns.

### PR 5: Repo Learning MVP

- Add `modes.repo_learning` with GitHub search, README fetch, and tree preview.
- Port preset query building and slug/ref validation.
- Port repo deterministic scoring and package-file extraction.
- Normalize recommendations into `SignalItem` fields:
  - `why_it_matters` from `why_recommended`.
  - `learning_value` from `what_to_study`.
  - `action_items` from files to read, one-day clone, and one-week extension.
- Add state suppression for recently recommended repos.

## 10. Attribution / License Plan For Horizon MIT-Derived Code

Aurora may include MIT-derived code from:

- Horizon / Horizon-TechNews: MIT, copyright (c) 2026 Thysrael.
- Horizon-Scholar: MIT, forked from Horizon and carrying the same MIT license
  notice.
- Horizon-Github / RepoRadar: MIT declared in `pyproject.toml` and README.

Plan:

1. Keep Aurora's own `LICENSE` as MIT unless project policy requires otherwise.
2. Add `NOTICE` at the repo root before any derived code is committed.
3. In `NOTICE`, list each upstream source, repository URL, license, copyright
   holder if available, and the Aurora modules derived from it.
4. Preserve original MIT copyright and permission notices for substantial
   portions copied or adapted from Horizon-derived files.
5. Add short file headers only where code is substantially derived, for example:

   ```python
   # Portions adapted from Horizon (MIT), copyright (c) 2026 Thysrael.
   # See NOTICE for attribution details.
   ```

6. Track attribution at module granularity, not vague project-level claims.
   Example:
   - `aurora.ai.client` adapted from `Horizon-TechNews/src/ai/client.py`.
   - `aurora.modes.scholar.clients.arxiv` adapted from
     `Horizon-Scholar/src/research/clients/arxiv.py`.
   - `aurora.modes.repo_learning.github_client` adapted from
     `Horizon-Github/src/reporadar/github_client.py`.
7. Do not copy generated outputs, docs screenshots, historical reports, or
   secrets-bearing config into Aurora.
8. If a module is rewritten from behavior only, still mention the reference in
   `NOTICE` if the implementation closely follows the same structure.
9. Keep third-party dependency licenses separate from Horizon attribution.
10. Update attribution in the same PR that introduces any derived code.
