"""Read-only Prometheus queries and Grafana alerts."""

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from mirador.application.ports.capabilities.metrics import (
    Alert,
    MetricsSource,
    Point,
    Series,
)
from mirador.application.ports.health import HealthCheckable, HealthResult
from mirador.domain.time import TimeWindow

from .client import GrafanaClient


class GrafanaPrometheus(MetricsSource, HealthCheckable):
    provider = "grafana"
    query_syntax = 'PromQL: rate(http_requests_total{service="api"}[5m])'

    def __init__(self, client: GrafanaClient, prometheus_uid: str) -> None:
        self.client = client
        self.uid = prometheus_uid

    async def health_check(self) -> HealthResult:
        return await self.client.datasource_health(self.uid)

    async def query_range(
        self, expr: str, window: TimeWindow, step: timedelta, target: str | None
    ) -> list[Series]:
        result = await self.client.request(
            "GET",
            f"/api/datasources/proxy/uid/{quote(self.uid, safe='')}/api/v1/query_range",
            params={
                "query": expr,
                "start": window.start.timestamp(),
                "end": window.end.timestamp(),
                "step": step.total_seconds(),
            },
        )
        return [
            Series(
                name=s["metric"].get("__name__", expr),
                labels=s["metric"],
                points=[
                    Point(ts=datetime.fromtimestamp(float(ts), UTC), value=float(v))
                    for ts, v in s["values"]
                ],
            )
            for s in result["data"]["result"]
        ]

    async def list_alerts(self, target: str | None) -> list[Alert]:
        result = await self.client.request(
            "GET", "/api/prometheus/grafana/api/v1/alerts"
        )
        return [
            Alert(
                name=a["labels"].get("alertname", ""),
                state=a["state"],
                since=datetime.fromisoformat(a["activeAt"].replace("Z", "+00:00"))
                if a.get("activeAt")
                else None,
                labels=a["labels"],
                summary=a.get("annotations", {}).get("summary", ""),
            )
            for a in result["data"]["alerts"]
        ]
