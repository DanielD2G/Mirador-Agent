from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, create_model, field_validator

from mirador.application.ports.capabilities.filter import (
    Filter,
    FilterSyntaxError,
    parse_filter,
)
from mirador.application.tooling.spec import (
    Capability,
    ToolCallContext,
    ToolError,
    ToolLimits,
    ToolOutput,
    ToolSpec,
)
from mirador.domain.time import TimeWindow, ensure_utc

FILTER_HELP = (
    "Clauses separated by AND: field:value, field!=value, field~value,"
    " or free text; quote values containing spaces with double quotes."
    " Examples: level:error; service:api AND level!=debug; "
    'message~"timed out".'
)


class Input(BaseModel):
    @field_validator("start", "end", "since", "until", check_fields=False)
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


def model(name: str, targets: Sequence[str], **fields: Any) -> type[Input]:
    if len(targets) > 1:
        fields["target"] = (str | None, None)
    return create_model(name, __base__=Input, **fields)


def target(args: BaseModel, targets: Sequence[str]) -> str | None:
    value = getattr(args, "target", None)
    if value is not None and value not in targets:
        raise ToolError("Unknown target", actionable=True, kind="invalid")
    return value if value is not None else (targets[0] if len(targets) == 1 else None)


def window(args: Any) -> TimeWindow:
    return TimeWindow(start=args.start, end=args.end)


def filter_value(text: str) -> Filter:
    try:
        return parse_filter(text)
    except FilterSyntaxError as exc:
        raise ToolError(str(exc), actionable=True, kind="invalid") from exc


def spec(
    name: str,
    capability: Capability,
    inputs: type[Input],
    handler: Callable[[Any, ToolCallContext], Awaitable[ToolOutput]],
    description: str,
    hours: int | None = None,
    medium: bool = False,
) -> ToolSpec[Any]:
    return ToolSpec(
        name=name,
        description=description,
        capability=capability,
        verb="read",
        input_model=inputs,
        limits=ToolLimits(
            max_window=timedelta(hours=hours) if hours else None,
            max_results=1000,
            timeout=timedelta(seconds=30),
        ),
        cost_hint="medium" if medium else "low",
        quota_group="-".join(reversed(name.split(".")[:2]))
        if name.count(".") == 2
        else None,
        cacheable=True,
        citable=capability not in ("docs", "memory"),
        handler=handler,
    )


WINDOW = {"start": (datetime, ...), "end": (datetime, ...)}
LIMIT = (int, Field(default=100, ge=1, le=1000))
FILTER = (str, Field(description=FILTER_HELP))
