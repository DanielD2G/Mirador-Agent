from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, JsonValue, field_validator

from mirador.application.ports.capabilities.filter import Filter
from mirador.domain.time import TimeWindow, ensure_utc

Row = dict[str, JsonValue]


class LogSearchResult(BaseModel):
    """Typed data exchanged across the application boundary."""

    rows: list[Row]
    truncated: bool


class GroupCount(BaseModel):
    """Typed data exchanged across the application boundary."""

    group: dict[str, str]
    count: int


class Bucket(BaseModel):
    """Typed data exchanged across the application boundary."""

    value: str
    count: int


class TimeBucket(BaseModel):
    """Typed data exchanged across the application boundary."""

    start: datetime
    count: int

    _utc = field_validator("start")(ensure_utc)


class LogsSource(Protocol):
    """Search rows expose ts (ISO UTC), message, and service when available.

    Provider-specific fields may supplement these reserved normalized keys.
    """

    provider: str

    async def search(
        self, window: TimeWindow, filter: Filter, limit: int, target: str | None
    ) -> LogSearchResult:
        """Respect limit and mark truncated when additional rows exist."""
        ...

    async def count(
        self,
        window: TimeWindow,
        filter: Filter,
        group_by: Sequence[str],
        target: str | None,
    ) -> list[GroupCount]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def top_values(
        self,
        window: TimeWindow,
        filter: Filter,
        field: str,
        limit: int,
        target: str | None,
    ) -> list[Bucket]:
        """Respect the requested limit and return matching results."""
        ...

    async def histogram(
        self,
        window: TimeWindow,
        filter: Filter,
        interval: timedelta,
        target: str | None,
    ) -> list[TimeBucket]:
        """Return UTC time buckets covering the requested window."""
        ...
