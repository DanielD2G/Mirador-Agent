from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from mirador.application.ports.capabilities.logs import LogsSource
from mirador.application.tooling.spec import (
    DataOutput,
    RowsOutput,
    ToolCallContext,
    ToolOutput,
    ToolSpec,
)

from ._common import FILTER, LIMIT, WINDOW, filter_value, model, spec, target, window


def logs_tools(src: LogsSource, targets: Sequence[str]) -> list[ToolSpec[Any]]:
    tools: list[ToolSpec[Any]] = []
    inputs = model("LogsSearch", targets, **WINDOW, filter=FILTER, limit=LIMIT)

    async def search(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.search(
            window(a), filter_value(a.filter), a.limit, target(a, targets)
        )
        return RowsOutput(
            kind="rows",
            rows=result.rows,
            truncated=result.truncated,
            table_hint=f"logs_{src.provider}",
        )

    tools.append(
        spec(
            f"logs.{src.provider}.search",
            "logs",
            inputs,
            search,
            (
                "Return search results; use to investigate logs. Prefer "
                "count/top_values/histogram before search. "
            ),
            hours=24,
            medium=True,
        )
    )
    inputs = model(
        "LogsCount", targets, **WINDOW, filter=FILTER, group_by=(list[str], [])
    )

    async def count(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.count(
            window(a), filter_value(a.filter), a.group_by, target(a, targets)
        )
        return DataOutput(
            kind="data", data=[item.model_dump(mode="json") for item in result]
        )

    tools.append(
        spec(
            f"logs.{src.provider}.count",
            "logs",
            inputs,
            count,
            (
                "Return count results; use to investigate logs. Prefer "
                "count/top_values/histogram before search. "
            ),
            hours=None,
            medium=False,
        )
    )
    inputs = model(
        "LogsTopValues", targets, **WINDOW, filter=FILTER, field=(str, ...), limit=LIMIT
    )

    async def top_values(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.top_values(
            window(a), filter_value(a.filter), a.field, a.limit, target(a, targets)
        )
        return DataOutput(
            kind="data", data=[item.model_dump(mode="json") for item in result]
        )

    tools.append(
        spec(
            f"logs.{src.provider}.top_values",
            "logs",
            inputs,
            top_values,
            (
                "Return top values results; use to investigate logs. Prefer "
                "count/top_values/histogram before search. "
            ),
            hours=None,
            medium=False,
        )
    )
    inputs = model(
        "LogsHistogram", targets, **WINDOW, filter=FILTER, interval=(timedelta, ...)
    )

    async def histogram(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.histogram(
            window(a), filter_value(a.filter), a.interval, target(a, targets)
        )
        return DataOutput(
            kind="data", data=[item.model_dump(mode="json") for item in result]
        )

    tools.append(
        spec(
            f"logs.{src.provider}.histogram",
            "logs",
            inputs,
            histogram,
            (
                "Return histogram results; use to investigate logs. Prefer "
                "count/top_values/histogram before search. "
            ),
            hours=None,
            medium=False,
        )
    )
    return tools
