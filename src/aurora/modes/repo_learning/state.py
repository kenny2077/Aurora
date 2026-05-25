"""Recently recommended repository state for repo_learning mode."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RepoLearningStateStore:
    """JSON state store for suppressing recently recommended repositories."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def recent_ids(self, cutoff: datetime) -> set[str]:
        recommended = self._recommended()
        recent: set[str] = set()
        for repo_id, raw_timestamp in recommended.items():
            timestamp = _parse_datetime(str(raw_timestamp))
            if timestamp is not None and timestamp >= cutoff:
                recent.add(repo_id)
        return recent

    def mark_recommended(self, repo_ids: list[str], when: datetime) -> None:
        if not repo_ids:
            return
        data = self._read_data()
        repo_learning = data.setdefault("repo_learning", {})
        recommended = repo_learning.setdefault("recommended", {})
        for repo_id in repo_ids:
            recommended[repo_id] = when.astimezone(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _recommended(self) -> dict[str, str]:
        data = self._read_data()
        repo_learning = data.get("repo_learning")
        if not isinstance(repo_learning, dict):
            return {}
        recommended = repo_learning.get("recommended")
        if not isinstance(recommended, dict):
            return {}
        return {str(key): str(value) for key, value in recommended.items()}

    def _read_data(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
