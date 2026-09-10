# Dependency Audit Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Aurora digest delivery reliable when dependency advisory data changes, while retaining a failing and visible security check.

**Architecture:** The digest workflow keeps its tests, build, content-quality gate, and publish checks, but no longer runs registry-backed vulnerability audits. A separate dependency-audit workflow owns Python and npm audits, and Dependabot proposes routine lockfile updates so newly disclosed advisories are handled without disabling security reporting.

**Tech Stack:** GitHub Actions, Dependabot, pytest, uv/pip-audit, npm audit, Astro

**Spec:** Approved design in the 2026-09-10 Codex task conversation.

## Global Constraints

- Dependency advisories must remain visible as failing GitHub checks.
- Dependency audit failures must not stop a healthy digest from publishing.
- Digest tests, build checks, strict delivery, and content-quality checks remain blocking.
- Fix all currently reported high and critical npm vulnerabilities.
- Keep the change limited to CI configuration, regression tests, and dependency metadata.

---

### Task 1: Lock the workflow boundary with a regression test

**Files:**
- Modify: `tests/core/test_github_actions.py`

**Interfaces:**
- Consumes: `.github/workflows/aurora-digest.yml`
- Produces: assertions that dependency audits exist only in `.github/workflows/dependency-audit.yml`

- [ ] **Step 1: Write the failing test**

Add assertions that the digest workflow contains no `pip-audit` or `npm audit`, while the dedicated audit workflow contains both scanners, bounded npm retry logic, and no publishing command.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/core/test_github_actions.py -q`
Expected: FAIL because dependency audits are still embedded in the digest workflow and the new workflow does not exist.

- [ ] **Step 3: Commit together with the implementation after the test turns green**

The test and workflow configuration are one atomic reliability fix.

### Task 2: Separate publishing from vulnerability monitoring

**Files:**
- Modify: `.github/workflows/aurora-digest.yml`
- Create: `.github/workflows/dependency-audit.yml`
- Create: `.github/dependabot.yml`

**Interfaces:**
- Consumes: `uv.lock`, `web/package-lock.json`
- Produces: independent GitHub security checks and weekly dependency update PRs

- [ ] **Step 1: Remove registry-backed audits from the digest job**

Keep the existing Python tests, Bandit scan, digest generation, quality checks, build, and Pages publishing unchanged.

- [ ] **Step 2: Add the dependency audit workflow**

Run on workflow dispatch, weekly schedule, pull requests touching dependency metadata, and pushes to `main` touching dependency metadata. Install with locked inputs, export Python requirements for `pip-audit`, and retain the existing bounded npm audit retry behavior.

- [ ] **Step 3: Add Dependabot configuration**

Configure weekly updates for npm in `/web`, pip in `/`, and GitHub Actions in `/`, with small open-PR limits.

- [ ] **Step 4: Run the regression test**

Run: `rtk pytest tests/core/test_github_actions.py -q`
Expected: PASS.

### Task 3: Patch current dependency advisories

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

**Interfaces:**
- Consumes: current npm registry advisory and package metadata
- Produces: a reproducible lockfile with no high or critical npm audit findings

- [ ] **Step 1: Update only affected package families**

Move Astro to at least `7.2.8`, Sharp to at least `0.35.4`, `js-yaml` to at least `4.3.2`, and `smol-toml` above `1.7.0`, accepting newer compatible versions selected by npm.

- [ ] **Step 2: Verify the dependency tree and audit**

Run: `rtk npm --prefix web audit --audit-level=high`
Expected: zero high or critical vulnerabilities.

- [ ] **Step 3: Verify the web application**

Run: `rtk npm --prefix web run lint`
Run: `rtk npm --prefix web run build`
Expected: both PASS.

### Task 4: Full verification and delivery

**Files:**
- Verify all changed files

**Interfaces:**
- Consumes: completed Tasks 1-3
- Produces: tested commit on `origin/main` and a successful dry-run workflow

- [ ] **Step 1: Run the full Python suite**

Run: `rtk uv run pytest -q`
Expected: all tests PASS.

- [ ] **Step 2: Validate workflow YAML and inspect the diff**

Parse both workflow files and Dependabot config as YAML, then review `git diff --check` and the complete diff.

- [ ] **Step 3: Commit and push**

Commit only the regression test, workflows, Dependabot config, and npm dependency metadata; push to `origin/main`.

- [ ] **Step 4: Run the safe GitHub dry run**

Dispatch `aurora-digest.yml` with `dry_run=true`, wait for completion, and inspect any failure before declaring success.
