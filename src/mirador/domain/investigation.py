from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator

from mirador.domain.budget import BudgetLimits, Spend
from mirador.domain.report import Confidence
from mirador.domain.time import ensure_utc

InvestigationStatus = Literal["running", "done", "partial", "failed"]
HypothesisStatus = Literal["open", "confirmed", "rejected", "inconclusive"]
ToolCallStatus = Literal["ok", "error", "denied", "budget", "validation_error"]


class Investigation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    trigger_id: str
    parent_id: str | None
    status: InvestigationStatus
    started_at: datetime
    finished_at: datetime | None
    limits: BudgetLimits
    spent: Spend | None
    models_used: dict[str, str]

    _utc = field_validator("started_at")(ensure_utc)

    @field_validator("finished_at")
    @classmethod
    def finished_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class Hypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    investigation_id: str
    statement: str
    status: HypothesisStatus
    agent_name: str


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    investigation_id: str
    hypothesis_id: str | None
    tool: str
    capability: str
    params: dict[str, JsonValue]
    target: str | None
    started_at: datetime
    duration_ms: int
    status: ToolCallStatus
    result_redacted: str
    workspace_table: str | None
    citable: bool = True
    cache_hit: bool

    _utc = field_validator("started_at")(ensure_utc)


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    investigation_id: str
    hypothesis_id: str | None
    claim: str
    evidence_tool_call_ids: tuple[str, ...]
    confidence: Confidence
