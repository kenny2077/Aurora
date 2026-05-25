"""GitHub Pages artifact delivery."""

from __future__ import annotations

from aurora.config import GitHubPagesDeliveryConfig
from aurora.delivery.filesystem import markdown_to_html
from aurora.models import DeliveryResult, RenderedDigest
from aurora.pipeline import StageContext


def write_pages_artifact(
    rendered: RenderedDigest,
    context: StageContext,
    config: GitHubPagesDeliveryConfig,
) -> DeliveryResult:
    """Write a Pages-ready static artifact for one digest."""
    mode_dir = config.publish_dir / rendered.mode
    mode_dir.mkdir(parents=True, exist_ok=True)
    html_path = mode_dir / "index.html"
    markdown_path = mode_dir / "index.md"
    html_path.write_text(rendered.html or markdown_to_html(rendered), encoding="utf-8")
    markdown_path.write_text(rendered.markdown, encoding="utf-8")
    return DeliveryResult(
        channel="github_pages",
        destination=str(html_path),
        metadata={"html_path": str(html_path), "markdown_path": str(markdown_path)},
    )
