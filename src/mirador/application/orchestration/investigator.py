"""Code-owned investigation lifecycle and bounded concurrent hypothesis testing."""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from mirador.application.config import MiradorConfig
from mirador.application.orchestration.context import RunContext, current_run_context
from mirador.application.orchestration.outputs import (
    CompletenessCheck,
    HypothesisDraft,
    Plan,
    ReportDraft,
    SubagentVerdict,
)
from mirador.application.ports.agent_runtime import (
    AgentRunError,
    AgentRuntime,
    AgentSpec,
)
from mirador.application.ports.capabilities.docs import DocsSource
from mirador.application.ports.clock import Clock
from mirador.application.ports.events import (
    BudgetExceededEvent,
    BudgetWarningEvent,
    EventSink,
    FindingAdded,
    HypothesisClosed,
    HypothesisOpened,
    InvestigationStarted,
    ReportReady,
    WarningEvent,
)
from mirador.application.ports.incident_memory import IncidentEpisode, IncidentMemory
from mirador.application.ports.redaction import Redactor
from mirador.application.ports.store import InvestigationStore
from mirador.application.ports.workspace import WorkspaceFactory
from mirador.application.prompts import load_prompt
from mirador.application.tooling.envelope import wrap_untrusted
from mirador.application.tooling.pipeline import ToolPipeline
from mirador.application.tooling.registry import ToolRegistry
from mirador.application.tooling.spec import Capability
from mirador.domain.budget import BudgetExceeded, BudgetTracker
from mirador.domain.investigation import Finding, Hypothesis, Investigation
from mirador.domain.report import EvidenceItem, Report, enforce_evidence
from mirador.domain.time import TimeWindow
from mirador.domain.trigger import Trigger


class Investigator:
    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        registry: ToolRegistry,
        pipeline: ToolPipeline,
        memory: IncidentMemory,
        docs: DocsSource | None,
        store: InvestigationStore,
        events: EventSink,
        clock: Clock,
        redactor: Redactor,
        workspaces: WorkspaceFactory,
        config: MiradorConfig,
        fixture_time: datetime | None = None,
    ) -> None:
        self.runtime = runtime
        self.registry = registry
        self.pipeline = pipeline
        self.memory = memory
        self.docs = docs
        self.store = store
        self.events = events
        self.clock = clock
        self.redactor = redactor
        self.workspaces = workspaces
        self.config = config
        self.fixture_time = fixture_time

    async def _agent[T: BaseModel](
        self,
        ctx: RunContext,
        name: Literal["plan", "subagent", "completeness", "reporter"],
        output: type[T],
        data: str,
        capabilities: list[Capability],
    ) -> T:
        ctx.budget.check(self.clock.now())
        token = current_run_context.set(ctx)
        try:
            async with asyncio.timeout(
                ctx.budget.remaining_time(self.clock.now()).total_seconds()
            ):
                return await self.runtime.run(
                    AgentSpec(
                        name=name,
                        role="subagent"
                        if name == "subagent"
                        else "reporter"
                        if name == "reporter"
                        else "orchestrator",
                        instructions=load_prompt(name),
                        tools=self.registry.bound(self.pipeline, capabilities),
                        output_type=output,
                        max_turns=20 if name == "subagent" else 12,
                    ),
                    f"current_time_utc: {self.clock.now().isoformat()}\n" + data,
                    ctx,
                )
        except TimeoutError as exc:
            raise BudgetExceeded("time") from exc
        finally:
            if ctx.budget.take_warning(self.clock.now()):
                ctx.events.emit(BudgetWarningEvent(spend=ctx.budget.snapshot()))
            current_run_context.reset(token)

    async def run(
        self,
        trigger: Trigger,
        *,
        parent_id: str | None,
        spend_kind: Literal["investigation", "eval"],
        investigation_id: str | None = None,
    ) -> Report:
        now = self.clock.now()
        id = investigation_id or str(uuid4())
        limits = self.config.budgets.per_investigation
        monthly = (
            self.config.budgets.eval.monthly_usd
            if spend_kind == "eval"
            else self.config.budgets.monthly_usd
        )
        remaining = monthly - await self.store.monthly_spend(
            now.date().replace(day=1), spend_kind
        )
        if remaining <= 0:
            raise BudgetExceeded("monthly_usd")
        limits = limits.model_copy(update={"usd": min(limits.usd, remaining)})
        budget = BudgetTracker(limits, now)
        workspace = (
            await self.workspaces.reopen(parent_id, as_investigation_id=id)
            if parent_id
            else None
        )
        reopened = workspace is not None
        if workspace is None:
            workspace = await self.workspaces.create(id)
        ctx = RunContext(
            id,
            None,
            budget,
            workspace,
            self.store,
            self.events,
            self.clock,
            self.redactor,
            spend_kind,
            self.config.workspace.inline_rows_threshold,
        )
        investigation = Investigation(
            id=id,
            trigger_id=trigger.id,
            parent_id=parent_id,
            status="running",
            started_at=now,
            finished_at=None,
            limits=limits,
            spent=None,
            models_used={
                "orchestrator": self.config.models.orchestrator[0],
                "subagent": self.config.models.subagent[0],
                "reporter": self.config.models.reporter[0],
            },
        )
        window_now = self.fixture_time or now
        window = trigger.window or TimeWindow(
            start=window_now - self.config.investigation.changes_window, end=window_now
        )
        context: list[str] = [
            trigger.model_dump_json(),
            f"Investigation window: [{window.start.isoformat()}, "
            f"{window.end.isoformat()}]",
        ]
        hypotheses: list[Hypothesis] = []
        verdicts: list[SubagentVerdict] = []
        findings: list[Finding] = []
        unchecked: list[str] = []
        partial = False
        cancelled = False
        failed = False
        model_unavailable = False
        self.registry.bound(self.pipeline)

        async def recall(name: str, args: dict[str, object]) -> None:
            if name not in self.registry.names():
                return
            try:
                token = current_run_context.set(ctx)
                try:
                    context.append(
                        await self.registry.bound_by_name(self.pipeline, name).invoke(
                            json.dumps(args), ctx
                        )
                    )
                finally:
                    current_run_context.reset(token)
            except BudgetExceeded:
                raise
            except Exception as exc:
                self.events.emit(
                    WarningEvent(
                        source=name, message=self.redactor.redact_text(str(exc))
                    )
                )

        def payload() -> str:
            return wrap_untrusted(
                self.redactor.redact_text(
                    json.dumps(
                        {
                            "context": context,
                            "hypotheses": [h.model_dump() for h in hypotheses],
                            "verdicts": [v.model_dump() for v in verdicts],
                            "findings": [f.model_dump() for f in findings],
                            "not_checked": unchecked,
                        }
                    )
                ),
                tool="investigation",
                call_id=id,
            )

        async def test(draft: HypothesisDraft) -> None:
            h = Hypothesis(
                id=str(uuid4()),
                investigation_id=id,
                statement=draft.statement,
                status="open",
                agent_name="subagent",
            )
            hypotheses.append(h)
            await self.store.save_hypothesis(h)
            self.events.emit(
                HypothesisOpened(hypothesis_id=h.id, statement=h.statement)
            )
            try:
                verdict = await self._agent(
                    ctx.for_hypothesis(h.id),
                    "subagent",
                    SubagentVerdict,
                    payload()
                    + wrap_untrusted(
                        draft.model_dump_json(), tool="hypothesis", call_id=h.id
                    ),
                    list(dict.fromkeys([*draft.capabilities, "workspace", "docs"])),
                )
            except AgentRunError as exc:
                verdict = SubagentVerdict(
                    status="inconclusive",
                    summary=self.redactor.redact_text(str(exc)),
                    findings=[],
                    not_checked=[h.statement],
                )
            verdicts.append(verdict)
            unchecked.extend(verdict.not_checked)
            calls = {c.id for c in await self.store.tool_calls(id)}
            for item in verdict.findings:
                if not item.tool_call_ids or any(
                    c not in calls for c in item.tool_call_ids
                ):
                    continue
                finding = Finding(
                    id=str(uuid4()),
                    investigation_id=id,
                    hypothesis_id=h.id,
                    claim=self.redactor.redact_text(item.claim),
                    evidence_tool_call_ids=tuple(item.tool_call_ids),
                    confidence=item.confidence,
                )
                findings.append(finding)
                await self.store.save_finding(finding)
                self.events.emit(FindingAdded(claim=finding.claim))
            closed = h.model_copy(update={"status": verdict.status})
            hypotheses[hypotheses.index(h)] = closed
            await self.store.save_hypothesis(closed)
            self.events.emit(HypothesisClosed(hypothesis_id=h.id, status=closed.status))

        async def fanout(drafts: list[HypothesisDraft]) -> None:
            tasks: list[asyncio.Task[None]] = []

            async def guarded(draft: HypothesisDraft) -> None:
                try:
                    await test(draft)
                except BudgetExceeded:
                    for other in tasks:
                        if other is not asyncio.current_task() and not other.done():
                            other.cancel()
                    raise

            tasks = [asyncio.create_task(guarded(d)) for d in drafts]
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                results.sort(key=lambda result: not isinstance(result, BudgetExceeded))
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        async def finalize() -> Report:
            nonlocal partial
            unchecked.extend(h.statement for h in hypotheses if h.status == "open")
            calls = {c.id: c for c in await self.store.tool_calls(id)}
            context.extend(
                wrap_untrusted(
                    json.dumps(
                        {
                            "tool": c.tool,
                            "params": c.params,
                            "target": c.target,
                            "status": c.status,
                            "result": c.result_redacted,
                        },
                        default=str,
                    ),
                    tool=c.tool,
                    call_id=c.id,
                    extra={
                        "citable": "true"
                        if c.status == "ok"
                        and c.citable
                        and c.capability
                        in {
                            "logs",
                            "metrics",
                            "changes",
                            "network",
                            "code",
                            "workspace",
                        }
                        else "false"
                    },
                )
                for c in calls.values()
            )
            draft_report: ReportDraft | None = None
            if not cancelled and not model_unavailable and not failed:
                try:
                    draft_report = await self._agent(
                        ctx, "reporter", ReportDraft, payload(), []
                    )
                except (BudgetExceeded, AgentRunError) as exc:
                    partial = True
                    unchecked.append(
                        f"budget exhausted: {exc.reason}"
                        if isinstance(exc, BudgetExceeded)
                        else f"model unavailable: {str(exc)[:160]}"
                    )
            evidence = tuple(
                EvidenceItem(
                    claim=f.claim,
                    tool_call_ids=f.evidence_tool_call_ids,
                    source=calls[f.evidence_tool_call_ids[0]].tool,
                    query=json.dumps(calls[f.evidence_tool_call_ids[0]].params),
                    result_summary=calls[f.evidence_tool_call_ids[0]].result_redacted,
                    link=None,
                )
                for f in findings
            )
            fallback = ReportDraft(
                root_cause=None,
                confidence=None,
                evidence=evidence,
                related_logs=(),
                timeline=(),
                impact="Unknown",
                actions_immediate=(),
                actions_fix=(),
                ruled_out=(),
                not_checked=(),
                context_used=(),
            )
            used: list[str] = []

            def collect(value: object) -> None:
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == "path" and isinstance(item, str):
                            used.append(item)
                        elif key == "episode_ids" and isinstance(item, list):
                            used.extend(x for x in item if isinstance(x, str))
                        else:
                            collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)

            for call in calls.values():
                if call.capability in {"docs", "memory"}:
                    try:
                        collect(json.loads(call.result_redacted))
                    except ValueError:
                        pass
            chosen = (draft_report or fallback).model_copy(
                update={"context_used": tuple(dict.fromkeys(used))}
            )
            report = Report(
                **chosen.model_dump(),
                investigation_id=id,
                partial=partial,
                cost_usd=budget.snapshot().usd,
            )
            report = report.model_copy(
                update={
                    "evidence": tuple(
                        item.model_copy(
                            update={
                                "source": "; ".join(
                                    calls[c].tool
                                    for c in item.tool_call_ids
                                    if c in calls
                                ),
                                "query": (
                                    json.dumps(calls[item.tool_call_ids[0]].params)
                                    if len(item.tool_call_ids) == 1
                                    and item.tool_call_ids[0] in calls
                                    else json.dumps(
                                        {
                                            c: calls[c].params
                                            for c in item.tool_call_ids
                                            if c in calls
                                        }
                                    )
                                ),
                            }
                        )
                        for item in report.evidence
                    )
                }
            )
            report = enforce_evidence(
                report.model_copy(
                    update={
                        "not_checked": tuple(
                            dict.fromkeys([*report.not_checked, *unchecked])
                        )
                    }
                ),
                calls,
            )
            if report.root_cause and not cancelled:
                token = current_run_context.set(ctx)
                try:
                    redact = self.redactor.redact_text
                    ctx.budget.check(self.clock.now())
                    async with asyncio.timeout(
                        budget.remaining_time(self.clock.now()).total_seconds()
                    ):
                        await self.memory.record(
                            IncidentEpisode(
                                investigation_id=redact(id),
                                recorded_at=self.clock.now(),
                                services=[redact(trigger.service)]
                                if trigger.service
                                else [],
                                symptom=redact(trigger.text),
                                root_cause=redact(report.root_cause),
                                confidence=report.confidence,
                                evidence_summary=[
                                    redact(e.claim) for e in report.evidence
                                ],
                                fix=[redact(s) for s in report.actions_fix],
                            )
                        )
                except TimeoutError:
                    partial = True
                    report = report.model_copy(
                        update={
                            "partial": True,
                            "not_checked": (
                                *report.not_checked,
                                "budget exhausted: time",
                            ),
                        }
                    )
                except BudgetExceeded as exc:
                    partial = True
                    report = report.model_copy(
                        update={
                            "partial": True,
                            "not_checked": (
                                *report.not_checked,
                                f"budget exhausted: {exc.reason}",
                            ),
                        }
                    )
                except Exception as exc:
                    self.events.emit(
                        WarningEvent(
                            source="memory", message=self.redactor.redact_text(str(exc))
                        )
                    )
                finally:
                    current_run_context.reset(token)
            report = report.model_copy(update={"cost_usd": budget.snapshot().usd})

            def redact_strings(value: object) -> object:
                if isinstance(value, str):
                    return self.redactor.redact_text(value)
                if isinstance(value, dict):
                    return {key: redact_strings(item) for key, item in value.items()}
                if isinstance(value, (list, tuple)):
                    return [redact_strings(item) for item in value]
                return value

            report = Report.model_validate(redact_strings(report.model_dump()))
            await self.store.save_report(report)
            await self.store.save_investigation(
                investigation.model_copy(
                    update={
                        "status": "failed"
                        if failed
                        else "partial"
                        if partial
                        else "done",
                        "finished_at": self.clock.now(),
                        "spent": budget.snapshot(),
                    }
                )
            )
            await workspace.persist()
            self.events.emit(ReportReady(investigation_id=id))
            return report

        try:
            await self.store.save_trigger(trigger)
            await self.store.save_investigation(investigation)
            self.events.emit(
                InvestigationStarted(investigation_id=id, trigger_text=trigger.text)
            )
            if parent_id:
                self.pipeline.seed_cache(
                    id, await self.store.tool_calls(parent_id), include_rows=reopened
                )
                parent = await self.store.get_report(parent_id)
                if parent:
                    context.append(parent.model_dump_json())
                context.extend(
                    f.model_dump_json() for f in await self.store.findings(parent_id)
                )

            try:
                recall_tasks = [
                    asyncio.create_task(coro)
                    for coro in (
                        recall(
                            "memory.recall",
                            {
                                "query": trigger.text,
                                "services": [trigger.service]
                                if trigger.service
                                else [],
                                "since": (now - timedelta(days=7)).isoformat(),
                            },
                        ),
                        recall(
                            "docs.search",
                            {"query": f"{trigger.service or ''} {trigger.text}"},
                        ),
                    )
                ]
                try:
                    await asyncio.gather(*recall_tasks)
                finally:
                    for task in recall_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*recall_tasks, return_exceptions=True)
                plan = await self._agent(
                    ctx,
                    "plan",
                    Plan,
                    payload()
                    + "\nMaximum hypotheses: "
                    + str(max(0, self.config.investigation.max_subagents - 1)),
                    ["logs", "metrics", "docs", "memory", "workspace"],
                )
                drafts = [
                    HypothesisDraft(
                        statement=(
                            f"What changed in [{window.start.isoformat()}, "
                            f"{window.end.isoformat()}] "
                            "(deploys, infra, WAF, DNS, config) that correlates "
                            "with the symptom start?"
                        ),
                        rationale="Always check correlated changes",
                        capabilities=["changes", "network", "code"],
                    ),
                    *plan.hypotheses[
                        : max(0, self.config.investigation.max_subagents - 1)
                    ],
                ]
                for round_index in range(2):
                    await fanout(drafts)
                    check = await self._agent(
                        ctx, "completeness", CompletenessCheck, payload(), []
                    )
                    unchecked.extend(check.unexplained)
                    if (
                        check.explains_all
                        or round_index == 1
                        or budget.remaining_time(self.clock.now())
                        <= timedelta(minutes=2)
                        or budget.snapshot().usd >= limits.usd * 4 / 5
                    ):
                        break
                    drafts = check.new_hypotheses[
                        : self.config.investigation.max_subagents
                    ]
            except BudgetExceeded as exc:
                partial = True
                unchecked.append(f"budget exhausted: {exc.reason}")
                self.events.emit(BudgetExceededEvent(reason=exc.reason))
            return await finalize()
        except AgentRunError as exc:
            partial = model_unavailable = True
            unchecked.append(f"model unavailable: {str(exc)[:160]}")
            return await finalize()
        except asyncio.CancelledError:
            partial = cancelled = True
            unchecked.append("investigation cancelled")
            await finalize()
            raise
        except Exception:
            partial = failed = True
            unchecked.append("investigation failed unexpectedly")
            await finalize()
            raise
