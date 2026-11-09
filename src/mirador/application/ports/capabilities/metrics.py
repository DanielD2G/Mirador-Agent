from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, field_validator

from mirador.domain.time import TimeWindow, ensure_utc


class Point(BaseModel):
    """Typed data exchanged across the application boundary."""

    ts: datetime
    value: float

    _utc = field_validator("ts")(ensure_utc)


class Series(BaseModel):
    """Typed data exchanged across the application boundary."""

    name: str
    labels: dict[str, str]
    points: list[Point]


class Alert(BaseModel):
    """Typed data exchanged across the application boundary."""

    name: str
    state: str
    since: datetime | None

    @field_validator("since")
    @classmethod
    def validate_since(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    labels: dict[str, str]
    summary: str


class MetricsSource(Protocol):
    """Adapter contract for MetricsSource."""

    provider: str
    query_syntax: str

    async def query_range(
        self, expr: str, window: TimeWindow, step: timedelta, target: str | None
    ) -> list[Series]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def list_alerts(self, target: str | None) -> list[Alert]:
        """Return the requested data, preserving source identifiers."""
        ...
