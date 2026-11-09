"""Deterministic log queries over fixture rows."""

from collections import Counter
from collections.abc import Sequence
from datetime import timedelta

from mirador.application.ports.capabilities.filter import Filter
from mirador.application.ports.capabilities.logs import (
    Bucket,
    GroupCount,
    LogSearchResult,
    LogsSource,
    Row,
    TimeBucket,
)
from mirador.application.tooling.spec import ToolError
from mirador.domain.time import TimeWindow

from .dataset import CaseDataset, in_window, matches, row_time


class FakeLogs(LogsSource):
    def __init__(self, provider: str, dataset: CaseDataset, auth_error: bool) -> None:
        self.provider, self.dataset, self.auth_error = provider, dataset, auth_error

    def _rows(
        self, window: TimeWindow, filter: Filter, target: str | None
    ) -> list[Row]:
        if self.auth_error:
            raise ToolError(
                "Fixture authentication failure", actionable=False, kind="auth"
            )
        return [
            row
            for row in self.dataset.logs.get(self.provider, [])
            if in_window(row, window)
            and matches(row, filter)
            and (target is None or row.get("target") == target)
        ]

    async def search(
        self, window: TimeWindow, filter: Filter, limit: int, target: str | None
    ) -> LogSearchResult:
        rows = self._rows(window, filter, target)
        return LogSearchResult(
            rows=rows[: max(0, limit)], truncated=len(rows) > max(0, limit)
        )

    async def count(
        self,
        window: TimeWindow,
        filter: Filter,
        group_by: Sequence[str],
        target: str | None,
    ) -> list[GroupCount]:
        rows = self._rows(window, filter, target)
        if not group_by:
            return [GroupCount(group={}, count=len(rows))]
        counts = Counter(tuple(str(row.get(k, "")) for k in group_by) for row in rows)
        return [
            GroupCount(group=dict(zip(group_by, key, strict=True)), count=n)
            for key, n in sorted(counts.items())
        ]

    async def top_values(
        self,
        window: TimeWindow,
        filter: Filter,
        field: str,
        limit: int,
        target: str | None,
    ) -> list[Bucket]:
        counts = Counter(
            str(row[field])
            for row in self._rows(window, filter, target)
            if field in row
        )
        return [
            Bucket(value=k, count=n)
            for k, n in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[
                : max(0, limit)
            ]
        ]

    async def histogram(
        self,
        window: TimeWindow,
        filter: Filter,
        interval: timedelta,
        target: str | None,
    ) -> list[TimeBucket]:
        rows = self._rows(window, filter, target)
        if interval.total_seconds() <= 0:
            raise ToolError(
                "Interval must be positive", actionable=True, kind="invalid"
            )
        counts = Counter((row_time(row) - window.start) // interval for row in rows)
        result = []
        start, index = window.start, 0
        while start < window.end:
            result.append(TimeBucket(start=start, count=counts[index]))
            start, index = start + interval, index + 1
        return result
