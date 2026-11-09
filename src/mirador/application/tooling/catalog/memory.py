from datetime import datetime
from typing import Any

from pydantic import Field

from mirador.application.ports.incident_memory import IncidentMemory
from mirador.application.tooling.spec import (
    DataOutput,
    ToolCallContext,
    ToolOutput,
    ToolSpec,
)

from ._common import model, spec


def memory_tools(mem: IncidentMemory) -> list[ToolSpec[Any]]:
    inputs = model(
        "MemoryRecall",
        [],
        query=(str, ...),
        services=(list[str], []),
        since=(datetime | None, None),
        until=(datetime | None, None),
        limit=(int, Field(default=10, ge=1, le=50)),
    )

    async def recall(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await mem.recall(a.query, a.services, a.since, a.until, a.limit)
        return DataOutput(
            kind="data", data=[item.model_dump(mode="json") for item in result]
        )

    return [
        spec(
            "memory.recall",
            "memory",
            inputs,
            recall,
            (
                "Recall prior incidents to guide investigation; historical memory "
                "is not evidence."
            ),
        )
    ]
