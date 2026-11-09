from typing import Any

from mirador.application.ports.capabilities.docs import DocsSource
from mirador.application.tooling.spec import (
    DataOutput,
    ToolCallContext,
    ToolOutput,
    ToolSpec,
)

from ._common import LIMIT, model, spec


def docs_tools(src: DocsSource) -> list[ToolSpec[Any]]:
    tools: list[ToolSpec[Any]] = []
    inputs = model("DocsSearch", [], query=(str, ...), limit=LIMIT)

    async def search(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.search(a.query, a.limit)
        return DataOutput(
            kind="data", data=[item.model_dump(mode="json") for item in result]
        )

    tools.append(
        spec(
            "docs.search",
            "docs",
            inputs,
            search,
            "Return search results; use to investigate docs.",
            hours=None,
            medium=False,
        )
    )
    inputs = model("DocsRead", [], path=(str, ...), heading=(str | None, None))

    async def read(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.read(a.path, a.heading)
        return DataOutput(kind="data", data=result.model_dump(mode="json"))

    tools.append(
        spec(
            "docs.read",
            "docs",
            inputs,
            read,
            "Return read results; use to investigate docs.",
            hours=None,
            medium=False,
        )
    )
    return tools
