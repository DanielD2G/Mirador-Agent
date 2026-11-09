"""Datadog metric queries and active monitors."""

from datetime import UTC, datetime, timedelta

from mirador.application.ports.capabilities.metrics import (
    Alert,
    MetricsSource,
    Point,
    Series,
)
from mirador.application.ports.health import HealthCheckable, HealthResult
from mirador.domain.time import TimeWindow

from .client import DatadogClient


class DatadogMetrics(MetricsSource, HealthCheckable):
    provider = "datadog"
    query_syntax = "Datadog: avg:metric{tag:val} by {tag}; rollup(avg, seconds)."

    def __init__(self, client: DatadogClient) -> None:
        self.client = client

    async def health_check(self) -> HealthResult:
        return await self.client.health_check()

    async def query_range(
        self, expr: str, window: TimeWindow, step: timedelta, target: str | None
    ) -> list[Series]:
        result = await self.client.request(
            "GET",
            "/api/v1/query",
            params={
                "from": int(window.start.timestamp()),
                "to": int(window.end.timestamp()),
                "query": expr,
            },
        )
        return [
            Series(
                name=s.get("metric", expr),
                labels=dict(
                    tag.split(":", 1) if ":" in tag else (tag, "")
                    for tag in s.get("tag_set", [])
                ),
                points=[
                    Point(ts=datetime.fromtimestamp(ts / 1000, UTC), value=value)
                    for ts, value in s.get("pointlist", [])
                    if value is not None
                ],
            )
            for s in result.get("series", [])
        ]

    async def list_alerts(self, target: str | None) -> list[Alert]:
        result = await self.client.request(
            "GET", "/api/v1/monitor", params={"group_states": "alert,warn"}
        )
        return [
            Alert(
                name=m["name"],
                state=m["overall_state"],
                since=None,
                labels=dict(
                    tag.split(":", 1) if ":" in tag else (tag, "")
                    for tag in m.get("tags", [])
                ),
                summary=m.get("message", ""),
            )
            for m in result
        ]
