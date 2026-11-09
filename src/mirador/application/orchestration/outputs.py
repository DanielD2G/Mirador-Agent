from typing import Literal

from pydantic import BaseModel

from mirador.application.tooling.spec import Capability
from mirador.domain.report import Confidence, EvidenceItem, RuledOut, TimelineEntry


class HypothesisDraft(BaseModel):
    statement: str
    rationale: str
    capabilities: list[Capability]


class Plan(BaseModel):
    hypotheses: list[HypothesisDraft]


class FindingDraft(BaseModel):
    claim: str
    tool_call_ids: list[str]
    confidence: Confidence


class SubagentVerdict(BaseModel):
    status: Literal["confirmed", "rejected", "inconclusive"]
    summary: str
    findings: list[FindingDraft]
    not_checked: list[str]


class CompletenessCheck(BaseModel):
    explains_all: bool
    unexplained: list[str]
    new_hypotheses: list[HypothesisDraft]


class ReportDraft(BaseModel):
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
