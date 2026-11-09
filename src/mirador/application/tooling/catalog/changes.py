from collections.abc import Sequence
from typing import Any

from mirador.application.ports.capabilities.changes import ChangesSource
from mirador.application.tooling.spec import (
    RowsOutput,
    ToolCallContext,
    ToolOutput,
    ToolSpec,
)

from ._common import LIMIT, WINDOW, model, spec, target, window


def changes_tools(src: ChangesSource, targets: Sequence[str]) -> list[ToolSpec[Any]]:
    tools: list[ToolSpec[Any]] = []
    inputs = model(
        "ChangesRecent", targets, **WINDOW, service=(str | None, None), limit=LIMIT
    )

    async def recent(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.recent_changes(
            window(a), a.service, target(a, targets), a.limit
        )
        return RowsOutput(
            kind="rows",
            rows=[item.model_dump(mode="json") for item in result],
            truncated=len(result) >= a.limit,
            table_hint="changes",
        )

    tools.append(
        spec(
            f"changes.{src.provider}.recent",
            "changes",
            inputs,
            recent,
            "Return recent results; use to investigate changes.",
            hours=72,
            medium=False,
        )
    )
    return tools
