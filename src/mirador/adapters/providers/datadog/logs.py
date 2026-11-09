"""Datadog log search and analytics."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from mirador.application.ports.capabilities.filter import Filter
from mirador.application.ports.capabilities.logs import (
    Bucket,
    GroupCount,
    LogSearchResult,
    LogsSource,
    TimeBucket,
)
from mirador.application.ports.health import HealthCheckable, HealthResult
from mirador.domain.time import TimeWindow

from .client import DatadogClient


def _field(field: str) -> str:
    return (
        field
        if field in {"service", "status", "host", "source"}
        else "@" + field.removeprefix("@")
    )


def to_native(filter: Filter) -> str:
    parts = []
    for clause in filter.clauses:
        escaped = "".join(
            "\\" + char
            if char in '+-= &|><!(){}[]^"“”~*?:\\/#' or char.isspace()
            else char
            for char in clause.value
        )
        value = '"' + escaped + '"'
        if clause.field is None:
            parts.append(value)
        else:
            if clause.op == "contains":
                value = "*" + escaped + "*"
            parts.append(
                ("-" if clause.op == "neq" else "") + _field(clause.field) + ":" + value
            )
    return " AND ".join(parts) or "*"


def _filter(window: TimeWindow, filter: Filter) -> dict[str, str]:
    return {
        "from": window.start.isoformat(),
        "to": window.end.isoformat(),
        "query": to_native(filter),
    }


class DatadogLogs(LogsSource, HealthCheckable):
    provider = "datadog"

    def __init__(self, client: DatadogClient) -> None:
        self.client = client

    async def health_check(self) -> HealthResult:
        return await self.client.health_check()

    async def search(
        self, window: TimeWindow, filter: Filter, limit: int, target: str | None
    ) -> LogSearchResult:
        rows: list[dict[str, Any]] = []
        cursor = None
        seen = set()
        while len(rows) <= limit:
            page: dict[str, Any] = {"limit": min(1000, max(1, limit + 1 - len(rows)))}
            if cursor:
                page["cursor"] = cursor
            result = await self.client.request(
                "POST",
                "/api/v2/logs/events/search",
                json={
                    "filter": _filter(window, filter),
                    "page": page,
                    "sort": "timestamp",
                },
            )
            for event in result.get("data", []):
                attrs = event["attributes"]
                row = dict(attrs.get("attributes", {}))
                row.update(
                    {
                        key: attrs.get(key)
                        for key in ("message", "service", "status", "host")
                    }
                )
                row["ts"] = (
                    datetime.fromisoformat(attrs["timestamp"].replace("Z", "+00:00"))
                    .astimezone(UTC)
                    .isoformat()
                )
                rows.append(row)
            cursor = result.get("meta", {}).get("page", {}).get("after")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        return LogSearchResult(
            rows=rows[:limit], truncated=len(rows) > limit or bool(cursor)
        )

    async def _aggregate(
        self,
        window: TimeWindow,
        filter: Filter,
        compute: dict[str, Any],
        groups: list[dict[str, Any]],
    ) -> Any:
        return await self.client.request(
            "POST",
            "/api/v2/logs/analytics/aggregate",
            json={
                "filter": _filter(window, filter),
                "compute": [compute],
                "group_by": groups,
            },
        )

    async def count(
        self,
        window: TimeWindow,
        filter: Filter,
        group_by: Sequence[str],
        target: str | None,
    ) -> list[GroupCount]:
        result = await self._aggregate(
            window,
            filter,
            {"aggregation": "count", "type": "total"},
            [
                {
                    "facet": _field(f),
                    "limit": 100 if i == 0 else 20,
                    "sort": {"aggregation": "count", "order": "desc"},
                }
                for i, f in enumerate(group_by)
            ],
        )
        counts = [
            GroupCount(
                group={k.removeprefix("@"): str(v) for k, v in b.get("by", {}).items()},
                count=int(b["computes"]["c0"]),
            )
            for b in result.get("data", {}).get("buckets", [])
        ]
        if any(
            len({b.group.get(f.removeprefix("@")) for b in counts})
            == (100 if i == 0 else 20)
            for i, f in enumerate(group_by)
        ):
            remainder = await self._remainder(
                window, filter, sum(b.count for b in counts)
            )
            counts.append(
                GroupCount(
                    group={
                        "__truncated__" if remainder is None else "__other__": "true"
                    },
                    count=0 if remainder is None else remainder,
                )
            )
        return counts

    async def _remainder(
        self, window: TimeWindow, filter: Filter, returned: int
    ) -> int | None:
        result = await self._aggregate(
            window, filter, {"aggregation": "count", "type": "total"}, []
        )
        buckets = result.get("data", {}).get("buckets", [])
        if not buckets or "c0" not in buckets[0].get("computes", {}):
            return None
        return int(buckets[0]["computes"]["c0"]) - returned

    async def top_values(
        self,
        window: TimeWindow,
        filter: Filter,
        field: str,
        limit: int,
        target: str | None,
    ) -> list[Bucket]:
        result = await self._aggregate(
            window,
            filter,
            {"aggregation": "count", "type": "total"},
            [
                {
                    "facet": _field(field),
                    "limit": limit,
                    "sort": {"aggregation": "count", "order": "desc"},
                }
            ],
        )
        buckets = [
            Bucket(value=str(b["by"][_field(field)]), count=int(b["computes"]["c0"]))
            for b in result.get("data", {}).get("buckets", [])
        ][:limit]
        if len(buckets) == limit:
            remainder = await self._remainder(
                window, filter, sum(b.count for b in buckets)
            )
            buckets.append(
                Bucket(
                    value="__truncated__" if remainder is None else "__other__",
                    count=0 if remainder is None else remainder,
                )
            )
        return buckets

    async def histogram(
        self,
        window: TimeWindow,
        filter: Filter,
        interval: timedelta,
        target: str | None,
    ) -> list[TimeBucket]:
        result = await self._aggregate(
            window,
            filter,
            {
                "aggregation": "count",
                "type": "timeseries",
                "interval": f"{int(interval.total_seconds())}s",
            },
            [],
        )
        return [
            TimeBucket(
                start=datetime.fromisoformat(p["time"].replace("Z", "+00:00")),
                count=int(p["value"]),
            )
            for b in result.get("data", {}).get("buckets", [])
            for p in b["computes"]["c0"]
        ]
