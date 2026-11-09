"""Synchronous event delivery and live investigation progress."""

from collections.abc import Sequence
from threading import Lock

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from mirador.application.ports.events import (
    BudgetExceededEvent,
    BudgetWarningEvent,
    Event,
    EventSink,
    FindingAdded,
    HypothesisClosed,
    HypothesisOpened,
    InvestigationStarted,
    ReportReady,
    ToolCallFinished,
    ToolCallStarted,
    WarningEvent,
)
from mirador.domain.budget import Spend


class ConsoleEventSink(EventSink):
    def __init__(self, console: Console, enabled: bool) -> None:
        self._lock = Lock()
        self._console = console
        self._enabled = enabled
        self._stderr = Console(stderr=True)
        self._live: Live | None = None
        self._title = "Investigation"
        self._hypotheses: dict[str, tuple[str, str]] = {}
        self._tools: dict[str, ToolCallStarted | ToolCallFinished] = {}
        self._findings: list[str] = []
        self._spend: Spend | None = None
        self._notice = ""

    def start(self) -> None:
        if self._enabled and self._live is None:
            self._live = Live(
                get_renderable=self._render,
                console=self._console,
                auto_refresh=True,
                refresh_per_second=8,
            )
            self._live.start()

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def emit(self, event: Event) -> None:
        with self._lock:
            self._emit(event)

    def _emit(self, event: Event) -> None:
        if isinstance(event, InvestigationStarted):
            self._title = (
                f"Investigation {event.investigation_id}: {event.trigger_text}"
            )
        elif isinstance(event, HypothesisOpened):
            self._hypotheses[event.hypothesis_id] = (event.statement, "open")
        elif isinstance(event, HypothesisClosed):
            statement = self._hypotheses.get(
                event.hypothesis_id, (event.hypothesis_id, "open")
            )[0]
            self._hypotheses[event.hypothesis_id] = (statement, event.status)
        elif isinstance(event, (ToolCallStarted, ToolCallFinished)):
            self._tools[event.call_id] = event
        elif isinstance(event, FindingAdded):
            self._findings.append(event.claim)
        elif isinstance(event, BudgetWarningEvent):
            self._spend = event.spend
            self._notice = "Budget warning: approaching limit"
            self._stderr.print(Text(self._notice, style="yellow"))
        elif isinstance(event, BudgetExceededEvent):
            self._notice = f"Budget exceeded: {event.reason}"
            self._stderr.print(Text(self._notice, style="red"))
        elif isinstance(event, WarningEvent):
            self._stderr.print(Text(f"{event.source}: {event.message}", style="yellow"))
        elif isinstance(event, ReportReady):
            self._notice = f"Report ready: {event.investigation_id}"

    def _render(self) -> Panel:
        with self._lock:
            title = self._title
            hypotheses = tuple(self._hypotheses.values())
            tools = tuple(self._tools.values())
            findings = tuple(self._findings)
            spend = self._spend
            notice = self._notice
        rows: list[Text | Spinner] = []
        for statement, status in hypotheses:
            rows.append(Text(f"[{status}] {statement}"))
        for tool in tools:
            if isinstance(tool, ToolCallStarted):
                rows.append(
                    Spinner("dots", text=Text(f"{tool.tool}: {tool.params_preview}"))
                )
            else:
                mark = "✓" if tool.status == "ok" else "✗"
                cache = " cache" if tool.cache_hit else ""
                rows.append(
                    Text(
                        f"{mark} {tool.tool} ({tool.status}) "
                        f"{tool.duration_ms} ms{cache}"
                    )
                )
        rows.extend(Text(f"Finding: {claim}") for claim in findings)
        if spend is None:
            rows.append(Text("Spend: awaiting budget snapshot"))
        else:
            rows.append(
                Text(
                    f"Spend: USD {spend.usd} | {spend.tokens} tokens | "
                    f"{spend.tool_calls} tool calls | "
                    f"{spend.elapsed.total_seconds():.1f}s"
                )
            )
        if notice:
            rows.append(Text(notice))
        return Panel(Group(*rows), title=Text(title))


class CompositeEventSink(EventSink):
    def __init__(self, sinks: Sequence[EventSink]) -> None:
        self._sinks = tuple(sinks)

    def emit(self, event: Event) -> None:
        for sink in self._sinks:
            sink.emit(event)


class RecordingEventSink(EventSink):
    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)
