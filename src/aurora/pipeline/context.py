"""Context passed through Aurora pipeline stages."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aurora.config import AuroraConfig


class StageContext(BaseModel):
    """Minimal shared context for stage protocol implementations."""

    model_config = ConfigDict(extra="forbid")

    mode: str
    run_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    since: datetime | None = None
    until: datetime | None = None
    config: AuroraConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("mode", "run_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value.strip()

