from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Literal

from mirador.application.ports.clock import Clock
from mirador.application.ports.events import EventSink
from mirador.application.ports.redaction import Redactor
from mirador.application.ports.store import InvestigationStore
from mirador.application.ports.workspace import Workspace
from mirador.domain.budget import BudgetTracker


@dataclass
class RunContext:
    investigation_id: str
    hypothesis_id: str | None
    budget: BudgetTracker
    workspace: Workspace
    store: InvestigationStore
    events: EventSink
    clock: Clock
    redactor: Redactor
    spend_kind: Literal["investigation", "eval"]
    inline_rows_threshold: int

    def for_hypothesis(self, hypothesis_id: str) -> "RunContext":
        return replace(self, hypothesis_id=hypothesis_id)


current_run_context: ContextVar[RunContext | None] = ContextVar(
    "current_run_context", default=None
)
