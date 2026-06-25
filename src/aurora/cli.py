"""Minimal Aurora CLI for PR 2 dry-run workflows."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aurora.config import AuroraConfig, ModeName
from aurora.ai.diagnostics import diagnose_ai_provider
from aurora.evaluation import benchmark_llm_fixture, compare_reports, replay_fixture, write_eval_report
from aurora.modes.repo_learning import build_repo_learning_pipeline
from aurora.modes.scholar import build_scholar_pipeline
from aurora.modes.tech_news import build_tech_news_pipeline
from aurora.modes.unified_digest import build_unified_digest_pipeline
from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.pipeline import ModePipeline, PipelineRunner, StageContext
from aurora.release_gate import load_release_gate_status, record_release_gate_run
from aurora.storage.config_loader import load_config


MODE_CHOICES = ("tech_news", "scholar", "repo_learning", "unified_digest", "all")
IMPLEMENTED_MODES = ("tech_news", "scholar", "repo_learning", "unified_digest")
TOPIC_PRESET_MAP = {
    "machine_learning": "ml",
    "agents_harness": "agents",
    "computer_vision": "cv",
}


class _DryRunFetch:
    name = "dry_run"

    async def fetch(self, context: StageContext) -> Sequence[Any]:
        return []


class _DryRunNormalize:
    async def normalize(self, raw_items: Sequence[Any], context: StageContext) -> list[SignalItem]:
        return []


class _DryRunDeduplicate:
    async def deduplicate(
        self, items: Sequence[SignalItem], context: StageContext
    ) -> list[SignalItem]:
        return list(items)


class _DryRunScore:
    async def score(self, items: Sequence[SignalItem], context: StageContext) -> list[ScoreResult]:
        return []


class _DryRunEnrich:
    async def enrich(
        self,
        items: Sequence[SignalItem],
        score_results: Sequence[ScoreResult],
        context: StageContext,
    ) -> list[SignalItem]:
        return list(items)


class _DryRunSummarize:
    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str:
        return f"Aurora dry run completed for {context.mode}."


class _DryRunRender:
    async def render(
        self, summary: str, items: Sequence[SignalItem], context: StageContext
    ) -> RenderedDigest:
        return RenderedDigest(
            mode=context.mode,
            title=f"Aurora {context.mode} dry run",
            markdown=summary,
        )


class _DryRunDeliver:
    async def deliver(
        self, rendered: RenderedDigest, context: StageContext
    ) -> list[DeliveryResult]:
        return [DeliveryResult(channel="dry_run")]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Aurora command-line interface."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "config":
            return _handle_config(args)
        if args.command == "doctor":
            return _handle_doctor(args)
        if args.command == "run":
            return _handle_run(args)
        if args.command == "eval":
            return _handle_eval(args)
        if args.command == "release-status":
            return _handle_release_status(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aurora")
    subparsers = parser.add_subparsers(dest="command")

    config_parser = subparsers.add_parser("config", help="Configuration commands")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    validate_parser = config_subparsers.add_parser("validate", help="Validate Aurora config")
    validate_parser.add_argument("--config", type=Path, default=None)

    doctor_parser = subparsers.add_parser("doctor", help="Check Aurora runtime environment")
    doctor_parser.add_argument("--config", type=Path, default=None)
    doctor_parser.add_argument("--local-llm", action="store_true")
    doctor_parser.add_argument("--llm", action="store_true")

    release_parser = subparsers.add_parser("release-status", help="Show Aurora release-gate status")
    release_parser.add_argument("--config", type=Path, default=None)
    release_parser.add_argument("--json", action="store_true")

    run_parser = subparsers.add_parser("run", help="Run Aurora")
    run_parser.add_argument("--config", type=Path, default=None)
    run_parser.add_argument("--mode", choices=MODE_CHOICES, default=None)
    run_parser.add_argument("--output-dir", type=Path, default=None)
    run_parser.add_argument("--run-id", default=None)
    run_parser.add_argument("--hours", type=int, default=None)
    run_parser.add_argument("--topic", choices=tuple(TOPIC_PRESET_MAP), default=None)
    run_parser.add_argument("--repo-interest", action="append", default=None)
    run_parser.add_argument("--research-field", action="append", default=None)
    run_parser.add_argument("--skip-llm", action="store_true")
    run_parser.add_argument("--local-llm", action="store_true")
    run_parser.add_argument("--free-mode", action="store_true")
    run_parser.add_argument("--skip-delivery", action="store_true")
    run_parser.add_argument("--strict-delivery", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true")

    eval_parser = subparsers.add_parser("eval", help="Evaluate digest quality from saved fixtures")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command", required=True)
    replay_parser = eval_subparsers.add_parser("replay", help="Replay SignalItem JSONL fixtures")
    replay_parser.add_argument("--config", type=Path, default=None)
    replay_parser.add_argument("--fixture", type=Path, required=True)
    replay_parser.add_argument("--output", type=Path, default=None)
    compare_parser = eval_subparsers.add_parser("compare", help="Compare two evaluation reports")
    compare_parser.add_argument("--before", type=Path, required=True)
    compare_parser.add_argument("--after", type=Path, required=True)
    compare_parser.add_argument("--output", type=Path, default=None)
    llm_parser = eval_subparsers.add_parser("llm", help="Benchmark configured LLM candidates from fixtures")
    llm_parser.add_argument("--config", type=Path, default=None)
    llm_parser.add_argument("--fixture", type=Path, required=True)
    llm_parser.add_argument("--candidate-config", type=Path, action="append", default=[])
    llm_parser.add_argument("--live", action="store_true")
    llm_parser.add_argument("--output", type=Path, default=None)

    return parser


def _handle_release_status(args: argparse.Namespace) -> int:
    config = AuroraConfig() if args.config is None else load_config(args.config)
    status = load_release_gate_status(config.release_gate)
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0 if status.get("ready") else 1
    print(f"release gate: {'ready' if status.get('ready') else 'not ready'}")
    print(f"enabled: {'yes' if status.get('enabled') else 'no'}")
    print(
        "clean scheduled runs: "
        f"{status.get('consecutive_clean_runs', 0)}/{status.get('required_clean_runs', 0)}"
    )
    latest = status.get("latest")
    if isinstance(latest, dict):
        blockers = latest.get("blockers")
        print(f"latest run: {latest.get('run_id') or 'unknown'}")
        print(f"latest blockers: {_format_list(blockers) if isinstance(blockers, list) else 'none'}")
    return 0 if status.get("ready") else 1


def _handle_config(args: argparse.Namespace) -> int:
    config = AuroraConfig() if args.config is None else load_config(args.config)
    print("config: ok")
    print(f"enabled modes: {', '.join(config.run.enabled_modes)}")
    missing = _missing_required_env_vars(config)
    print(f"missing required env vars: {', '.join(missing) if missing else 'none'}")
    return 0


def _handle_doctor(args: argparse.Namespace) -> int:
    config = AuroraConfig() if args.config is None else load_config(args.config)
    print("doctor: ok")
    print(f"python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print(f"enabled modes: {', '.join(config.run.enabled_modes)}")
    for label, path in {
        "output_dir": config.run.output_dir,
        "cache_dir": config.run.cache_dir,
        "state_path": config.run.state_path,
        "reports_dir": config.delivery.filesystem.reports_dir,
        "site_dir": config.delivery.github_pages.publish_dir,
    }.items():
        print(f"{label}: {'writable' if _is_writable_target(path) else 'not writable'} ({path})")
    print("pages branch: gh-pages")
    print("run summaries: enabled")
    print(f"email delivery: {'enabled' if config.delivery.email.enabled else 'disabled'}")
    missing_required = _missing_required_env_vars(config)
    print(f"missing required env vars: {', '.join(missing_required) if missing_required else 'none'}")
    optional = _missing_optional_env_vars(config)
    print(f"missing optional env vars: {', '.join(optional) if optional else 'none'}")
    if args.local_llm or args.llm:
        diagnostic = asyncio.run(
            diagnose_ai_provider(config.ai, require_local=False)
            if args.llm
            else diagnose_ai_provider(config.ai)
        )
        label = "LLM" if args.llm else "local LLM"
        print(
            f"{label}: {diagnostic.status} ({diagnostic.detail}; "
            f"{diagnostic.latency_ms}ms)"
        )
        if diagnostic.model_available is not None:
            print(f"{label} model: {'available' if diagnostic.model_available else 'missing'}")
        if diagnostic.json_response_valid is not None:
            print(
                f"{label} JSON response: "
                f"{'valid' if diagnostic.json_response_valid else 'invalid'}"
            )
        return 0 if diagnostic.status == "ok" else 1
    return 0


def _handle_run(args: argparse.Namespace) -> int:
    config = load_config(args.config) if args.config is not None else AuroraConfig()
    if args.output_dir is not None:
        config = config.model_copy(
            update={"run": config.run.model_copy(update={"output_dir": args.output_dir})}
        )
    if args.hours is not None:
        if args.hours < 1:
            print("error: --hours must be at least 1", file=sys.stderr)
            return 2
        config = config.model_copy(
            update={"run": config.run.model_copy(update={"time_window_hours": args.hours})}
        )
    config = _apply_run_overrides(config, args)

    modes = _select_modes(config, args.mode)
    run_id = args.run_id or ("dry-run" if args.dry_run else _default_run_id())

    if args.dry_run:
        results = asyncio.run(_run_dry_modes(config, modes, run_id))
    else:
        unsupported = [mode for mode in modes if mode not in IMPLEMENTED_MODES]
        if unsupported:
            print(
                f"error: mode not implemented yet: {', '.join(unsupported)}",
                file=sys.stderr,
            )
            return 2
        results = asyncio.run(
            _run_real_modes(
                config,
                modes,
                run_id,
                skip_llm=args.skip_llm,
                skip_delivery=args.skip_delivery,
                strict_delivery=args.strict_delivery,
            )
        )

    for result in results:
        _record_release_gate_if_needed(config, result)
        run_dir = config.run.output_dir / result.run_id / result.mode
        print(f"{result.mode}: ok ({run_dir})")
        _print_source_health(result)
        _print_ai_usage(result)
        _print_public_copy_quality(result)
        _print_run_warnings(result)
        for delivery_result in result.delivery_results:
            status = "ok" if delivery_result.ok else "failed"
            suffix = f" - {delivery_result.error}" if delivery_result.error else ""
            print(f"{result.mode}: delivery {delivery_result.channel}: {status}{suffix}")
    return 0


def _handle_eval(args: argparse.Namespace) -> int:
    if args.eval_command == "replay":
        config = AuroraConfig() if args.config is None else load_config(args.config)
        report = replay_fixture(config, args.fixture)
        if args.output is not None:
            write_eval_report(args.output, report)
        print("eval replay: ok")
        print(f"selected: {_format_list(report['selected_item_ids'])}")
        print(f"missing sections: {_format_list(report['missing_sections'])}")
        if args.output is not None:
            print(f"report: {args.output}")
        return 0
    if args.eval_command == "compare":
        comparison = compare_reports(args.before, args.after)
        if args.output is not None:
            write_eval_report(args.output, comparison)
        print("eval compare: ok")
        print(f"added: {_format_list(comparison['added'])}")
        print(f"removed: {_format_list(comparison['removed'])}")
        print(f"unchanged: {comparison['unchanged_count']}")
        if args.output is not None:
            print(f"report: {args.output}")
        return 0
    if args.eval_command == "llm":
        config = AuroraConfig() if args.config is None else load_config(args.config)
        candidates = [load_config(path) for path in args.candidate_config]
        report = benchmark_llm_fixture(config, args.fixture, candidates, live=args.live)
        if args.output is not None:
            write_eval_report(args.output, report)
        print("eval llm: ok")
        print(f"candidates: {len(candidates)} ({'live' if args.live else 'fixture-only'})")
        if args.output is not None:
            print(f"report: {args.output}")
        return 0
    raise ValueError(f"unknown eval command: {args.eval_command}")


def _select_modes(config: AuroraConfig, mode: str | None) -> list[ModeName]:
    if mode is None or mode == "all":
        return list(config.run.enabled_modes)
    return [mode]  # type: ignore[list-item]


def _apply_run_overrides(config: AuroraConfig, args: argparse.Namespace) -> AuroraConfig:
    modes = config.modes
    if args.topic:
        if args.repo_interest or args.research_field:
            raise ValueError("--topic cannot be combined with --repo-interest or --research-field")
        preset = TOPIC_PRESET_MAP[args.topic]
        repo_learning = type(modes.repo_learning).model_validate(
            {**modes.repo_learning.model_dump(mode="python"), "interests": [preset]}
        )
        scholar = type(modes.scholar).model_validate(
            {**modes.scholar.model_dump(mode="python"), "fields": [preset]}
        )
        modes = modes.model_copy(update={"repo_learning": repo_learning, "scholar": scholar})
    if args.repo_interest:
        repo_learning = type(modes.repo_learning).model_validate(
            {**modes.repo_learning.model_dump(mode="python"), "interests": args.repo_interest}
        )
        modes = modes.model_copy(update={"repo_learning": repo_learning})
    if args.research_field:
        scholar = type(modes.scholar).model_validate(
            {**modes.scholar.model_dump(mode="python"), "fields": args.research_field}
        )
        modes = modes.model_copy(update={"scholar": scholar})
    ai = config.ai
    if args.local_llm or args.free_mode:
        if not ai.is_local_provider():
            raise ValueError("--local-llm and --free-mode require a configured local provider")
        ai = type(ai).model_validate({**ai.model_dump(mode="python"), "local_only": True})
    if modes is not config.modes or ai is not config.ai:
        return config.model_copy(update={"modes": modes, "ai": ai})
    return config


async def _run_dry_modes(
    config: AuroraConfig, modes: Sequence[ModeName], run_id: str
) -> list[Any]:
    runner = PipelineRunner(output_dir=config.run.output_dir)
    results = []
    for mode in modes:
        context = StageContext(mode=mode, run_id=run_id, config=config)
        results.append(await runner.run(_build_dry_run_pipeline(mode), context))
    return results


async def _run_real_modes(
    config: AuroraConfig,
    modes: Sequence[ModeName],
    run_id: str,
    *,
    skip_llm: bool = False,
    skip_delivery: bool = False,
    strict_delivery: bool = False,
) -> list[Any]:
    now = datetime.now(timezone.utc)
    runner = PipelineRunner(output_dir=config.run.output_dir)
    results = []
    for mode in modes:
        context = StageContext(
            mode=mode,
            run_id=run_id,
            config=config,
            since=now - timedelta(hours=config.run.time_window_hours),
            until=now,
            metadata={
                "skip_llm": skip_llm,
                "skip_delivery": skip_delivery,
                "strict_delivery": strict_delivery,
            },
        )
        if mode == "tech_news":
            pipeline = build_tech_news_pipeline(config)
        elif mode == "scholar":
            pipeline = build_scholar_pipeline(config)
        elif mode == "repo_learning":
            pipeline = build_repo_learning_pipeline(config)
        elif mode == "unified_digest":
            pipeline = build_unified_digest_pipeline(config)
        else:
            raise ValueError(f"mode not implemented yet: {mode}")
        results.append(await runner.run(pipeline, context))
    return results


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")


def _missing_required_env_vars(config: AuroraConfig) -> list[str]:
    names: list[str] = []
    if config.delivery.email.enabled:
        names.extend(
            [
                config.delivery.email.smtp_username_env,
                config.delivery.email.password_env,
                config.delivery.email.recipients_env,
            ]
        )
    return [name for name in _unique(names) if not os.getenv(name)]


def _missing_optional_env_vars(config: AuroraConfig) -> list[str]:
    names = [config.modes.repo_learning.sources.github_search.token_env]
    if not config.ai.is_local_provider() or config.ai.provider == "anythingllm":
        names.insert(0, config.ai.api_key_env)
    if config.modes.scholar.sources.semantic_scholar.enabled:
        names.append(config.modes.scholar.sources.semantic_scholar.api_key_env)
    if config.delivery.email.enabled:
        names.extend(
            [
                config.delivery.email.smtp_username_env,
                config.delivery.email.password_env,
                config.delivery.email.recipients_env,
            ]
        )
    return [name for name in _unique(names) if not os.getenv(name)]


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _format_list(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "none"
    return ", ".join(str(value) for value in values)


def _is_writable_target(path: Path) -> bool:
    candidate = path if path.suffix == "" else path.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return os.access(candidate, os.W_OK)


def _print_source_health(result: Any) -> None:
    run_summary = _run_summary_for_result(result)
    if isinstance(run_summary, dict):
        health = run_summary.get("source_health")
        sources = run_summary.get("sources")
        if isinstance(health, dict) and isinstance(sources, list):
            ok_count = _int_value(health, "ok")
            failed_count = _int_value(health, "failed")
            rate_limited_count = _int_value(health, "rate_limited")
            print(
                f"{result.mode}: sources {ok_count} ok, "
                f"{failed_count} failed, {rate_limited_count} rate limited"
            )
            for source in sources:
                if not isinstance(source, dict) or source.get("ok") is not False:
                    continue
                suffix = f" - {_redact_secret_like_text(str(source.get('error')))}" if source.get("error") else ""
                print(f"{result.mode}: source {source.get('source', 'unknown')} failed{suffix}")
            return
    statuses = list(result.source_statuses)
    if not statuses:
        return
    ok_count = sum(1 for status in statuses if status.ok)
    failed_count = sum(1 for status in statuses if not status.ok)
    rate_limited_count = sum(1 for status in statuses if status.rate_limited)
    print(
        f"{result.mode}: sources {ok_count} ok, "
        f"{failed_count} failed, {rate_limited_count} rate limited"
    )
    for status in statuses:
        if not status.ok:
            suffix = f" - {_redact_secret_like_text(status.error)}" if status.error else ""
            print(f"{result.mode}: source {status.source} failed{suffix}")


def _record_release_gate_if_needed(config: AuroraConfig, result: Any) -> None:
    if result.mode != "unified_digest" or not config.release_gate.enabled:
        return
    run_summary = _run_summary_for_result(result)
    if not isinstance(run_summary, dict):
        return
    status = record_release_gate_run(
        config.release_gate,
        run_summary,
        scheduled=_is_scheduled_run(),
    )
    print(
        f"{result.mode}: release gate "
        f"{status.get('consecutive_clean_runs', 0)}/{status.get('required_clean_runs', 0)} "
        f"clean scheduled runs"
    )


def _run_summary_for_result(result: Any) -> dict[str, Any] | None:
    output_paths = getattr(result, "output_paths", None)
    if isinstance(output_paths, dict):
        path = output_paths.get("run_summary")
        if isinstance(path, Path) and path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
            except (OSError, json.JSONDecodeError):
                pass
    run_summary = result.rendered_digest.metadata.get("run_summary")
    return run_summary if isinstance(run_summary, dict) else None


def _is_scheduled_run() -> bool:
    return os.getenv("GITHUB_EVENT_NAME") == "schedule" or os.getenv("AURORA_SCHEDULED_RUN") == "true"


def _redact_secret_like_text(value: str) -> str:
    patterns = [
        r"(?i)(authorization:\s*bearer\s+)[^\s]+",
        r"(?i)([a-z0-9_]*(?:token|key|secret|password)[a-z0-9_]*=)[^\s]+",
    ]
    redacted = value
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1[REDACTED]", redacted)
    return redacted


def _print_run_warnings(result: Any) -> None:
    run_summary = _run_summary_for_result(result)
    if not isinstance(run_summary, dict):
        return
    warnings = run_summary.get("warnings")
    if not isinstance(warnings, list):
        return
    for warning in warnings:
        text = str(warning).strip()
        if text:
            print(f"{result.mode}: warning {text}")


def _print_ai_usage(result: Any) -> None:
    run_summary = _run_summary_for_result(result)
    if not isinstance(run_summary, dict):
        return
    usage = run_summary.get("ai_usage")
    if not isinstance(usage, dict):
        return
    print(
        f"{result.mode}: ai requests "
        f"{_int_value(usage, 'requested_calls')} requested, "
        f"{_int_value(usage, 'succeeded_calls')} succeeded, "
        f"{_int_value(usage, 'failed_calls')} failed, "
        f"{_int_value(usage, 'skipped_by_budget')} skipped, "
        f"{_int_value(usage, 'network_attempts')} network attempts, "
        f"{_int_value(usage, 'retried_calls')} retries, "
        f"~{_int_value(usage, 'approx_total_tokens')} tokens, "
        f"{_int_value(usage, 'deterministic_fallbacks')} fallbacks, "
        f"{_int_value(usage, 'latency_ms_average')}ms average, "
        f"provider {usage.get('provider', 'unknown')}/{usage.get('model', 'unknown')}"
    )


def _print_public_copy_quality(result: Any) -> None:
    run_summary = _run_summary_for_result(result)
    if not isinstance(run_summary, dict):
        return
    quality = run_summary.get("public_copy_quality")
    if not isinstance(quality, dict):
        return
    print(
        f"{result.mode}: public copy "
        f"{_int_value(quality, 'selected_items')} selected, "
        f"{_int_value(quality, 'accepted')} accepted, "
        f"{_int_value(quality, 'repair_attempted')} repair requested, "
        f"{_int_value(quality, 'repaired')} repaired, "
        f"{_int_value(quality, 'replaced')} replaced, "
        f"{_int_value(quality, 'unresolved_selected')} unresolved, "
        f"{_int_value(quality, 'delivery_blocked')} delivery blocked"
    )


def _int_value(payload: dict[str, Any], key: str) -> int:
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _build_dry_run_pipeline(mode: str) -> ModePipeline:
    return ModePipeline(
        mode=mode,
        fetch_stages=[_DryRunFetch()],
        normalize_stage=_DryRunNormalize(),
        deduplicate_stage=_DryRunDeduplicate(),
        score_stage=_DryRunScore(),
        enrich_stage=_DryRunEnrich(),
        summarize_stage=_DryRunSummarize(),
        render_stage=_DryRunRender(),
        deliver_stage=_DryRunDeliver(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
