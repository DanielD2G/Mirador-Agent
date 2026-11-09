from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Literal

import aiosqlite
from pydantic import BaseModel

from mirador.application.ports.incident_memory import (
    EpisodeState,
    EpisodeStateRepository,
    EpisodeSummary,
)
from mirador.application.ports.store import InvestigationStore, LlmCallRecord
from mirador.domain.investigation import (
    Finding,
    Hypothesis,
    Investigation,
    ToolCallRecord,
)
from mirador.domain.report import Report
from mirador.domain.time import ensure_utc
from mirador.domain.trigger import Trigger


class SqliteStore(InvestigationStore, EpisodeStateRepository):
    """SQLite persistence with serialized access to the connection."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path) -> SqliteStore:
        connection = await aiosqlite.connect(str(path), isolation_level=None)
        try:
            await connection.execute("PRAGMA journal_mode=WAL")
            async with connection.execute(
                "SELECT name FROM sqlite_master WHERE name='schema_version'"
            ) as cursor:
                exists = await cursor.fetchone()
            if exists is None:
                schema = await asyncio.to_thread(
                    files(__package__).joinpath("schema.sql").read_text,
                    encoding="utf-8",
                )
                await connection.executescript(schema)
            async with connection.execute(
                "SELECT version FROM schema_version"
            ) as cursor:
                version = [tuple(row) for row in await cursor.fetchall()]
            if version == [(1,)]:
                # MVP dev-only migration: discard scoped audit records.
                await connection.executescript("""
                    BEGIN;
                    DROP TABLE tool_calls;
                    DROP TABLE hypotheses;
                    DROP TABLE findings;
                    CREATE TABLE tool_calls (
                        id TEXT NOT NULL, investigation_id TEXT NOT NULL,
                        started_at TEXT, body TEXT NOT NULL,
                        PRIMARY KEY (investigation_id, id));
                    CREATE TABLE hypotheses (
                        id TEXT NOT NULL, investigation_id TEXT NOT NULL,
                        started_at TEXT, body TEXT NOT NULL,
                        PRIMARY KEY (investigation_id, id));
                    CREATE TABLE findings (
                        id TEXT NOT NULL, investigation_id TEXT NOT NULL,
                        started_at TEXT, body TEXT NOT NULL,
                        PRIMARY KEY (investigation_id, id));
                    UPDATE schema_version SET version=2;
                    COMMIT;
                """)
                version = [(2,)]
            if version != [(2,)]:
                raise ValueError("Unsupported SQLite schema version")
            return cls(connection)
        except BaseException:
            await connection.close()
            raise

    async def close(self) -> None:
        async with self._lock:
            await self._connection.close()

    async def _save(self, table: str, key: str, value: str, record: BaseModel) -> None:
        columns = [key, "body"]
        values: list[str | None] = [value, record.model_dump_json()]
        if key != "investigation_id":
            columns.append("investigation_id")
            values.append(getattr(record, "investigation_id", None))
        columns.append("started_at")
        at = getattr(record, "started_at", getattr(record, "recorded_at", None))
        values.append(ensure_utc(at).isoformat() if at is not None else None)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column}=excluded.{column}" for column in columns[1:])
        conflict = (
            "investigation_id, id"
            if table in {"tool_calls", "hypotheses", "findings"}
            else key
        )
        # Table and column names come exclusively from adapter constants.
        query = (  # noqa: S608 — identifiers are internal constants
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "  # noqa: S608
            f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
        )
        async with self._lock:
            await self._connection.execute(query, values)

    async def _read(self, query: str, values: Sequence[str] = ()) -> list[str]:
        async with self._lock:
            async with self._connection.execute(query, values) as cursor:
                return [str(row[0]) for row in await cursor.fetchall()]

    async def save_trigger(self, trigger: Trigger) -> None:
        await self._save("triggers", "id", trigger.id, trigger)

    async def save_investigation(self, investigation: Investigation) -> None:
        await self._save("investigations", "id", investigation.id, investigation)

    async def save_hypothesis(self, hypothesis: Hypothesis) -> None:
        await self._save("hypotheses", "id", hypothesis.id, hypothesis)

    async def save_tool_call(self, call: ToolCallRecord) -> None:
        await self._save("tool_calls", "id", call.id, call)

    async def save_finding(self, finding: Finding) -> None:
        await self._save("findings", "id", finding.id, finding)

    async def save_report(self, report: Report) -> None:
        await self._save("reports", "investigation_id", report.investigation_id, report)

    async def save_llm_call(self, call: LlmCallRecord) -> None:
        await self._save("llm_calls", "id", call.id, call)

    async def save_episode(self, summary: EpisodeSummary) -> None:
        await self._save("memory_episodes", "id", summary.id, summary)

    async def get_investigation(self, id: str) -> Investigation | None:
        rows = await self._read("SELECT body FROM investigations WHERE id=?", (id,))
        return Investigation.model_validate_json(rows[0]) if rows else None

    async def get_report(self, investigation_id: str) -> Report | None:
        rows = await self._read(
            "SELECT body FROM reports WHERE investigation_id=?", (investigation_id,)
        )
        return Report.model_validate_json(rows[0]) if rows else None

    async def tool_calls(self, investigation_id: str) -> list[ToolCallRecord]:
        rows = await self._read(
            "SELECT body FROM tool_calls WHERE investigation_id=? "
            "ORDER BY started_at, rowid",
            (investigation_id,),
        )
        return [ToolCallRecord.model_validate_json(row) for row in rows]

    async def findings(self, investigation_id: str) -> list[Finding]:
        rows = await self._read(
            "SELECT body FROM findings WHERE investigation_id=? "
            "ORDER BY started_at, rowid",
            (investigation_id,),
        )
        return [Finding.model_validate_json(row) for row in rows]

    async def llm_calls(self, investigation_id: str) -> list[LlmCallRecord]:
        rows = await self._read(
            "SELECT body FROM llm_calls WHERE investigation_id=? "
            "ORDER BY started_at, rowid",
            (investigation_id,),
        )
        return [LlmCallRecord.model_validate_json(row) for row in rows]

    async def add_spend(
        self, at: datetime, usd: Decimal, kind: Literal["investigation", "eval"]
    ) -> None:
        at = ensure_utc(at)
        async with self._lock:
            await self._connection.execute(
                "INSERT INTO spend VALUES (?, ?, ?, ?)",
                (at.isoformat(), at.strftime("%Y-%m"), kind, str(usd)),
            )

    async def monthly_spend(
        self, month: date, kind: Literal["investigation", "eval"]
    ) -> Decimal:
        rows = await self._read(
            "SELECT usd FROM spend WHERE month=? AND kind=?",
            (month.strftime("%Y-%m"), kind),
        )
        return sum((Decimal(row) for row in rows), Decimal(0))

    async def episode_states(self, ids: Sequence[str]) -> dict[str, EpisodeState]:
        summaries = await self.list_episodes(None)
        wanted = set(ids)
        return {item.id: item.state for item in summaries if item.id in wanted}

    async def set_episode_state(self, id: str, state: EpisodeState) -> None:
        async with self._lock:
            async with self._connection.execute(
                "SELECT body FROM memory_episodes WHERE id=?", (id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise KeyError(id)
            summary = EpisodeSummary.model_validate_json(row[0])
            summary.state = state
            await self._connection.execute(
                "UPDATE memory_episodes SET body=? WHERE id=?",
                (summary.model_dump_json(), id),
            )

    async def list_episodes(self, since: datetime | None) -> list[EpisodeSummary]:
        query = "SELECT body FROM memory_episodes"
        values: tuple[str, ...] = ()
        if since is not None:
            query += " WHERE started_at>=?"
            values = (ensure_utc(since).isoformat(),)
        rows = await self._read(query + " ORDER BY started_at, rowid", values)
        return [EpisodeSummary.model_validate_json(row) for row in rows]
