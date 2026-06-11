# Security Policy

## Design Principles

Aurora is designed to be self-hosted, inspectable, and conservative with user
secrets. It runs locally or in GitHub Actions, fetches public source data, and
publishes a digest to channels you configure.

## Security Properties

- **Self-hosted execution.** Aurora runs in your local checkout or your GitHub
  Actions workflow. There is no Aurora-hosted backend.
- **Explicit secrets.** Email and enrichment credentials are read from
  environment variables or GitHub Actions secrets, not committed configuration.
- **No secrets in generated output.** Digests, Pages output, reports, and
  `run_summary.json` should never include API keys, email passwords, SMTP
  credentials, or GitHub tokens.
- **Optional LLM use.** LLM summaries and ranking require an explicit
  `DEEPSEEK_API_KEY`. Use `--skip-llm` for deterministic-only operation.
- **Bounded external sources.** Source adapters fetch from configured public
  news, GitHub, arXiv/OpenReview, and enrichment APIs. Tests must mock external
  HTTP behavior.
- **Defensive XML parsing.** Aurora uses `defusedxml` for XML handling where
  XML parsing is needed.
- **No shell execution from source content.** Fetched headlines, abstracts,
  repository metadata, and summaries are treated as data, not commands.
- **Generated artifacts stay out of git.** Runtime paths such as `data/runs/`,
  `data/cache/`, `reports/`, `site/`, `web/dist/`, and `dist/` are ignored and
  should not be committed.
- **GitHub Pages publishing is generated.** The workflow builds `web/dist/` and
  pushes the generated site to `gh-pages`; do not manually edit generated Pages
  output.

## Reporting a Vulnerability

If you discover a security issue, please report it through GitHub:

1. Prefer a private GitHub Security Advisory for the repository if the issue
   exposes credentials, enables code execution, leaks private data, or affects
   GitHub Actions publishing.
2. If private advisories are unavailable, open a GitHub issue with minimal
   reproduction details and avoid posting secrets, tokens, private email
   addresses, or exploit payloads.

Please include:

- A short description of the vulnerability.
- Affected files, commands, modes, or workflow steps.
- Whether the issue requires secrets, GitHub Actions, email delivery, Pages
  publishing, or live external sources.
- A minimal reproduction using dummy credentials or mocked data.

Maintainers will review reports as soon as practical and coordinate a fix before
encouraging public disclosure of sensitive details.

## Supported Versions

| Version | Supported |
| --- | --- |
| `0.1.x` | Yes |
| `< 0.1.0` | No |

## Security Checks

Use these checks before submitting security-sensitive changes:

```bash
rtk uv run pytest -q
rtk uv run aurora config validate --config data/actions.config.json
rtk uvx --from bandit bandit -q -r src
```

For workflow or Pages changes, also run:

```bash
rtk uv run pytest -q tests/core/test_github_actions.py tests/core/test_github_pages.py
npm --prefix web run build
```
