"""Filesystem report delivery."""

from __future__ import annotations

import html
from pathlib import Path

from aurora.config import FilesystemDeliveryConfig
from aurora.models import DeliveryResult, RenderedDigest
from aurora.pipeline import StageContext


def write_filesystem_report(
    rendered: RenderedDigest,
    context: StageContext,
    config: FilesystemDeliveryConfig,
) -> DeliveryResult:
    """Write Markdown and HTML report files for one rendered digest."""
    report_dir = config.reports_dir / context.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = report_dir / f"{rendered.mode}.md"
    html_path = report_dir / f"{rendered.mode}.html"
    markdown_path.write_text(rendered.markdown, encoding="utf-8")
    html_path.write_text(rendered.html or markdown_to_html(rendered), encoding="utf-8")
    return DeliveryResult(
        channel="filesystem",
        destination=str(markdown_path),
        metadata={"markdown_path": str(markdown_path), "html_path": str(html_path)},
    )


def markdown_to_html(rendered: RenderedDigest) -> str:
    """Render simple Markdown as inspectable standalone HTML."""
    title = html.escape(rendered.title)
    body = html.escape(rendered.markdown)
    body = body.replace("\n", "<br>\n")
    return (
        "<!doctype html>\n"
        "<html><head><meta charset=\"utf-8\">"
        f"<title>{title}</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "line-height:1.5;max-width:960px;margin:40px auto;padding:0 20px;}"
        "code{background:#f4f4f4;padding:2px 4px;border-radius:4px;}</style>"
        "</head><body>"
        f"<main>{body}</main>"
        "</body></html>\n"
    )
