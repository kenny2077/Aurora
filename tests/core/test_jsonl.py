from __future__ import annotations

import json

from aurora.models import ScoreResult, SignalItem
from aurora.storage.jsonl import write_jsonl


def test_write_jsonl_writes_stable_parseable_model_rows(tmp_path) -> None:
    path = tmp_path / "snapshots" / "items.jsonl"
    item = SignalItem(
        id="news:1",
        type="news",
        title="Signal",
        url="https://example.com/signal",
        source="example",
        published_at="2026-05-25T00:00:00Z",
        metadata={"b": 2, "a": 1},
    )

    output = write_jsonl(path, [item])

    assert output == path
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].startswith('{"action_items"')
    assert json.loads(lines[0])["metadata"] == {"a": 1, "b": 2}


def test_write_jsonl_writes_empty_file_for_empty_rows(tmp_path) -> None:
    path = tmp_path / "empty.jsonl"

    write_jsonl(path, [])

    assert path.exists()
    assert path.read_text(encoding="utf-8") == ""


def test_write_jsonl_accepts_score_results(tmp_path) -> None:
    path = tmp_path / "scores.jsonl"

    write_jsonl(path, [ScoreResult(item_id="news:1", final_score=8.0)])

    assert json.loads(path.read_text(encoding="utf-8"))["final_score"] == 8.0

