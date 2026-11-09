"""CloudWatch Logs Insights read-only queries."""

import asyncio
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from mirador.application.ports.capabilities.filter import Filter
from mirador.application.ports.capabilities.logs import (
    Bucket,
    GroupCount,
    LogSearchResult,
    LogsSource,
    Row,
    TimeBucket,
)
from mirador.application.ports.health import HealthResult
from mirador.application.tooling.spec import ToolError
from mirador.domain.time import TimeWindow

from .session import AwsSessions


def field_name(value: str) -> str:
    if not re.fullmatch(r"@?[A-Za-z_][A-Za-z_0-9.]*", value):
        raise ToolError("Invalid Insights field", actionable=True, kind="invalid")
    return value


def to_native(filter: Filter) -> str:
    clauses = []
    for clause in filter.clauses:
        field = field_name(clause.field or "@message")
        if clause.op == "contains":
            value = (
                re.escape(clause.value)
                .replace("/", r"\/")
                .replace("\n", r"\n")
                .replace("\r", r"\r")
            )
            clauses.append(f"filter {field} like /{value}/")
        else:
            op = "=" if clause.op == "eq" else "!="
            clauses.append(f"filter {field} {op} {json.dumps(clause.value)}")
    return " | ".join(clauses)


class CloudWatchLogs(LogsSource):
    provider = "aws"

    def __init__(self, sessions: AwsSessions) -> None:
        self.sessions = sessions

    async def health_check(self) -> HealthResult:
        return await self.sessions.health_check()

    async def _query(
        self,
        window: TimeWindow,
        filter: Filter,
        suffix: str,
        target: str | None,
        limit: int = 10000,
    ) -> list[dict[str, str]]:
        config = self.sessions.resolve(target)
        services = [
            c.value for c in filter.clauses if c.field == "service" and c.op == "eq"
        ]
        groups = list(
            dict.fromkeys(
                g
                for service, values in config.log_groups.items()
                if not services or service in services
                for g in values
            )
        )
        if not groups or len(groups) > 50:
            raise ToolError(
                "Configure targets.aws[].log_groups by service "
                "(1 to 50 log groups per query)",
                actionable=True,
                kind="invalid",
            )
        if not 1 <= limit <= 10000:
            raise ToolError(
                "Insights limit must be 1..10000", actionable=True, kind="invalid"
            )
        query = " | ".join(part for part in [to_native(filter), suffix] if part)
        async with self.sessions.client("logs", target) as client:
            try:
                async with asyncio.timeout(15):
                    result = await client.start_query(
                        logGroupNames=groups,
                        startTime=int(window.start.timestamp()),
                        endTime=int(window.end.timestamp()),
                        queryString=query,
                        limit=limit,
                    )
                    query_id = result["queryId"]
                    delay = 0.1
                    while True:
                        result = await client.get_query_results(queryId=query_id)
                        if result["status"] == "Complete":
                            return [
                                {c["field"]: c["value"] for c in row}
                                for row in result["results"]
                            ]
                        if result["status"] not in {"Scheduled", "Running"}:
                            raise ToolError(
                                "Insights query failed",
                                actionable=False,
                                kind="upstream",
                            )
                        # Query results do not repeat the identifier.
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 1)
            except TimeoutError as exc:
                raise ToolError(
                    "Insights query timed out", actionable=False, kind="upstream"
                ) from exc

    async def search(
        self, window: TimeWindow, filter: Filter, limit: int, target: str | None
    ) -> LogSearchResult:
        if not 1 <= limit <= 10000:
            raise ToolError(
                "Insights limit must be 1..10000", actionable=True, kind="invalid"
            )
        rows = await self._query(
            window,
            filter,
            "fields @timestamp, @message, @logStream, @log | sort @timestamp desc",
            target,
            min(limit + 1, 10000),
        )
        normalized: list[Row] = []
        config = self.sessions.resolve(target)
        services = [
            c.value for c in filter.clauses if c.field == "service" and c.op == "eq"
        ]
        for raw in rows[:limit]:
            row: Row = {
                k: v
                for k, v in raw.items()
                if not k.startswith("@") and k not in {"ts", "message", "service"}
            }
            if "@timestamp" in raw:
                at = datetime.fromisoformat(raw["@timestamp"].replace("Z", "+00:00"))
                row["ts"] = (
                    at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
                ).isoformat()
            message = raw.get("@message", "")
            row["message"] = message
            group = raw.get("@log", "").split(":", 1)[-1]
            stream = raw.get("@logStream", "")
            matched = [
                service
                for service, groups in config.log_groups.items()
                if group in groups or stream in groups
            ]
            service = (
                matched[0]
                if len(matched) == 1
                else services[0]
                if len(services) == 1
                else next(iter(config.log_groups))
                if len(config.log_groups) == 1
                else None
            )
            if service:
                row["service"] = service
            try:
                parsed = json.loads(message)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    if (
                        key not in {"ts", "message", "service", "@ptr"}
                        and key not in row
                    ):
                        row[key] = value
            normalized.append(row)
        return LogSearchResult(
            rows=normalized, truncated=len(rows) > limit or len(rows) == 10000
        )

    async def count(
        self,
        window: TimeWindow,
        filter: Filter,
        group_by: Sequence[str],
        target: str | None,
    ) -> list[GroupCount]:
        fields = [field_name(f) for f in group_by]
        rows = await self._query(
            window,
            filter,
            "stats count(*) as c" + (" by " + ", ".join(fields) if fields else ""),
            target,
        )
        result = [
            GroupCount(group={f: r.get(f, "") for f in fields}, count=int(r["c"]))
            for r in rows
        ]
        if len(rows) == 10000:
            result.append(GroupCount(group={"__truncated__": "true"}, count=0))
        return result

    async def top_values(
        self,
        window: TimeWindow,
        filter: Filter,
        field: str,
        limit: int,
        target: str | None,
    ) -> list[Bucket]:
        if not 1 <= limit <= 10000:
            raise ToolError(
                "Insights limit must be 1..10000", actionable=True, kind="invalid"
            )
        rows = await self._query(
            window,
            filter,
            f"stats count(*) as c by {field_name(field)} "
            f"| sort c desc | limit {min(limit, 10000)}",
            target,
        )
        result = [Bucket(value=r.get(field, ""), count=int(r["c"])) for r in rows]
        if len(rows) == 10000:
            result.append(Bucket(value="__truncated__", count=0))
        return result

    async def histogram(
        self,
        window: TimeWindow,
        filter: Filter,
        interval: timedelta,
        target: str | None,
    ) -> list[TimeBucket]:
        seconds = interval.total_seconds()
        duration = None
        for unit, scale, cap in (
            ("d", 86400, None),
            ("h", 3600, 24),
            ("m", 60, 60),
            ("s", 1, 60),
        ):
            value = seconds / scale
            if value >= 1 and value.is_integer() and (cap is None or value <= cap):
                duration = f"{int(value)}{unit}"
                break
        if duration is None:
            raise ToolError(
                "Interval must be exact whole seconds (1..60), minutes (1..60), "
                "hours (1..24), or positive whole days",
                actionable=True,
                kind="invalid",
            )
        rows = await self._query(
            window, filter, f"stats count(*) as c by bin({duration}) as ts", target
        )
        if len(rows) == 10000:
            raise ToolError(
                "Histogram exceeds Insights limit; increase interval",
                actionable=True,
                kind="invalid",
            )
        return [
            TimeBucket(
                start=datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).replace(
                    tzinfo=UTC
                ),
                count=int(r["c"]),
            )
            for r in rows
        ]
