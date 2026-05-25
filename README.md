# Aurora

Aurora is a configurable personal intelligence radar for:

- `tech_news`: timely, high-engagement technology news.
- `scholar`: research papers by selected research fields.
- `repo_learning`: GitHub repositories by selected learning interests.
- `unified_digest`: one combined digest across papers, repos, and news.

## Quick Start

Install and validate:

```bash
rtk uv sync --dev
rtk uv run aurora config validate --config data/config.example.json
rtk uv run aurora doctor --config data/config.example.json
```

Run a no-network smoke test:

```bash
rtk uv run aurora run --dry-run --mode all --output-dir /tmp/aurora-dry-run
```

Run the unified daily digest:

```bash
rtk uv run aurora run --mode unified_digest --config data/config.example.json
```

## Repository Recommendations

Agents-focused repositories:

```bash
rtk uv run aurora run --mode repo_learning --repo-interest agents
```

Computer-vision repositories:

```bash
rtk uv run aurora run --mode repo_learning --repo-interest cv
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
