"""Minimal Aurora CLI for PR 2 dry-run workflows."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aurora.config import AuroraConfig, ModeName
from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.pipeline import ModePipeline, PipelineRunner, StageContext
from aurora.storage.config_loader import load_config


MODE_CHOICES = ("tech_news", "scholar", "repo_learning", "unified_digest", "all")


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

    run_parser = subparsers.add_parser("run", help="Run Aurora")
    run_parser.add_argument("--config", type=Path, default=None)
    run_parser.add_argument("--mode", choices=MODE_CHOICES, default=None)
    run_parser.add_argument("--output-dir", type=Path, default=None)
    run_parser.add_argument("--run-id", default=None)
    run_parser.add_argument("--dry-run", action="store_true")

    return parser


def _handle_config(args: argparse.Namespace) -> int:
    if args.config is None:
        AuroraConfig()
    else:
        load_config(args.config)
    print("config: ok")
    return 0


def _handle_run(args: argparse.Namespace) -> int:
    if not args.dry_run:
        print("error: only dry-run is available in PR 2", file=sys.stderr)
        return 2

    config = load_config(args.config) if args.config is not None else AuroraConfig()
    if args.output_dir is not None:
        config = config.model_copy(
            update={"run": config.run.model_copy(update={"output_dir": args.output_dir})}
        )

    modes = _select_modes(config, args.mode)
    run_id = args.run_id or "dry-run"
    results = asyncio.run(_run_dry_modes(config, modes, run_id))
    for result in results:
        run_dir = config.run.output_dir / result.run_id / result.mode
        print(f"{result.mode}: ok ({run_dir})")
    return 0


def _select_modes(config: AuroraConfig, mode: str | None) -> list[ModeName]:
    if mode is None or mode == "all":
        return list(config.run.enabled_modes)
    return [mode]  # type: ignore[list-item]


async def _run_dry_modes(
    config: AuroraConfig, modes: Sequence[ModeName], run_id: str
) -> list[Any]:
    runner = PipelineRunner(output_dir=config.run.output_dir)
    results = []
    for mode in modes:
        context = StageContext(mode=mode, run_id=run_id, config=config)
        results.append(await runner.run(_build_dry_run_pipeline(mode), context))
    return results


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

