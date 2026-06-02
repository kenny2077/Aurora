# Navigation

Use this file before broad repository exploration. It points to the smallest
set of files that usually answer where a behavior lives.

## Key Files

- `README.md` — product overview, setup, CLI examples, secrets, Pages setup.
- `AGENTS.md` — repo-wide agent rules, validation policy, avoid-touch paths.
- `pyproject.toml` — Python version, dependencies, pytest config, console script.
- `data/config.example.json` — local example config.
- `data/actions.config.json` — GitHub Actions config.
- `.github/workflows/aurora-digest.yml` — scheduled/manual workflow and Pages publish.
- `docs/interests.md` — repo interests and scholar research field presets.
- `docs/merge_design.md` — original integration design and product structure.
- `src/aurora/cli.py` — CLI parser and command dispatch.
- `src/aurora/config.py` — config schema and defaults.
- `src/aurora/models.py` — shared item/result/delivery contracts.
- `src/aurora/pipeline/` — shared stage protocols, context, and runner.
- `src/aurora/modes/` — mode implementations.
- `tests/` — test suite.

## Common Tasks

### Add Or Change A Mode Feature

Read:

1. `src/aurora/modes/<mode>/`
2. `tests/modes/<mode>/`
3. `src/aurora/config.py`
4. `README.md` and `docs/interests.md` if CLI/config behavior changes

### Fix A Bug

Read:

1. failing test, workflow log, or `data/runs/<run_id>/<mode>/run_summary.json`
2. target module under `src/aurora/`
3. nearby tests under `tests/`
4. related docs only if user-facing behavior changes

### Change CLI Behavior

Read:

1. `src/aurora/cli.py`
2. `tests/core/test_cli.py`
3. `README.md`
4. `docs/interests.md` for interest/research-field flags

### Change Config Schema

Read:

1. `src/aurora/config.py`
2. `tests/core/test_config.py`
3. `data/config.example.json`
4. `data/actions.config.json`
5. README/docs sections that mention the setting

### Change Pipeline Behavior

Read:

1. `src/aurora/pipeline/runner.py`
2. `src/aurora/pipeline/stages.py`
3. `src/aurora/pipeline/context.py`
4. `tests/core/test_pipeline_runner.py`
5. affected mode tests

### Change Provider Or Source Adapter

Read:

1. adapter/source module under `src/aurora/modes/<mode>/`
2. corresponding stage/scoring/render module
3. `src/aurora/config.py`
4. mocked tests under `tests/modes/<mode>/`
5. README/docs for setup or secret changes

### Change Delivery, Pages, Or Email

Read:

1. `src/aurora/delivery/`
2. `tests/core/test_delivery.py`
3. `tests/core/test_github_pages.py`
4. `.github/workflows/aurora-digest.yml`
5. `tests/core/test_github_actions.py`
6. README Pages/secrets sections

### Change LLM Behavior

Read:

1. `src/aurora/ai/`
2. mode prompt files under `src/aurora/modes/*/prompts.py`
3. `tests/core/test_ai.py`
4. mode tests that assert summaries, scores, or actions
5. README secrets and deterministic-only examples

## Runtime Artifacts

Generated files are useful for debugging but should not be committed:

- `data/runs/`
- `data/cache/`
- `data/aurora_state.json`
- `reports/`
- `site/`

