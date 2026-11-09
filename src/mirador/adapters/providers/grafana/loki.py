"""Loki search and server-side log aggregation."""

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from mirador.application.ports.capabilities.filter import Filter
from mirador.application.ports.capabilities.logs import (
    Bucket,
    GroupCount,
    LogSearchResult,
    LogsSource,
    TimeBucket,
)
from mirador.application.ports.health import HealthCheckable, HealthResult
from mirador.application.tooling.spec import ToolError
from mirador.domain.time import TimeWindow

from .client import GrafanaClient


def _field(name: str) -> str:
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
        raise ToolError("Invalid Loki field name", actionable=True, kind="invalid")
    return "service_name" if name == "service" else name


def to_native(filter: Filter) -> str:
    selectors = []
    stages = []
    has_positive_service = False
    for c in filter.clauses:
        value = json.dumps(c.value)
        field = _field(c.field) if c.field else None
        if field == "service_name" and c.op != "contains":
            has_positive_service |= c.op == "eq"
            selectors.append(f"{field}{'!=' if c.op == 'neq' else '='}{value}")
        elif field:
            if c.op == "contains":
                value = json.dumps(".*" + re.escape(c.value) + ".*")
            op = {"eq": "=", "neq": "!=", "contains": "=~"}[c.op]
            stages.append(f"| json | {field}{op}{value}")
        else:
            stages.append(f"|= {value}")
    if not has_positive_service:
        selectors.append('service_name=~".+"')
    return (
        "{"
        + ",".join(selectors or ['service_name=~".+"'])
        + "}"
        + (" " + " ".join(stages) if stages else "")
    )


class GrafanaLoki(LogsSource, HealthCheckable):
    provider = "grafana"

    def __init__(self, client: GrafanaClient, loki_uid: str) -> None:
        self.client = client
        self.uid = loki_uid

    async def health_check(self) -> HealthResult:
        return await self.client.datasource_health(self.uid)

    async def _query(self, query: str, window: TimeWindow, **params: Any) -> Any:
        instant = params.pop("instant", False)
        return await self.client.request(
            "GET",
            f"/api/datasources/proxy/uid/{quote(self.uid, safe='')}/loki/api/v1/"
            + ("query" if instant else "query_range"),
            params={
                "query": query,
                **(
                    {"time": window.end.timestamp()}
                    if instant
                    else {
                        "start": window.start.timestamp(),
                        "end": window.end.timestamp(),
                    }
                ),
                **params,
            },
        )

    async def search(
        self, window: TimeWindow, filter: Filter, limit: int, target: str | None
    ) -> LogSearchResult:
        result = await self._query(
            to_native(filter), window, direction="backward", limit=limit
        )
        rows = []
        for stream in result["data"]["result"]:
            for ts, line in stream["values"]:
                row = dict(stream["stream"])
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        row.update(parsed)
                except ValueError:
                    pass
                row.update(
                    ts=datetime.fromtimestamp(int(ts) / 1e9, UTC).isoformat(),
                    message=line,
                )
                rows.append(row)
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return LogSearchResult(
            rows=rows[:limit],
            truncated=len(rows) >= limit or bool(result.get("warnings")),
        )

    def _expr(self, window: TimeWindow, filter: Filter, groups: Sequence[str]) -> str:
        fields = ",".join(_field(f) for f in groups)
        selector = to_native(filter) + (
            " | json" if any(_field(f) != "service_name" for f in groups) else ""
        )
        duration = int((window.end - window.start).total_seconds() * 1000)
        return f"sum by ({fields}) (count_over_time({selector} [{duration}ms]))"

    async def count(
        self,
        window: TimeWindow,
        filter: Filter,
        group_by: Sequence[str],
        target: str | None,
    ) -> list[GroupCount]:
        result = await self._query(
            self._expr(window, filter, group_by), window, instant=True
        )
        rows = [
            GroupCount(
                group={f: s["metric"].get(_field(f), "") for f in group_by},
                count=int(float(s["value"][1])),
            )
            for s in result["data"]["result"]
        ]
        if result.get("warnings"):
            rows.append(GroupCount(group={"__truncated__": "true"}, count=0))
        return rows

    async def top_values(
        self,
        window: TimeWindow,
        filter: Filter,
        field: str,
        limit: int,
        target: str | None,
    ) -> list[Bucket]:
        result = await self._query(
            f"topk({limit}, {self._expr(window, filter, [field])})",
            window,
            instant=True,
        )
        rows = sorted(
            [
                Bucket(
                    value=s["metric"].get(_field(field), ""),
                    count=int(float(s["value"][1])),
                )
                for s in result["data"]["result"]
            ],
            key=lambda b: b.count,
            reverse=True,
        )[:limit]
        if len(rows) == limit or result.get("warnings"):
            rows.append(Bucket(value="__truncated__", count=0))
        return rows

    async def histogram(
        self,
        window: TimeWindow,
        filter: Filter,
        interval: timedelta,
        target: str | None,
    ) -> list[TimeBucket]:
        seconds = interval.total_seconds()
        query = f"sum(count_over_time({to_native(filter)} [{int(seconds * 1000)}ms]))"
        if window.start + interval > window.end:
            return []
        result = await self._query(
            query, window, start=(window.start + interval).timestamp(), step=seconds
        )
        return [
            TimeBucket(
                start=datetime.fromtimestamp(float(ts), UTC) - interval,
                count=int(float(v)),
            )
            for s in result["data"]["result"]
            for ts, v in s["values"]
        ]
