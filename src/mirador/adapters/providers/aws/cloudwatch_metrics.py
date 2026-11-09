"""CloudWatch metric expressions and active alarms."""

import re
from datetime import timedelta
from typing import Any

from mirador.application.ports.capabilities.metrics import (
    Alert,
    MetricsSource,
    Point,
    Series,
)
from mirador.application.ports.health import HealthResult
from mirador.application.tooling.spec import ToolError
from mirador.domain.time import TimeWindow

from .session import AwsSessions


class CloudWatchMetrics(MetricsSource):
    provider = "aws"
    query_syntax = "Namespace:MetricName{Dim=Val}:Stat"

    def __init__(self, sessions: AwsSessions) -> None:
        self.sessions = sessions

    async def health_check(self) -> HealthResult:
        return await self.sessions.health_check()

    async def query_range(
        self, expr: str, window: TimeWindow, step: timedelta, target: str | None
    ) -> list[Series]:
        match = re.fullmatch(
            r"([^:{}]+):([^:{}]+)\{([^{}]*)\}:(Average|Sum|Minimum|Maximum|SampleCount|p\d+(?:\.\d+)?)",
            expr,
        )
        if not match or step.total_seconds() < 1:
            raise ToolError(
                f"Expected {self.query_syntax} and positive step",
                actionable=True,
                kind="invalid",
            )
        namespace, name, dimensions, stat = match.groups()
        labels = {}
        for part in dimensions.split(",") if dimensions else []:
            if "=" not in part:
                raise ToolError("Expected Dim=Val", actionable=True, kind="invalid")
            key, value = part.split("=", 1)
            labels[key.strip()] = value.strip()
        query = {
            "Id": "m1",
            "MetricStat": {
                "Metric": {
                    "Namespace": namespace,
                    "MetricName": name,
                    "Dimensions": [{"Name": k, "Value": v} for k, v in labels.items()],
                },
                "Period": int(step.total_seconds()),
                "Stat": stat,
            },
            "ReturnData": True,
        }
        points: list[Point] = []
        params: dict[str, Any] = {}
        async with self.sessions.client("cloudwatch", target) as client:
            while True:
                response = await client.get_metric_data(
                    MetricDataQueries=[query],
                    StartTime=window.start,
                    EndTime=window.end,
                    **params,
                )
                for row in response.get("MetricDataResults", []):
                    if row.get("StatusCode") not in {None, "Complete", "PartialData"}:
                        raise ToolError(
                            "CloudWatch metric query failed",
                            actionable=False,
                            kind="upstream",
                        )
                    points.extend(
                        Point(ts=t, value=v)
                        for t, v in zip(
                            row.get("Timestamps", []),
                            row.get("Values", []),
                            strict=True,
                        )
                    )
                if not response.get("NextToken"):
                    break
                params["NextToken"] = response["NextToken"]
        return [
            Series(name=name, labels=labels, points=sorted(points, key=lambda p: p.ts))
        ]

    async def list_alerts(self, target: str | None) -> list[Alert]:
        result = []
        params: dict[str, Any] = {}
        async with self.sessions.client("cloudwatch", target) as client:
            while True:
                response = await client.describe_alarms(StateValue="ALARM", **params)
                for row in response.get("MetricAlarms", []) + response.get(
                    "CompositeAlarms", []
                ):
                    result.append(
                        Alert(
                            name=row["AlarmName"],
                            state=row["StateValue"],
                            since=row.get("StateUpdatedTimestamp"),
                            labels={"arn": row["AlarmArn"]},
                            summary=row.get("StateReason", ""),
                        )
                    )
                if not response.get("NextToken"):
                    break
                params["NextToken"] = response["NextToken"]
        return result
