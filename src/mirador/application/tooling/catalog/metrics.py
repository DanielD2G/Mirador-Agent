from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from mirador.application.ports.capabilities.metrics import MetricsSource
from mirador.application.tooling.spec import (
    DataOutput,
    ToolCallContext,
    ToolOutput,
    ToolSpec,
)

from ._common import WINDOW, model, spec, target, window


def metrics_tools(src: MetricsSource, targets: Sequence[str]) -> list[ToolSpec[Any]]:
    tools: list[ToolSpec[Any]] = []
    inputs = model(
        "MetricsQueryRange", targets, **WINDOW, expr=(str, ...), step=(timedelta, ...)
    )

    async def query_range(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.query_range(a.expr, window(a), a.step, target(a, targets))
        return DataOutput(
            kind="data", data=[item.model_dump(mode="json") for item in result]
        )

    tools.append(
        spec(
            f"metrics.{src.provider}.query_range",
            "metrics",
            inputs,
            query_range,
            "Return query range results; use to investigate metrics."
            + src.query_syntax,
            hours=24,
            medium=False,
        )
    )
    inputs = model("MetricsAlerts", targets)

    async def alerts(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.list_alerts(target(a, targets))
        return DataOutput(
            kind="data", data=[item.model_dump(mode="json") for item in result]
        )

    tools.append(
        spec(
            f"metrics.{src.provider}.alerts",
            "metrics",
            inputs,
            alerts,
            "Return alerts results; use to investigate metrics." + src.query_syntax,
            hours=None,
            medium=False,
        )
    )
    return tools
