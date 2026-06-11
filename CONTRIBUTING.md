# Contributing to Aurora

Thanks for your interest in Aurora. Aurora is a self-hosted daily learning radar
for AI builders, researchers, and students. It turns tech news, GitHub
repositories, and research papers into a compact digest for email and GitHub
Pages.

This guide covers setup, testing, code expectations, and pull request hygiene.

## Setup

Run commands from the repository root.

```bash
git clone https://github.com/kenny2077/Aurora.git
cd Aurora

rtk uv sync --dev
npm --prefix web ci
```

Validate the local example configuration:

```bash
rtk uv run aurora config validate --config data/config.example.json
rtk uv run aurora doctor --config data/config.example.json
```

## Running Aurora Locally

Run a no-network smoke test:

```bash
rtk uv run aurora run --dry-run --mode all --output-dir /tmp/aurora-dry-run
```

Run the unified digest with the local example config:

```bash
rtk uv run aurora run --mode unified_digest --config data/config.example.json
```

Build the Astro web frontend:

```bash
npm --prefix web run build
```

Preview the web frontend:

```bash
npm --prefix web run dev -- --host 127.0.0.1 --port 4321
```

## Running Tests

For most product code changes:

```bash
rtk uv run pytest -q
rtk uv run aurora config validate --config data/actions.config.json
rtk uvx --from bandit bandit -q -r src
```

For documentation-only changes:

```bash
rtk uv run pytest -q tests/core/test_docs.py
rtk git diff --check
```

For web frontend changes:

```bash
npm --prefix web run build
```

For workflow or GitHub Pages changes:

```bash
rtk uv run pytest -q tests/core/test_github_actions.py tests/core/test_github_pages.py
npm --prefix web run build
```

## Project Structure

```text
src/aurora/
  cli.py                 # CLI parser and command dispatch
  config.py              # Pydantic configuration schema and defaults
  models.py              # Shared SignalItem, score, render, and delivery models
  pipeline/              # Shared stage protocols, context, and runner
  modes/
    tech_news/           # News fetch, score, summarize, and render logic
    repo_learning/       # GitHub repository learning radar
    scholar/             # Research paper radar
    unified_digest/      # Combined news, repo, and paper digest
  delivery/              # Filesystem, email, webhook, and Pages delivery
  storage/               # Config loading, snapshots, state, and cache helpers
  ai/                    # Optional LLM clients and ranking helpers
web/                     # AstroPaper-based Aurora Digest static site
tests/                   # Unit and integration-style tests
docs/                    # User-facing docs and README assets
```

## Code Style

- Preserve the shared pipeline shape:
  `fetch -> normalize -> deduplicate -> score -> enrich -> summarize -> render -> deliver`.
- Normalize every item to `SignalItem`; keep mode-specific fields in
  `metadata`.
- Keep scores in the `0.0..10.0` range.
- Keep source adapters isolated inside their mode packages.
- External enrichment should be best-effort unless a feature explicitly requires
  hard failure.
- Prefer Pydantic validators and config models over ad hoc validation.
- Tests for external HTTP behavior must use mocked transports. Do not make live
  network calls in tests.
- Do not commit generated runtime files from `data/runs/`, `data/cache/`,
  `reports/`, `site/`, `web/dist/`, or `dist/`.

## Pull Requests

1. Open an issue first for large behavior changes, new providers, delivery
   channels, or config schema changes.
2. Keep each pull request focused on one feature, bug fix, or documentation
   improvement.
3. Update README or docs when setup commands, CLI flags, secrets, config,
   workflow behavior, delivery behavior, or source behavior changes.
4. Include the verification commands you ran in the pull request description.
5. Do not include real API keys, email addresses, tokens, generated digests, or
   local run artifacts.

## Commit Messages

Use concise, conventional-style commit messages:

```text
feat: add repo topic preset
fix: handle empty scholar cache
docs: update GitHub Pages setup
test: cover unified digest ordering
```

## License

By contributing to Aurora, you agree that your contributions will be licensed
under the [MIT License](LICENSE).
