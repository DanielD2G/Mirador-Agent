from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, JsonValue


class WorkspaceQueryError(ValueError):
    """Workspace SQL could not be executed."""


class TableInfo(BaseModel):
    """Typed data exchanged across the application boundary."""

    name: str
    columns: list[str]
    row_count: int
    source_tool_call_id: str
    source_tool: str


class QueryResult(BaseModel):
    """Typed data exchanged across the application boundary."""

    columns: list[str]
    rows: list[list[JsonValue]]
    total_rows: int


class Workspace(Protocol):
    """Adapter contract for Workspace."""

    async def load(
        self,
        name_hint: str,
        rows: Sequence[Mapping[str, JsonValue]],
        source_tool_call_id: str,
        source_tool: str,
    ) -> TableInfo:
        """Return the requested data, preserving source identifiers."""
        ...

    async def tables(self) -> list[TableInfo]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def query(self, sql: str, limit: int) -> QueryResult:
        """Respect the requested limit and return matching results."""
        ...

    async def persist(self) -> None:
        """Persist the supplied record before returning."""
        ...

    async def close(self) -> None:
        """Release workspace resources before returning."""
        ...


class WorkspaceFactory(Protocol):
    """Adapter contract for WorkspaceFactory."""

    async def create(self, investigation_id: str) -> Workspace:
        """Return the requested data, preserving source identifiers."""
        ...

    async def reopen(
        self, investigation_id: str, *, as_investigation_id: str | None = None
    ) -> Workspace | None:
        """Return the requested data, preserving source identifiers."""
        ...

    async def purge_expired(self, now: datetime) -> int:
        """Return the requested data, preserving source identifiers."""
        ...
