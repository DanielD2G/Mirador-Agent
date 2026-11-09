import re
from dataclasses import replace
from typing import Any

from mirador.application.ports.workspace import WorkspaceQueryError
from mirador.application.tooling.spec import (
    DataOutput,
    ToolCallContext,
    ToolError,
    ToolOutput,
    ToolSpec,
)

from ._common import model, spec


def workspace_tools(max_rows_returned: int) -> list[ToolSpec[Any]]:
    if max_rows_returned < 1:
        raise ValueError("max_rows_returned must be positive")

    async def tables(a: Any, ctx: ToolCallContext) -> ToolOutput:
        return DataOutput(
            kind="data",
            data=[
                item.model_dump(mode="json") for item in await ctx.workspace.tables()
            ],
        )

    async def sql(a: Any, ctx: ToolCallContext) -> ToolOutput:
        try:
            result = await ctx.workspace.query(a.sql, max_rows_returned)
        except WorkspaceQueryError as exc:
            raise ToolError(str(exc), actionable=True, kind="invalid") from exc
        result.rows = result.rows[:max_rows_returned]
        # Ignore SQL literals/comments so a fabricated constant cannot cite a source.
        identifiers = re.sub(r"'([^']|'')*'|--[^\n]*|/\*.*?\*/", " ", a.sql, flags=re.S)
        names = {
            token.casefold()
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", identifiers)
        }
        citable = any(t.name.casefold() in names for t in await ctx.workspace.tables())
        return DataOutput(
            kind="data", data=result.model_dump(mode="json"), citable=citable
        )

    return [
        replace(item, cacheable=False)
        for item in [
            spec(
                "workspace.tables",
                "workspace",
                model("WorkspaceTables", []),
                tables,
                "List loaded tables and columns before querying.",
            ),
            spec(
                "workspace.sql",
                "workspace",
                model("WorkspaceSql", [], sql=(str, ...)),
                sql,
                (
                    "single SELECT/WITH over tables listed by workspace.tables; JOIN "
                    "across sources by request_id/client_ip; regexp_matches() for "
                    "patterns"
                ),
            ),
        ]
    ]
