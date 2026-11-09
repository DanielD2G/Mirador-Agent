from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, field_validator

from mirador.domain.investigation import (
    Finding,
    Hypothesis,
    Investigation,
    ToolCallRecord,
)
from mirador.domain.report import Report
from mirador.domain.time import ensure_utc
from mirador.domain.trigger import Trigger


class LlmCallRecord(BaseModel):
    """Typed data exchanged across the application boundary."""

    id: str
    investigation_id: str
    agent_name: str
    model: str
    started_at: datetime
    input_tokens: int
    output_tokens: int
    usd: Decimal
    request_text: str
    response_text: str

    _utc = field_validator("started_at")(ensure_utc)


class InvestigationStore(Protocol):
    """Adapter contract for InvestigationStore."""

    async def save_trigger(self, trigger: Trigger) -> None:
        """Persist the supplied record before returning."""
        ...

    async def save_investigation(self, investigation: Investigation) -> None:
        """Persist the supplied record before returning."""
        ...

    async def save_hypothesis(self, hypothesis: Hypothesis) -> None:
        """Persist the supplied record before returning."""
        ...

    async def save_tool_call(self, call: ToolCallRecord) -> None:
        """Persist the supplied record before returning."""
        ...

    async def save_finding(self, finding: Finding) -> None:
        """Persist the supplied record before returning."""
        ...

    async def save_report(self, report: Report) -> None:
        """Persist the supplied record before returning."""
        ...

    async def save_llm_call(self, call: LlmCallRecord) -> None:
        """Persist the supplied record before returning."""
        ...

    async def get_investigation(self, id: str) -> Investigation | None:
        """Return the requested data, preserving source identifiers."""
        ...

    async def get_report(self, investigation_id: str) -> Report | None:
        """Return the requested data, preserving source identifiers."""
        ...

    async def tool_calls(self, investigation_id: str) -> list[ToolCallRecord]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def findings(self, investigation_id: str) -> list[Finding]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def llm_calls(self, investigation_id: str) -> list[LlmCallRecord]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def monthly_spend(
        self, month: date, kind: Literal["investigation", "eval"]
    ) -> Decimal:
        """Return the requested data, preserving source identifiers."""
        ...

    async def add_spend(
        self, at: datetime, usd: Decimal, kind: Literal["investigation", "eval"]
    ) -> None:
        """Persist the supplied record before returning."""
        ...
