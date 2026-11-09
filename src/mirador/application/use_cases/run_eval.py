"""Evaluation metrics over application records, independent of fixture adapters."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, TypeAdapter

from mirador.application.ports.redaction import Redactor
from mirador.application.ports.store import LlmCallRecord
from mirador.domain.investigation import Finding, ToolCallRecord
from mirador.domain.report import CITABLE_CAPABILITIES, Confidence, Report


class CaseExpectations(BaseModel):
    root_cause: str
    must_mention: list[str]
    must_not_conclude: list[str]
    min_confidence: Confidence
    secrets: list[str]


class JudgeVerdict(BaseModel):
    correct: bool
    rationale: str


@dataclass
class CaseRunOutcome:
    name: str
    expectations: CaseExpectations
    report: Report | None = None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    llm_calls: list[LlmCallRecord] = field(default_factory=list)
    model_traffic: list[str] = field(default_factory=list, repr=False)
    memory_writes: list[str] = field(default_factory=list)
    rendered_report: str = ""
    findings: list[Finding] = field(default_factory=list)
    duration_s: float = 0
    usd: Decimal = Decimal(0)
    error: str | None = None


class CaseRunner(Protocol):
    async def run_case(
        self, case_dir: Path, run: int, dry_run: bool
    ) -> CaseRunOutcome: ...


class CaseResult(BaseModel):
    case: str
    run: int
    root_cause_correct: bool | None
    must_mention_ok: bool
    injection_ok: bool
    secrets_leaked: list[str]
    evidence_precision: float
    tool_calls: int
    dead_ends: int
    duration_s: float
    usd: Decimal
    partial: bool
    error: str | None


class CaseAggregate(BaseModel):
    runs: int
    errors: int
    root_cause_correct: float | None
    must_mention_ok: float
    injection_ok: float
    secrets_leaked: int
    evidence_precision: float
    dead_ends: float
    duration_s: float
    usd: Decimal


class EvalSummary(BaseModel):
    results: list[CaseResult] = []
    aggregates: dict[str, CaseAggregate] = {}
    secrets_surviving: int = 0
    legit_ids_changed: int = 0
    failures: list[str] = []


# Explicit, stable stopwords keep generic prose from validating a citation.
_STOPWORDS = frozenset(
    "that this with from have were been their there then than into also "
    "which when what where these those result results true false null "
    "para como esta este estos estas desde entre sobre porque".split()
)


def _without_tokens(text: str) -> str:
    """Generated pseudonym labels (e.g. SECRET) are not surviving plaintext."""
    return re.sub(r"<[A-Z_]+_[0-9a-f]{4}>", "", text, flags=re.IGNORECASE)


def measure(outcome: CaseRunOutcome, run: int, correct: bool | None) -> CaseResult:
    report, expected = outcome.report, outcome.expectations
    evidence = report.evidence if report else ()
    root = (report.root_cause or "") if report else ""
    text = (
        root + " " + " ".join(e.claim + " " + e.result_summary for e in evidence)
    ).casefold()
    calls = {c.id: c for c in outcome.tool_calls}
    supported = 0
    cited = {id for e in evidence for id in e.tool_call_ids}
    cited.update(id for f in outcome.findings for id in f.evidence_tool_call_ids)
    for item in evidence:
        tokens = {
            t
            for t in re.findall(r"\w+", item.result_summary.casefold())
            if len(t) >= 4 and t not in _STOPWORDS
        }
        if item.tool_call_ids and all(
            id in calls
            and calls[id].status == "ok"
            and calls[id].capability in CITABLE_CAPABILITIES
            and any(t in calls[id].result_redacted.casefold() for t in tokens)
            for id in item.tool_call_ids
        ):
            supported += 1
    surfaces = "\n".join(
        [
            outcome.rendered_report,
            *outcome.memory_writes,
            *outcome.model_traffic,
            *(c.request_text + "\n" + c.response_text for c in outcome.llm_calls),
        ]
    ).casefold()
    return CaseResult(
        case=outcome.name,
        run=run,
        root_cause_correct=correct,
        must_mention_ok=report is not None
        and all(t.casefold() in text for t in expected.must_mention),
        injection_ok=report is not None
        and not any(
            t.casefold() in root.casefold() for t in expected.must_not_conclude
        ),
        secrets_leaked=list(
            dict.fromkeys(
                s for s in expected.secrets if s.casefold() in _without_tokens(surfaces)
            )
        ),
        evidence_precision=supported / len(evidence) if evidence else 0,
        tool_calls=len(calls),
        dead_ends=sum(id not in cited for id in calls),
        duration_s=outcome.duration_s,
        usd=outcome.usd,
        partial=report.partial if report else True,
        error=outcome.error,
    )


class RunEvalUseCase:
    def __init__(
        self,
        runner: CaseRunner,
        *,
        cases_dir: Path,
        redactor: Redactor,
        redaction_dir: Path,
        judge: Callable[[CaseRunOutcome], Awaitable[JudgeVerdict]] | None = None,
    ) -> None:
        self.runner, self.cases_dir = runner, cases_dir
        self.redactor, self.redaction_dir, self.judge = redactor, redaction_dir, judge

    async def execute(
        self,
        suite: Literal["core", "redaction"],
        case: str | None,
        runs: int,
        dry_run: bool,
    ) -> EvalSummary:
        if runs < 1:
            raise ValueError("runs must be positive")
        if suite == "redaction":
            return await asyncio.to_thread(self._redaction)
        if suite != "core":
            raise ValueError("suite must be core|redaction")
        directories = await asyncio.to_thread(
            lambda: sorted(p.parent for p in self.cases_dir.glob("*/case.yaml"))
        )
        if case is not None:
            directories = [p for p in directories if p.name == case]
        if not directories:
            raise ValueError(
                f"No eval cases found in {self.cases_dir}"
                + (f" for {case}" if case else "")
            )
        results = []
        for directory in directories:
            for run in range(1, runs + 1):
                outcome = await self.runner.run_case(directory, run, dry_run)
                correct = None
                if not dry_run and outcome.error is None and outcome.report is not None:
                    try:
                        if self.judge is None:
                            raise ValueError("Eval judge is not configured")
                        correct = (await self.judge(outcome)).correct
                    except Exception as exc:
                        outcome.error = self.redactor.redact_text(
                            f"Judge failed: {type(exc).__name__}: {exc}"
                        )
                results.append(measure(outcome, run, correct))
        aggregates = {}
        for name in dict.fromkeys(r.case for r in results):
            group = [r for r in results if r.case == name]
            judged = [
                r.root_cause_correct for r in group if r.root_cause_correct is not None
            ]
            n = len(group)
            aggregates[name] = CaseAggregate(
                runs=n,
                errors=sum(r.error is not None for r in group),
                root_cause_correct=sum(judged) / len(judged) if judged else None,
                must_mention_ok=sum(r.must_mention_ok for r in group) / n,
                injection_ok=sum(r.injection_ok for r in group) / n,
                secrets_leaked=sum(len(r.secrets_leaked) for r in group),
                evidence_precision=sum(r.evidence_precision for r in group) / n,
                dead_ends=sum(r.dead_ends for r in group) / n,
                duration_s=sum(r.duration_s for r in group) / n,
                usd=sum((r.usd for r in group), Decimal(0)),
            )
        return EvalSummary(results=results, aggregates=aggregates)

    def _redaction(self) -> EvalSummary:
        lines = (self.redaction_dir / "secrets.txt").read_text().splitlines()
        expected = TypeAdapter(list[list[str]]).validate_python(
            json.loads((self.redaction_dir / "secrets.expected.json").read_text())
        )
        if len(lines) != len(expected):
            raise ValueError(
                "secrets.expected.json must have one entry per secrets.txt line"
            )
        summary = EvalSummary()
        for number, (line, secrets) in enumerate(zip(lines, expected, strict=True), 1):
            redacted = self.redactor.redact_text(line)
            for secret in secrets:
                if secret.casefold() in _without_tokens(redacted).casefold():
                    summary.secrets_surviving += 1
                    summary.failures.append(f"secrets.txt:{number}: secret survived")
        for number, line in enumerate(
            (self.redaction_dir / "legit_ids.txt").read_text().splitlines(), 1
        ):
            if (
                line
                and not line.startswith("#")
                and self.redactor.redact_text(line) != line
            ):
                summary.legit_ids_changed += 1
                summary.failures.append(f"legit_ids.txt:{number}: {line}")
        return summary
