from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from mirador.domain.time import ensure_utc

if TYPE_CHECKING:
    from mirador.domain.investigation import ToolCallRecord

Confidence = Literal["high", "medium", "low"]
CITABLE_CAPABILITIES: frozenset[str] = frozenset(
    {"logs", "metrics", "changes", "network", "code", "workspace"}
)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claim: str
    tool_call_ids: tuple[str, ...]
    source: str
    query: str
    result_summary: str
    link: str | None


class TimelineEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    at: datetime
    event: str

    _utc = field_validator("at")(ensure_utc)


class RuledOut(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis: str
    reason: str


class Report(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: Literal["1"] = "1"
    investigation_id: str
    root_cause: str | None
    confidence: Confidence | None
    evidence: tuple[EvidenceItem, ...]
    related_logs: tuple[str, ...]
    timeline: tuple[TimelineEntry, ...]
    impact: str
    actions_immediate: tuple[str, ...]
    actions_fix: tuple[str, ...]
    ruled_out: tuple[RuledOut, ...]
    not_checked: tuple[str, ...]
    context_used: tuple[str, ...]
    partial: bool
    cost_usd: Decimal


def enforce_evidence(report: Report, calls: Mapping[str, ToolCallRecord]) -> Report:
    evidence = tuple(
        item
        for item in report.evidence
        if item.tool_call_ids
        and all(
            call_id in calls
            and calls[call_id].status == "ok"
            and calls[call_id].citable
            and calls[call_id].capability in CITABLE_CAPABILITIES
            for call_id in item.tool_call_ids
        )
    )
    if not evidence:
        note = "Root cause could not be supported by evidence"
        return report.model_copy(
            update={
                "evidence": evidence,
                "root_cause": None,
                "confidence": None,
                "not_checked": report.not_checked
                + (() if note in report.not_checked else (note,)),
            }
        )
    confidence = report.confidence
    if len(evidence) < len(report.evidence):
        if confidence == "high":
            confidence = "medium"
        elif confidence == "medium":
            confidence = "low"
    return report.model_copy(update={"evidence": evidence, "confidence": confidence})
