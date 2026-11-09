from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, Field

from mirador.domain.budget import Spend
from mirador.domain.investigation import HypothesisStatus, ToolCallStatus


class InvestigationStarted(BaseModel):
    """Typed data exchanged across the application boundary."""

    investigation_id: str
    trigger_text: str
    kind: Literal["investigation_started"] = "investigation_started"


class HypothesisOpened(BaseModel):
    """Typed data exchanged across the application boundary."""

    hypothesis_id: str
    statement: str
    kind: Literal["hypothesis_opened"] = "hypothesis_opened"


class HypothesisClosed(BaseModel):
    """Typed data exchanged across the application boundary."""

    hypothesis_id: str
    status: HypothesisStatus
    kind: Literal["hypothesis_closed"] = "hypothesis_closed"


class ToolCallStarted(BaseModel):
    """Typed data exchanged across the application boundary."""

    call_id: str
    tool: str
    params_preview: str
    kind: Literal["tool_call_started"] = "tool_call_started"


class ToolCallFinished(BaseModel):
    """Typed data exchanged across the application boundary."""

    call_id: str
    tool: str
    status: ToolCallStatus
    duration_ms: int
    cache_hit: bool
    kind: Literal["tool_call_finished"] = "tool_call_finished"


class FindingAdded(BaseModel):
    """Typed data exchanged across the application boundary."""

    claim: str
    kind: Literal["finding_added"] = "finding_added"


class BudgetWarningEvent(BaseModel):
    """Typed data exchanged across the application boundary."""

    spend: Spend
    kind: Literal["budget_warning"] = "budget_warning"


class BudgetExceededEvent(BaseModel):
    """Typed data exchanged across the application boundary."""

    reason: str
    kind: Literal["budget_exceeded"] = "budget_exceeded"


class ReportReady(BaseModel):
    """Typed data exchanged across the application boundary."""

    investigation_id: str
    kind: Literal["report_ready"] = "report_ready"


class WarningEvent(BaseModel):
    kind: Literal["warning"] = "warning"
    source: str
    message: str


Event = Annotated[
    InvestigationStarted
    | HypothesisOpened
    | HypothesisClosed
    | ToolCallStarted
    | ToolCallFinished
    | FindingAdded
    | BudgetWarningEvent
    | BudgetExceededEvent
    | ReportReady
    | WarningEvent,
    Field(discriminator="kind"),
]


class EventSink(Protocol):
    def emit(self, event: Event) -> None:
        """Deliver an event synchronously without performing blocking I/O."""
        ...
