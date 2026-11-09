import asyncio
import json
import logging
import secrets
from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from pydantic import JsonValue, ValidationError

from mirador.application.orchestration.context import RunContext
from mirador.application.ports.clock import Clock
from mirador.application.ports.events import (
    BudgetWarningEvent,
    EventSink,
    ToolCallFinished,
    ToolCallStarted,
)
from mirador.application.ports.redaction import Redactor
from mirador.application.ports.store import InvestigationStore
from mirador.application.tooling.envelope import wrap_untrusted
from mirador.application.tooling.spec import (
    Capability,
    RowsOutput,
    ToolCallContext,
    ToolError,
    ToolSpec,
)
from mirador.domain.budget import BudgetExceeded
from mirador.domain.investigation import ToolCallRecord, ToolCallStatus
from mirador.domain.time import TimeWindow

logger = logging.getLogger(__name__)


class _RedactionFailure(Exception):
    pass


class BoundTool(Protocol):
    name: str
    description: str
    params_schema: dict[str, JsonValue]
    capability: Capability

    async def invoke(self, args_json: str, ctx: RunContext) -> str: ...


def clipped(value: JsonValue, limit: int = 2000) -> JsonValue:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        return [clipped(v, limit) for v in value]
    if isinstance(value, dict):
        return {k[:limit]: clipped(v, limit) for k, v in value.items()}
    return value


class ToolPipeline:
    def __init__(
        self,
        *,
        redactor: Redactor,
        store: InvestigationStore,
        events: EventSink,
        clock: Clock,
        enabled_sensitive: frozenset[str],
        quotas: Mapping[str, int],
        fixture_time: datetime | None = None,
    ) -> None:
        self.redactor, self.store, self.events, self.clock = (
            redactor,
            store,
            events,
            clock,
        )
        self.fixture_time = fixture_time
        self.enabled_sensitive = enabled_sensitive
        self.quotas = dict(quotas)
        if any(v <= 0 for v in quotas.values()):
            raise ValueError("Quotas must be positive")
        self._counts: dict[str, int] = defaultdict(int)
        self._cache: dict[str, dict[str, tuple[str, str | None, bool]]] = defaultdict(
            dict
        )
        self._quota: dict[str, deque[datetime]] = defaultdict(deque)
        self._specs: dict[str, ToolSpec[Any]] = {}

    def _redact_text(self, value: str) -> str:
        try:
            return self.redactor.redact_text(value)
        except Exception as exc:
            raise _RedactionFailure from exc

    def _redact_json(self, value: JsonValue) -> JsonValue:
        try:
            return self.redactor.redact_json(value)
        except Exception as exc:
            raise _RedactionFailure from exc

    @staticmethod
    def _key(name: str, params: dict[str, JsonValue], target: str | None) -> str:
        return json.dumps([name, params, target], sort_keys=True)

    def bind(self, spec: ToolSpec[Any]) -> BoundTool:
        self._specs[spec.name] = spec
        return _Bound(self, spec)

    def seed_cache(
        self,
        investigation_id: str,
        records: Sequence[ToolCallRecord],
        *,
        include_rows: bool,
    ) -> None:
        for r in records:
            if r.workspace_table is not None and not include_rows:
                continue
            spec = self._specs.get(r.tool)
            if r.status == "ok" and spec is not None and spec.cacheable:
                self._cache[investigation_id][self._key(r.tool, r.params, r.target)] = (
                    r.result_redacted,
                    r.workspace_table,
                    r.citable,
                )


class _Bound:
    def __init__(self, pipeline: ToolPipeline, spec: ToolSpec[Any]) -> None:
        self.pipeline, self.spec = pipeline, spec
        self.name, self.description, self.capability = (
            spec.name,
            spec.description,
            spec.capability,
        )
        self.params_schema = cast(
            dict[str, JsonValue], spec.input_model.model_json_schema()
        )

    async def invoke(self, args_json: str, ctx: RunContext) -> str:
        try:
            return await self._invoke(args_json, ctx)
        except BudgetExceeded:
            raise
        except Exception:
            logger.exception("Internal pipeline failure")
            return wrap_untrusted(
                '{"error":"upstream","message":"internal pipeline error"}',
                tool=self.name,
                call_id=f"tc_{self.pipeline._counts[ctx.investigation_id]:04d}",
                extra={"citable": str(self.spec.citable).lower()},
            )

    async def _invoke(self, args_json: str, ctx: RunContext) -> str:
        p, s = self.pipeline, self.spec
        p._counts[ctx.investigation_id] += 1
        call_id = f"tc_{p._counts[ctx.investigation_id]:04d}"
        now = p.clock.now()
        params: dict[str, JsonValue] = {}
        table = None
        target = None
        status: ToolCallStatus = "ok"
        hit = False
        citable = s.citable
        content = ""
        try:
            try:
                args = s.input_model.model_validate_json(args_json, strict=True)
            except ValidationError as exc:
                status = "validation_error"
                content = json.dumps(
                    {
                        "error": "invalid_arguments",
                        "details": json.loads(
                            exc.json(include_input=False, include_context=False)
                        )[:5],
                    }
                )
            if not content:
                params = args.model_dump(mode="json")
                target_value = params.get("target")
                target = target_value if isinstance(target_value, str) else None
                if s.verb == "read-sensitive" and s.name not in p.enabled_sensitive:
                    status = "denied"
                    content = json.dumps(
                        {
                            "error": "permission",
                            "message": "tool disabled by configuration",
                        }
                    )
            if not content:
                window = getattr(args, "window", None)
                if window is None and hasattr(args, "start") and hasattr(args, "end"):
                    window = TimeWindow(start=args.start, end=args.end)
                if (
                    isinstance(window, TimeWindow)
                    and s.limits.max_window is not None
                    and window.duration() > s.limits.max_window
                ):
                    raise ToolError(
                        "Maximum window is "
                        f"{s.limits.max_window.total_seconds() / 3600:g}:00:00",
                        actionable=True,
                        kind="invalid",
                    )
                ctx.budget.charge_tool_call(now)
                if ctx.budget.take_warning(now):
                    ctx.events.emit(BudgetWarningEvent(spend=ctx.budget.snapshot()))
                key = p._key(s.name, params, target)
                cached = (
                    p._cache[ctx.investigation_id].get(key) if s.cacheable else None
                )
                if cached is not None:
                    content, table, citable = cached
                    hit = True
                else:
                    p.events.emit(
                        ToolCallStarted(
                            call_id=call_id,
                            tool=s.name,
                            params_preview=p._redact_text(json.dumps(params)),
                        )
                    )
                    for attempt in range(3):
                        group = s.quota_group
                        if group is not None and group in p.quotas:
                            queue = p._quota[group]
                            while True:
                                current = p.clock.now()
                                while (
                                    queue and (current - queue[0]).total_seconds() >= 60
                                ):
                                    queue.popleft()
                                if len(queue) < p.quotas[group]:
                                    queue.append(current)
                                    break
                                delay = max(
                                    0.0, 60 - (current - queue[0]).total_seconds()
                                )
                                if (
                                    delay
                                    >= ctx.budget.remaining_time(
                                        current
                                    ).total_seconds()
                                ):
                                    raise ToolError(
                                        "Quota exceeds remaining time",
                                        actionable=False,
                                        kind="rate_limited",
                                    )
                                await asyncio.sleep(delay)
                        current = p.clock.now()
                        ctx.budget.check(current)
                        remaining = ctx.budget.remaining_time(current).total_seconds()
                        timeout = min(s.limits.timeout.total_seconds(), remaining)
                        try:
                            async with asyncio.timeout(timeout):
                                output = await s.handler(
                                    args,
                                    ToolCallContext(
                                        ctx.investigation_id,
                                        ctx.hypothesis_id,
                                        call_id,
                                        p.fixture_time or current,
                                        ctx.workspace,
                                    ),
                                )
                            break
                        except (ToolError, TimeoutError) as exc:
                            if isinstance(exc, TimeoutError) and timeout == remaining:
                                raise BudgetExceeded("time") from exc
                            if isinstance(exc, ToolError) and exc.kind not in {
                                "rate_limited",
                                "upstream",
                            }:
                                raise
                            delay = (0.5 if attempt == 0 else 1.5) + (
                                secrets.randbelow(101) / 1000
                            )
                            if (
                                attempt == 2
                                or delay
                                >= ctx.budget.remaining_time(
                                    p.clock.now()
                                ).total_seconds()
                            ):
                                raise
                            await asyncio.sleep(delay)
                    if output.citable is not None:
                        citable = output.citable
                    if isinstance(output, RowsOutput):
                        rows = cast(
                            list[dict[str, JsonValue]],
                            # A wide pre-clip bounds redaction cost (some rules are
                            # superlinear on long runs); redacting before the final
                            # clip keeps quoted secrets near the cut whole.
                            clipped(
                                p._redact_json(
                                    clipped(
                                        cast(
                                            JsonValue,
                                            output.rows[: s.limits.max_results],
                                        ),
                                        8000,
                                    )
                                )
                            ),
                        )
                        info = (
                            (
                                await ctx.workspace.load(
                                    output.table_hint, rows, call_id, s.name
                                )
                            )
                            if rows
                            else None
                        )
                        table = info.name if info else None
                        truncated = (
                            output.truncated
                            or len(output.rows) > len(rows)
                            or clipped(cast(JsonValue, output.rows)) != output.rows
                        )
                        payload: JsonValue
                        if len(rows) < ctx.inline_rows_threshold:
                            payload = {
                                "table": table,
                                "row_count": len(rows),
                                "columns": cast(
                                    JsonValue, info.columns if info else []
                                ),
                                "rows": cast(JsonValue, rows),
                                "truncated": truncated,
                            }
                        else:
                            summary: dict[str, JsonValue] = {}
                            for field in ("status", "service", "path"):
                                counts = Counter(
                                    str(r[field]) for r in rows if field in r
                                )
                                if counts:
                                    summary[field] = dict(counts.most_common(5))
                            timestamps = [str(r["ts"]) for r in rows if "ts" in r]
                            if timestamps:
                                summary["ts"] = {
                                    "min": min(timestamps),
                                    "max": max(timestamps),
                                }
                            payload = {
                                "table": table,
                                "row_count": len(rows),
                                "columns": cast(
                                    JsonValue, info.columns if info else []
                                ),
                                "summary": summary,
                                "sample": cast(JsonValue, rows[:5]),
                                "truncated": truncated,
                            }
                    else:
                        payload = p._redact_json(output.data)
                    content = json.dumps(payload)
                    if isinstance(output, RowsOutput) and isinstance(payload, dict):
                        while len(content) > s.limits.max_output_chars:
                            payload["truncated"] = True
                            samples = payload.get("rows", payload.get("sample"))
                            if isinstance(samples, list) and samples:
                                samples.pop()
                            elif payload.get("summary"):
                                payload["summary"] = {}
                            else:
                                break  # Preserve source metadata even for a tiny limit.
                            content = json.dumps(payload)
                    elif len(content) > s.limits.max_output_chars:
                        content = json.dumps(
                            {
                                "data": content[
                                    : max(0, (s.limits.max_output_chars - 100) // 6)
                                ],
                                "truncated": True,
                            }
                        )
                    if s.cacheable:
                        p._cache[ctx.investigation_id][key] = (content, table, citable)
        except BudgetExceeded:
            raise
        except _RedactionFailure:
            raise
        except Exception as exc:
            status = "error"
            if isinstance(exc, ToolError):
                error, message, actionable = exc.kind, exc.message, exc.actionable
            else:
                logger.exception("Tool pipeline failed: %s", s.name)
                error, actionable = "upstream", False
                message = (
                    f"timed out after {s.limits.timeout.total_seconds():g}s"
                    if isinstance(exc, TimeoutError)
                    else type(exc).__name__
                )
            content = json.dumps(
                {"error": error, "message": message, "actionable": actionable}
            )
        content = p._redact_text(content)
        try:
            duration = max(0, int((p.clock.now() - now).total_seconds() * 1000))
            safe_params = cast(dict[str, JsonValue], p._redact_json(params))
            await p.store.save_tool_call(
                ToolCallRecord(
                    id=call_id,
                    investigation_id=ctx.investigation_id,
                    hypothesis_id=ctx.hypothesis_id,
                    tool=s.name,
                    capability=s.capability,
                    params=safe_params,
                    target=p._redact_text(target) if target else None,
                    started_at=now,
                    duration_ms=duration,
                    status=status,
                    result_redacted=content,
                    workspace_table=table,
                    cache_hit=hit,
                    citable=citable,
                )
            )
            p.events.emit(
                ToolCallFinished(
                    call_id=call_id,
                    tool=s.name,
                    status=status,
                    duration_ms=duration,
                    cache_hit=hit,
                )
            )
        except BudgetExceeded:
            raise
        except Exception:
            logger.exception("Tool audit failed")
            content = (
                '{"error": "upstream", "message": "audit failed", "actionable": false}'
            )
        return wrap_untrusted(
            content,
            tool=s.name,
            call_id=call_id,
            extra={"citable": str(citable).lower()},
        )
