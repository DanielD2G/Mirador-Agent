from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, JsonValue, field_validator

from mirador.domain.time import TimeWindow, ensure_utc


class Change(BaseModel):
    """Typed data exchanged across the application boundary."""

    at: datetime
    source: str
    actor: str | None
    kind: str
    resource: str | None
    summary: str
    details: dict[str, JsonValue]
    link: str | None

    _utc = field_validator("at")(ensure_utc)


class ChangesSource(Protocol):
    """Adapter contract for ChangesSource."""

    provider: str

    async def recent_changes(
        self, window: TimeWindow, service: str | None, target: str | None, limit: int
    ) -> list[Change]:
        """Respect the requested limit and return matching results."""
        ...
