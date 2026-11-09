"""Explicitly enabled, read-sensitive ALB access log reader."""

import asyncio
import gzip
import shlex
from collections import Counter
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
from mirador.application.ports.health import HealthResult
from mirador.application.tooling.spec import ToolError
from mirador.domain.time import TimeWindow

from .session import AwsSessions


def parse_line(line: str) -> dict[str, Any]:
    fields = shlex.split(line)
    names = (
        "type",
        "timestamp",
        "elb",
        "client",
        "target",
        "request_processing_time",
        "target_processing_time",
        "response_processing_time",
        "elb_status_code",
        "target_status_code",
        "received_bytes",
        "sent_bytes",
        "request",
        "user_agent",
        "ssl_cipher",
        "ssl_protocol",
        "target_group_arn",
        "trace_id",
        "domain_name",
        "chosen_cert_arn",
        "matched_rule_priority",
        "request_creation_time",
        "actions_executed",
        "redirect_url",
        "error_reason",
    )
    if len(fields) < 17:
        raise ValueError("Incomplete ALB access log")
    return dict(zip(names, fields, strict=False))


def matches(row: dict[str, Any], filter: Filter) -> bool:
    for clause in filter.clauses:
        value = (
            str(row.get(clause.field, ""))
            if clause.field
            else " ".join(map(str, row.values()))
        )
        if clause.op == "eq" and value != clause.value:
            return False
        if clause.op == "neq" and value == clause.value:
            return False
        if clause.op == "contains" and clause.value not in value:
            return False
    return True


class AlbAccessLogs(LogsSource):
    provider = "aws_alb"

    def __init__(self, sessions: AwsSessions, bucket: str, prefix: str) -> None:
        self.sessions, self.bucket, self.prefix = sessions, bucket, prefix

    async def health_check(self) -> HealthResult:
        return await self.sessions.health_check()

    async def _read(
        self, window: TimeWindow, filter: Filter, target: str | None
    ) -> tuple[list[dict[str, Any]], bool]:
        config = self.sessions.resolve(target)
        async with self.sessions.client("sts", target) as sts:
            account = (await sts.get_caller_identity())["Account"]
        rows: list[dict[str, Any]] = []
        count = 0
        day = window.start.replace(hour=0, minute=0, second=0, microsecond=0)
        async with self.sessions.client("s3", target) as client:
            while day < window.end:
                prefix = (
                    f"{self.prefix.strip('/')}/AWSLogs/{account}/"
                    f"elasticloadbalancing/{config.region}/{day:%Y/%m/%d}/"
                ).lstrip("/")
                params: dict[str, Any] = {}
                while True:
                    page = await client.list_objects_v2(
                        Bucket=self.bucket, Prefix=prefix, **params
                    )
                    for item in page.get("Contents", []):
                        if not item["Key"].endswith(".log.gz"):
                            continue
                        if count == 50:
                            return rows, True
                        count += 1
                        obj = await client.get_object(
                            Bucket=self.bucket, Key=item["Key"]
                        )
                        async with obj["Body"] as body:
                            data = await body.read()
                        text = (await asyncio.to_thread(gzip.decompress, data)).decode(
                            "utf-8"
                        )
                        for line in text.splitlines():
                            try:
                                row = parse_line(line)
                                at = datetime.fromisoformat(
                                    row["timestamp"].replace("Z", "+00:00")
                                )
                            except ValueError as exc:
                                raise ToolError(
                                    "Malformed ALB access log",
                                    actionable=False,
                                    kind="upstream",
                                ) from exc
                            if window.start <= at < window.end and matches(row, filter):
                                rows.append(row)
                    if not page.get("IsTruncated"):
                        break
                    params["ContinuationToken"] = page["NextContinuationToken"]
                day += timedelta(days=1)
        return rows, False

    async def search(
        self, window: TimeWindow, filter: Filter, limit: int, target: str | None
    ) -> LogSearchResult:
        rows, truncated = await self._read(window, filter, target)
        return LogSearchResult(
            rows=rows[:limit], truncated=truncated or len(rows) > limit
        )

    async def count(
        self,
        window: TimeWindow,
        filter: Filter,
        group_by: Sequence[str],
        target: str | None,
    ) -> list[GroupCount]:
        rows, truncated = await self._read(window, filter, target)
        counts = Counter(tuple(str(r.get(f, "")) for f in group_by) for r in rows)
        result = [
            GroupCount(group=dict(zip(group_by, key, strict=True)), count=n)
            for key, n in counts.most_common(10000)
        ]
        if truncated or len(counts) > 10000:
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
        rows, truncated = await self._read(window, filter, target)
        counts = Counter(str(r.get(field, "")) for r in rows)
        top = counts.most_common(limit)
        result = [Bucket(value=k, count=n) for k, n in top]
        other = sum(counts.values()) - sum(n for _, n in top)
        if other:
            result.append(Bucket(value="__other__", count=other))
        if truncated:
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
        if seconds < 1:
            raise ToolError(
                "Interval must be positive", actionable=True, kind="invalid"
            )
        rows, truncated = await self._read(window, filter, target)
        if truncated:
            raise ToolError(
                "ALB histogram exceeds 50 objects; narrow window",
                actionable=True,
                kind="invalid",
            )
        counts = Counter(
            int(
                datetime.fromisoformat(
                    r["timestamp"].replace("Z", "+00:00")
                ).timestamp()
                // seconds
            )
            for r in rows
        )
        return [
            TimeBucket(start=datetime.fromtimestamp(k * seconds, UTC), count=n)
            for k, n in sorted(counts.items())
        ]
