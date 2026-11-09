from collections.abc import Sequence
from typing import Any

from mirador.application.ports.capabilities.code import CodeSource
from mirador.application.tooling.spec import (
    DataOutput,
    RowsOutput,
    ToolCallContext,
    ToolError,
    ToolOutput,
    ToolSpec,
)

from ._common import LIMIT, model, spec, target


def code_tools(src: CodeSource, repos: Sequence[str]) -> list[ToolSpec[Any]]:
    def selected_repo(args: Any) -> str:
        repo = target(args, repos)
        if repo is None:
            raise ToolError(
                "Select a repository with target. "
                f"Configured repositories: {list(repos)}",
                actionable=True,
                kind="invalid",
            )
        return repo

    tools: list[ToolSpec[Any]] = []
    inputs = model("CodeReadFile", repos, path=(str, ...), ref=(str, ...))

    async def read_file(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.read_file(selected_repo(a), a.path, a.ref)
        return DataOutput(kind="data", data=result.model_dump(mode="json"))

    tools.append(
        spec(
            f"code.{src.provider}.read_file",
            "code",
            inputs,
            read_file,
            "Return read file results; use to investigate code.",
            hours=None,
            medium=False,
        )
    )
    inputs = model("CodeSearch", repos, query=(str, ...), limit=LIMIT)

    async def search(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.search_code(selected_repo(a), a.query, a.limit)
        return RowsOutput(
            kind="rows",
            rows=[item.model_dump(mode="json") for item in result],
            truncated=len(result) >= a.limit,
            table_hint="code",
        )

    tools.append(
        spec(
            f"code.{src.provider}.search",
            "code",
            inputs,
            search,
            "Return search results; use to investigate code.",
            hours=None,
            medium=False,
        )
    )
    inputs = model("CodeDiff", repos, base=(str, ...), head=(str, ...))

    async def diff(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.diff(selected_repo(a), a.base, a.head)
        return DataOutput(kind="data", data=result.model_dump(mode="json"))

    tools.append(
        spec(
            f"code.{src.provider}.diff",
            "code",
            inputs,
            diff,
            "Return diff results; use to investigate code.",
            hours=None,
            medium=False,
        )
    )
    return tools
