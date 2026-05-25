"""Strict JSON extraction helpers for AI responses."""

from __future__ import annotations

import json
import re
from typing import Any


FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def parse_json_object(value: str) -> dict[str, Any]:
    """Parse a JSON object from plain or fenced model output."""
    text = value.strip()
    match = FENCED_JSON_RE.search(text)
    if match:
        text = match.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI response must be a JSON object")
    return parsed
