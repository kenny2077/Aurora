"""Minimal Aurora CLI for PR 2 dry-run workflows."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aurora.config import AuroraConfig, ModeName
from aurora.modes.repo_learning import build_repo_learning_pipeline
from aurora.modes.scholar import build_scholar_pipeline
from aurora.modes.tech_news import build_tech_news_pipeline
from aurora.modes.unified_digest import build_unified_digest_pipeline
from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.pipeline import ModePipeline, PipelineRunner, StageContext
from aurora.storage.config_loader import load_config


MODE_CHOICES = ("tech_news", "scholar", "repo_learning", "unified_digest", "all")
IMPLEMENTED_MODES = ("tech_news", "scholar", "repo_learning", "unified_digest")


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

    run_parser = subparsers.add_parser("run", help="Run Aurora")
    run_parser.add_argument("--config", type=Path, default=None)
    run_parser.add_argument("--mode", choices=MODE_CHOICES, default=None)
    run_parser.add_argument("--output-dir", type=Path, default=None)
    run_parser.add_argument("--run-id", default=None)
    run_parser.add_argument("--hours", type=int, default=None)
    run_parser.add_argument("--repo-interest", action="append", default=None)
    run_parser.add_argument("--research-field", action="append", default=None)
    run_parser.add_argument("--skip-llm", action="store_true")
    run_parser.add_argument("--skip-delivery", action="store_true")
    run_parser.add_argument("--strict-delivery", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true")

    return parser


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
    missing_required = _missing_required_env_vars(config)
    print(f"missing required env vars: {', '.join(missing_required) if missing_required else 'none'}")
    optional = _missing_optional_env_vars(config)
    print(f"missing optional env vars: {', '.join(optional) if optional else 'none'}")
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
        run_dir = config.run.output_dir / result.run_id / result.mode
        print(f"{result.mode}: ok ({run_dir})")
        for delivery_result in result.delivery_results:
            status = "ok" if delivery_result.ok else "failed"
            suffix = f" - {delivery_result.error}" if delivery_result.error else ""
            print(f"{result.mode}: delivery {delivery_result.channel}: {status}{suffix}")
    return 0


def _select_modes(config: AuroraConfig, mode: str | None) -> list[ModeName]:
    if mode is None or mode == "all":
        return list(config.run.enabled_modes)
    return [mode]  # type: ignore[list-item]


def _apply_run_overrides(config: AuroraConfig, args: argparse.Namespace) -> AuroraConfig:
    modes = config.modes
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
    if modes is not config.modes:
        return config.model_copy(update={"modes": modes})
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
    names = [
        config.ai.api_key_env,
        config.modes.repo_learning.sources.github_search.token_env,
    ]
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


def _is_writable_target(path: Path) -> bool:
    candidate = path if path.suffix == "" else path.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return os.access(candidate, os.W_OK)


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
