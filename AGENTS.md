# AGENTS.md

## Project Overview

Aurora is a self-hosted daily learning radar for AI builders, researchers, and
students. It turns timely tech news, research papers, and GitHub repositories
into a unified daily digest and learning path.

Supported modes:

- `tech_news`: timely, high-engagement technology news.
- `scholar`: research papers selected by research fields.
- `repo_learning`: GitHub repositories selected by learning interests.
- `unified_digest`: combined paper/repo/news digest.

## Tech Stack

- Python `>=3.11`
- `uv` package/environment management
- Pydantic v2 data/config models
- Async pipeline stages and source adapters
- `httpx`, `feedparser`, `defusedxml`
- Pytest test suite
- GitHub Actions workflow publishing a Jekyll/static site to `gh-pages`

## Important Directories

- `docs/navigation.md`: start here before broad repository exploration.
- `src/aurora/models.py`: shared contracts such as `SignalItem`, `ScoreResult`,
  `RenderedDigest`, and `DeliveryResult`.
- `src/aurora/config.py`: Pydantic config schema and defaults.
- `src/aurora/pipeline/`: shared context, stage protocols, and runner.
- `src/aurora/modes/`: mode-specific fetch/normalize/score/render logic.
- `src/aurora/ai/`: optional LLM client/ranking helpers.
- `src/aurora/delivery/`: filesystem, email, webhook, and GitHub Pages delivery.
- `src/aurora/storage/`: JSONL snapshots, config loading, source quality state.
- `tests/`: unit and integration-style tests; network tests use mocks.
- `data/config.example.json`: local example config.
- `data/actions.config.json`: GitHub Actions config.
- `.github/workflows/aurora-digest.yml`: scheduled/manual digest workflow.
- `docs/`: user-facing docs.

## Commands

Run commands from the repository root (`target/`).

Install dependencies:

```bash
rtk uv sync --dev
```

Validate config and environment:

```bash
rtk uv run aurora config validate --config data/config.example.json
rtk uv run aurora config validate --config data/actions.config.json
rtk uv run aurora doctor --config data/config.example.json
```

Run no-network smoke tests:

```bash
rtk uv run aurora run --dry-run --mode all --output-dir /tmp/aurora-dry-run
```

Run the main local digest:

```bash
rtk uv run aurora run --mode unified_digest --config data/config.example.json
```

Test:

```bash
rtk uv run pytest -q
rtk uv run pytest -q tests/modes/scholar/test_stages.py
```

Security scan:

```bash
rtk uvx bandit -q -r src
```

Build package:

```bash
rtk uv build
```

Typecheck/lint:

- No dedicated typecheck, formatter, or lint command is configured in
  `pyproject.toml`.
- Do not invent tool requirements. If adding one, update `pyproject.toml`,
  docs, and tests deliberately.

## Validation Policy

- For product code changes, run:
  - `rtk uv sync --dev`
  - `rtk uv run pytest -q`
  - `rtk uv run aurora config validate --config data/actions.config.json`
  - `rtk uvx bandit -q -r src`
- For mode-specific changes, also run the relevant focused tests under
  `tests/modes/<mode>/`.
- For workflow or Pages changes, run `tests/core/test_github_actions.py` and
  `tests/core/test_github_pages.py`.
- For docs-only changes, at minimum run `rtk git diff --check`; run focused
  docs tests if README/docs command text changes.
- Local config validation may report missing env vars if secrets are not set in
  the shell; that is acceptable when the command exits `0`.

## Coding Conventions

- Preserve the shared pipeline shape:
  `fetch -> normalize -> deduplicate -> score -> enrich -> summarize -> render -> deliver`.
- Normalize every item to `SignalItem`; keep scores in the `0..10` range.
- Keep source adapters isolated inside their mode packages.
- Keep optional enrichment best-effort. External enrichment failures should not
  kill a mode unless that behavior is explicitly required.
- Use async stage protocols consistently.
- Prefer Pydantic validators/config models over ad hoc validation.
- Tests for external HTTP behavior must use mocked transports; do not make live
  network calls in tests.
- Keep generated runtime files out of commits.
- Work directly on local `main` and push `main` unless the user explicitly asks
  for a branch/PR flow.

## Files And Directories To Avoid

- Do not edit or commit generated runtime paths:
  - `.venv/`
  - `__pycache__/`
  - `.pytest_cache/`
  - `data/runs/`
  - `data/cache/`
  - `data/aurora_state.json`
  - `reports/`
  - `site/`
  - `dist/`
- Do not modify generated `gh-pages` output in `site/` by hand; it is produced
  by Aurora and published by CI.
- Treat external reference material outside this repo, such as `references/`,
  `new references/`, and `planning/`, as read-only unless the user explicitly
  asks for planning/doc work there.
- Do not copy whole modules or folders from reference projects.

## Security And Secrets

- Never commit real API keys, SMTP passwords, tokens, cookies, or generated
  state containing secrets.
- Config files should contain environment variable names, not secret values.
- Known env vars:
  - `DEEPSEEK_API_KEY` for optional LLM ranking/summaries.
  - `SEMANTIC_SCHOLAR_API_KEY` for optional scholar enrichment.
  - `GH_SEARCH_TOKEN` or GitHub Actions `GITHUB_TOKEN` for GitHub API limits.
  - `SMTP_USERNAME`, `EMAIL_PASSWORD`, `AURORA_EMAIL_RECIPIENTS` for email.
- Keep error reporting sanitized; do not print auth headers or secret-like
  values.
- Avoid adding long sleeps, live retry loops, or workflow polling. Let the user
  provide manual GitHub Actions results when needed.

## RTK And Shell Usage

- Use `rtk` for shell commands so noisy output is filtered:

```bash
rtk git status
rtk uv run pytest -q
```

- Use `rtk proxy <cmd>` only when exact output matters, such as reading files or
  formatting-sensitive content.
- Do not run recursive, wildcard, or bulk deletion commands. Delete only one
  clearly specified file path at a time when deletion is necessary.

## Scope Boundaries

- Aurora is not a realtime alerting system, exhaustive literature review, SaaS
  dashboard, or GitHub Trending clone.
- Do not add new source providers, delivery channels, LLM behavior, or UI
  surfaces unless the user explicitly asks and tests are added.
- Keep changes small and mode-scoped when possible; shared pipeline or config
  changes need broader tests.
