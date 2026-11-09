from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, JsonValue

from mirador.application.ports.capabilities.logs import Row
from mirador.application.ports.workspace import Workspace

ToolVerb = Literal["read", "read-sensitive"]
Capability = Literal[
    "logs", "metrics", "changes", "code", "network", "docs", "memory", "workspace"
]
I = TypeVar("I", bound=BaseModel)  # noqa: E741


class ToolLimits(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_window: timedelta | None
    max_results: int
    timeout: timedelta
    max_output_chars: int = 20_000


class RowsOutput(BaseModel):
    citable: bool | None = None
    kind: Literal["rows"]
    rows: list[Row]
    truncated: bool
    table_hint: str


class DataOutput(BaseModel):
    citable: bool | None = None
    kind: Literal["data"]
    data: JsonValue


ToolOutput = RowsOutput | DataOutput


class ToolError(Exception):
    def __init__(
        self,
        message: str,
        *,
        actionable: bool,
        kind: Literal[
            "auth", "permission", "not_found", "rate_limited", "invalid", "upstream"
        ],
    ) -> None:
        super().__init__(message)
        self.message = message
        self.actionable = actionable
        self.kind = kind


@dataclass
class ToolCallContext:
    investigation_id: str
    hypothesis_id: str | None
    call_id: str
    now: datetime
    workspace: Workspace


@dataclass(frozen=True)
class ToolSpec[I: BaseModel]:
    name: str
    description: str
    capability: Capability
    verb: ToolVerb
    input_model: type[I]
    limits: ToolLimits
    cost_hint: Literal["low", "medium", "high"]
    quota_group: str | None
    cacheable: bool
    citable: bool
    handler: Callable[[I, ToolCallContext], Awaitable[ToolOutput]]
