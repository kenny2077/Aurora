from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from aurora.cli import main
from aurora.models import SignalItem
from aurora.storage.jsonl import write_jsonl


def test_eval_replay_writes_unified_digest_quality_report(tmp_path: Path, capsys) -> None:
    fixture_path = tmp_path / "fixture.jsonl"
    output_path = tmp_path / "evaluation.json"
    write_jsonl(
        fixture_path,
        [
            _item("news:1", "news", "News", 9.0, source="rss"),
            _item("repo:1", "repo", "Repo", 8.5, source="github_search"),
            _item("paper:1", "paper", "Paper", 8.0, source="openreview"),
        ],
    )

    exit_code = main(["eval", "replay", "--fixture", str(fixture_path), "--output", str(output_path)])

    assert exit_code == 0
    assert "eval replay: ok" in capsys.readouterr().out
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["selected_item_ids"] == ["news:1", "repo:1", "paper:1"]
    assert report["item_counts"] == {"news": 1, "repo": 1, "paper": 1}
    assert report["missing_sections"] == []
    assert report["source_mix"] == {
        "news": {"rss": 1},
        "repo": {"github_search": 1},
        "paper": {"openreview": 1},
    }
    assert report["selection_diagnostics"] == {
        "news:1": {"quality_label": "news", "selection_reason": "source-diverse news item"},
        "repo:1": {
            "quality_label": "high_potential",
            "selection_reason": "new high-potential repository",
        },
        "paper:1": {"quality_label": "top_venue", "selection_reason": "current top-venue paper"},
    }
    assert "/10" not in report["markdown"]


def test_eval_compare_reports_selection_and_count_changes(tmp_path: Path, capsys) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(
        json.dumps(
            {
                "selected_item_ids": ["news:old", "repo:same", "paper:same"],
                "item_counts": {"news": 1, "repo": 1, "paper": 1},
                "missing_sections": [],
                "source_mix": {"news": {"rss": 1}},
            }
        ),
        encoding="utf-8",
    )
    after_path.write_text(
        json.dumps(
            {
                "selected_item_ids": ["news:new", "repo:same", "paper:same"],
                "item_counts": {"news": 1, "repo": 1, "paper": 1},
                "missing_sections": [],
                "source_mix": {"news": {"hackernews": 1}},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["eval", "compare", "--before", str(before_path), "--after", str(after_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "eval compare: ok" in output
    assert "added: news:new" in output
    assert "removed: news:old" in output
    assert "unchanged: 2" in output


def test_digest_quality_golden_fixtures_replay(tmp_path: Path) -> None:
    fixture_dir = Path("tests/fixtures/digest_quality")
    expected_topics = {"agents", "machine_learning", "computer_vision"}

    for topic in expected_topics:
        fixture_path = fixture_dir / f"{topic}.jsonl"
        expected_path = fixture_dir / f"{topic}.expected.json"
        output_path = tmp_path / f"{topic}.evaluation.json"

        exit_code = main(
            ["eval", "replay", "--fixture", str(fixture_path), "--output", str(output_path)]
        )

        assert exit_code == 0
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        actual = json.loads(output_path.read_text(encoding="utf-8"))
        assert actual["selected_item_ids"] == expected["selected_item_ids"]
        assert actual["item_counts"] == expected["item_counts"]
        assert actual["missing_sections"] == []
        assert "/10" not in actual["markdown"]


def _item(
    item_id: str,
    item_type: str,
    title: str,
    score: float,
    *,
    source: str,
) -> SignalItem:
    return SignalItem(
        id=item_id,
        type=item_type,
        title=title,
        url=f"https://example.com/{item_id.replace(':', '-')}",
        source=source,
        published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        raw_content=f"{title} explains a useful learning signal.",
        summary=f"{title} summary.",
        deterministic_score=score,
        final_score=score,
        why_it_matters=f"{title} has practical learning value.",
        metadata=_metadata(item_type, source),
    )


def _metadata(item_type: str, source: str) -> dict:
    if item_type == "repo":
        return {"stars": 2500, "forks": 120, "open_issues": 8, "language": "Python"}
    if item_type == "paper":
        return {"venue": "ICLR", "venue_year": 2026, "status": "accepted"}
    if source == "rss":
        return {"feed_name": "OpenAI News"}
    return {}
