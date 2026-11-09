"""Isolated, serialized investigation databases."""

# SQL identifiers are quoted; model SQL is parser-validated on a locked sandbox.
# ruff: noqa: S608
import asyncio
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypeVar

import duckdb
from pydantic import JsonValue

from mirador.application.ports.workspace import (
    QueryResult,
    TableInfo,
    Workspace,
    WorkspaceFactory,
    WorkspaceQueryError,
)

T = TypeVar("T")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _copy(source: duckdb.DuckDBPyConnection, target: duckdb.DuckDBPyConnection) -> None:
    for (name,) in source.execute("SHOW TABLES").fetchall():
        if name == "_mirador_tables":
            continue
        schema = source.execute(f"DESCRIBE {_quote(name)}").fetchall()
        target.execute(
            f"CREATE TABLE {_quote(name)} ("
            + ", ".join(f"{_quote(c[0])} {c[1]}" for c in schema)
            + ")"
        )
        rows = source.execute(f"SELECT * FROM {_quote(name)}").fetchall()
        if rows:
            target.executemany(
                f"INSERT INTO {_quote(name)} VALUES ({','.join('?' for _ in schema)})",
                rows,
            )


class DuckDBWorkspace:
    def __init__(
        self, connection: duckdb.DuckDBPyConnection, path: Path, timeout: timedelta
    ) -> None:
        self._con = connection
        self._path = path
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._tables: dict[str, TableInfo] = {}

    async def _run(self, operation: Callable[[], T]) -> T:
        async with self._lock:
            task = asyncio.create_task(asyncio.to_thread(operation))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                self._con.interrupt()
                try:
                    await task
                except duckdb.Error:
                    pass
                raise

    async def load(
        self,
        name_hint: str,
        rows: Sequence[Mapping[str, JsonValue]],
        source_tool_call_id: str,
        source_tool: str,
    ) -> TableInfo:
        def operation() -> TableInfo:
            slug = re.sub("[^a-z0-9_]", "_", name_hint.lower()) or "table"
            name = f"{slug}_{len(self._tables) + 1}"
            columns = list(dict.fromkeys(key for row in rows for key in row))
            if not columns:
                raise ValueError("workspace loads require at least one column")
            types = []
            for column in columns:
                values = [r[column] for r in rows if r.get(column) is not None]
                if column == "ts":
                    kind = "TIMESTAMPTZ"
                elif values and all(type(v) is int for v in values):
                    kind = "BIGINT"
                elif values and all(type(v) in (int, float) for v in values):
                    kind = "DOUBLE"
                else:
                    kind = "VARCHAR"
                types.append(kind)
            self._con.execute("BEGIN")
            try:
                self._con.execute(
                    f"CREATE TABLE {_quote(name)} ("
                    + ", ".join(
                        f"{_quote(c)} {t}" for c, t in zip(columns, types, strict=True)
                    )
                    + ")"
                )
                if rows:
                    self._con.executemany(
                        f"INSERT INTO {_quote(name)} VALUES "
                        f"({','.join('?' for _ in columns)})",
                        [
                            [
                                json.dumps(v)
                                if isinstance(v, (dict, list))
                                else str(v)
                                if v is not None and t == "VARCHAR"
                                else v
                                for c, t in zip(columns, types, strict=True)
                                for v in [row.get(c)]
                            ]
                            for row in rows
                        ],
                    )
                info = TableInfo(
                    name=name,
                    columns=columns,
                    row_count=len(rows),
                    source_tool_call_id=source_tool_call_id,
                    source_tool=source_tool,
                )
                self._con.execute("COMMIT")
                self._tables[name] = info
                return info
            except Exception:
                self._con.execute("ROLLBACK")
                raise

        return await self._run(operation)

    async def tables(self) -> list[TableInfo]:
        return await self._run(
            lambda: [
                self._tables[name].model_copy(deep=True)
                for name in sorted(self._tables)
            ]
        )

    async def query(self, sql: str, limit: int) -> QueryResult:
        if limit < 0:
            raise WorkspaceQueryError("limit must be nonnegative")
        loop = asyncio.get_running_loop()

        def operation() -> QueryResult:
            try:
                statements = self._con.extract_statements(sql)
                if (
                    len(statements) != 1
                    or statements[0].type != duckdb.StatementType.SELECT
                ):
                    raise WorkspaceQueryError(
                        "only a single SELECT/WITH statement is allowed"
                    )
                if re.search(
                    r"_mirador_tables|duckdb_|pragma|\bquery(?:_table)?\s*\(",
                    sql,
                    re.IGNORECASE,
                ):
                    raise WorkspaceQueryError(
                        "internal workspace objects are not accessible"
                    )
                statement = statements[0].query.rstrip().rstrip(";")
                result = self._con.execute(
                    f"SELECT * FROM ({statement}) LIMIT {limit + 1}"
                )
                columns = [c[0] for c in result.description]
                rows = result.fetchall()[:limit]
                total = self._con.execute(
                    f"SELECT count(*) FROM ({statement})"
                ).fetchone()
                assert total is not None
                return QueryResult(
                    columns=columns,
                    rows=[
                        [v.isoformat() if isinstance(v, datetime) else v for v in row]
                        for row in rows
                    ],
                    total_rows=total[0],
                )
            except duckdb.Error as exc:
                raise WorkspaceQueryError(str(exc)) from exc

        async with self._lock:
            timer = loop.call_later(self._timeout.total_seconds(), self._con.interrupt)
            task = asyncio.create_task(asyncio.to_thread(operation))
            try:
                return await asyncio.shield(task)
            finally:
                if not task.done():
                    self._con.interrupt()
                    try:
                        await task
                    except WorkspaceQueryError:
                        pass
                timer.cancel()

    async def persist(self) -> None:
        def operation() -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.unlink(missing_ok=True)
            try:
                with duckdb.connect(str(temporary)) as target:
                    _copy(self._con, target)
                    target.execute(
                        "CREATE TABLE _mirador_tables (name VARCHAR, metadata VARCHAR)"
                    )
                    if self._tables:
                        target.executemany(
                            "INSERT INTO _mirador_tables VALUES (?, ?)",
                            [
                                (name, info.model_dump_json())
                                for name, info in self._tables.items()
                            ],
                        )
                temporary.replace(self._path)
            finally:
                temporary.unlink(missing_ok=True)

        await self._run(operation)

    async def close(self) -> None:
        await self._run(self._con.close)


class DuckDBWorkspaceFactory(WorkspaceFactory):
    def __init__(
        self,
        data_dir: Path,
        memory_limit_bytes: int,
        query_timeout: timedelta,
        retention: timedelta,
    ) -> None:
        self._directory = data_dir / "workspaces"
        self._memory = memory_limit_bytes
        self._timeout = query_timeout
        self._retention = retention

    def _path(self, investigation_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", investigation_id):
            raise ValueError("invalid investigation id")
        return self._directory / f"{investigation_id}.duckdb"

    def _connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(
            ":memory:",
            config={
                "enable_external_access": False,
                "memory_limit": f"{self._memory}B",
            },
        )
        con.execute("SET lock_configuration = true")
        return con

    async def create(self, investigation_id: str) -> Workspace:
        path = self._path(investigation_id)

        def operation() -> DuckDBWorkspace:
            con = self._connect()
            return DuckDBWorkspace(con, path, self._timeout)

        return await asyncio.to_thread(operation)

    async def reopen(
        self, investigation_id: str, *, as_investigation_id: str | None = None
    ) -> Workspace | None:
        path = self._path(investigation_id)

        def operation() -> Workspace | None:
            if not path.exists():
                return None
            con = self._connect()
            try:
                with duckdb.connect(str(path), read_only=True) as source:
                    metadata = {
                        name: TableInfo.model_validate_json(value)
                        for name, value in source.execute(
                            "SELECT name, metadata FROM _mirador_tables"
                        ).fetchall()
                    }
                    _copy(source, con)
            except Exception:
                con.close()
                raise
            workspace = DuckDBWorkspace(
                con, self._path(as_investigation_id or investigation_id), self._timeout
            )
            workspace._tables = metadata
            return workspace

        return await asyncio.to_thread(operation)

    async def purge_expired(self, now: datetime) -> int:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone aware")

        def operation() -> int:
            count = 0
            cutoff = (now - self._retention).timestamp()
            for path in self._directory.glob("*.duckdb"):
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    count += 1
            return count

        return await asyncio.to_thread(operation)
