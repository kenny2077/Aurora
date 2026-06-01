# Aurora

Aurora is a daily learning radar for AI builders, researchers, and students.
It turns current papers, GitHub repositories, and timely AI/technology news into
one practical learning path you can run locally or on GitHub Actions.

Aurora tracks:

- `tech_news`: timely, high-engagement technology news.
- `scholar`: research papers by selected research fields.
- `repo_learning`: GitHub repositories by selected learning interests.
- `unified_digest`: one combined digest across papers, repos, and news.

## Today's Learning Workflow

The unified digest is designed around a compact daily loop:

- Pick one paper to understand.
- Pick one repo to study.
- Scan timely AI news with high engagement.
- Follow concrete action items that turn reading into experiments.

## Quick Start

## 10-Minute Setup

Local setup:

```bash
rtk uv sync --dev
rtk uv run aurora config validate --config data/config.example.json
rtk uv run aurora doctor --config data/config.example.json
```

Run a no-network smoke test:

```bash
rtk uv run aurora run --dry-run --mode all --output-dir /tmp/aurora-dry-run
```

Run the unified daily digest locally:

```bash
rtk uv run aurora run --mode unified_digest --config data/config.example.json
```

GitHub Actions setup:

1. Fork or clone the repository.
2. Configure GitHub Pages to publish from the `gh-pages` branch.
3. Add only the secrets for channels you enable.
4. Run the `aurora-digest` workflow manually once before relying on the schedule.

## Repository Recommendations

Agents-focused repositories:

```bash
rtk uv run aurora run --mode repo_learning --repo-interest agents
```

Computer-vision repositories:

```bash
rtk uv run aurora run --mode repo_learning --repo-interest cv
```

MCP ecosystem repositories:

```bash
rtk uv run aurora run --mode repo_learning --repo-interest mcp
```

Aurora uses `GH_SEARCH_TOKEN` first, then `GITHUB_TOKEN`, and can run
unauthenticated with lower GitHub rate limits.

## Scholar Radar

Machine-learning research:

```bash
rtk uv run aurora run --mode scholar --research-field ml
```

ML plus agents:

```bash
rtk uv run aurora run --mode scholar --research-field ml --research-field agents
```

## Secrets

Required only when email delivery is enabled:

- `SMTP_USERNAME, EMAIL_PASSWORD, AURORA_EMAIL_RECIPIENTS`

Optional:

- `DEEPSEEK_API_KEY` for LLM summaries and ranking.
- `GH_SEARCH_TOKEN` for higher GitHub Search API limits.
- `GITHUB_TOKEN fallback` is used automatically in GitHub Actions.
- `SEMANTIC_SCHOLAR_API_KEY` for optional scholar enrichment configuration.

## Delivery

By default Aurora writes Markdown/HTML reports to `reports/` and Pages-ready
artifacts to `site/`. Generated runtime paths are ignored by git.

Use deterministic-only mode:

```bash
rtk uv run aurora run --mode unified_digest --skip-llm
```

Preview without delivery:

```bash
rtk uv run aurora run --mode unified_digest --skip-delivery
```

See [docs/interests.md](docs/interests.md) for the full interest and research
field preset list.

## What Aurora Is Not

Aurora is not a realtime alerting system, an exhaustive literature review, a
SaaS dashboard, or a GitHub Trending clone. It is a self-hosted daily learning
radar that favors a small set of useful items over complete coverage.
