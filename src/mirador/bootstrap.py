"""Application composition and owned resource lifetime."""

import asyncio
import json
import os
import secrets
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from rich.console import Console

from mirador.adapters.agents.openai_agents.pricing import PricingCatalog
from mirador.adapters.agents.openai_agents.runtime import OpenAIAgentsRuntime
from mirador.adapters.cli.render import render_json
from mirador.adapters.eval.fakes.changes import FakeChanges
from mirador.adapters.eval.fakes.code import FakeCode
from mirador.adapters.eval.fakes.dataset import CaseDataset
from mirador.adapters.eval.fakes.logs import FakeLogs
from mirador.adapters.eval.fakes.memory import FakeMemory
from mirador.adapters.eval.fakes.metrics import FakeMetrics
from mirador.adapters.eval.fakes.network import FakeNetwork
from mirador.adapters.eval.scripted_runtime import ScriptedAgentRuntime
from mirador.adapters.events.console.sink import ConsoleEventSink
from mirador.adapters.memory.disabled import DisabledIncidentMemory
from mirador.adapters.memory.graphiti.memory import GraphitiIncidentMemory
from mirador.adapters.providers.aws.alb_access_logs import AlbAccessLogs
from mirador.adapters.providers.aws.cloudtrail import CloudTrailChanges
from mirador.adapters.providers.aws.cloudwatch_logs import CloudWatchLogs
from mirador.adapters.providers.aws.cloudwatch_metrics import CloudWatchMetrics
from mirador.adapters.providers.aws.network import AwsNetwork
from mirador.adapters.providers.aws.session import AwsSessions
from mirador.adapters.providers.datadog.client import DatadogClient
from mirador.adapters.providers.datadog.logs import DatadogLogs
from mirador.adapters.providers.datadog.metrics import DatadogMetrics
from mirador.adapters.providers.github.changes import GitHubChanges
from mirador.adapters.providers.github.client import GitHubClient
from mirador.adapters.providers.github.code import GitHubCode
from mirador.adapters.providers.grafana.client import GrafanaClient
from mirador.adapters.providers.grafana.loki import GrafanaLoki
from mirador.adapters.providers.grafana.prometheus import GrafanaPrometheus
from mirador.adapters.providers.localdocs.index import LocalDocsSource
from mirador.adapters.store.sqlite.store import SqliteStore
from mirador.adapters.workspace.duckdb.workspace import DuckDBWorkspaceFactory
from mirador.application.config import MiradorConfig, Secrets, load_config, load_secrets
from mirador.application.orchestration.context import RunContext, current_run_context
from mirador.application.orchestration.investigator import Investigator
from mirador.application.ports.agent_runtime import AgentRuntime, AgentSpec
from mirador.application.ports.clock import SystemClock
from mirador.application.ports.events import BudgetWarningEvent, WarningEvent
from mirador.application.ports.health import HealthCheckable, HealthResult
from mirador.application.ports.incident_memory import IncidentMemory
from mirador.application.ports.store import InvestigationStore
from mirador.application.ports.workspace import Workspace
from mirador.application.prompts import load_prompt
from mirador.application.tooling.catalog.changes import changes_tools
from mirador.application.tooling.catalog.code import code_tools
from mirador.application.tooling.catalog.docs import docs_tools
from mirador.application.tooling.catalog.logs import logs_tools
from mirador.application.tooling.catalog.memory import memory_tools
from mirador.application.tooling.catalog.metrics import metrics_tools
from mirador.application.tooling.catalog.network import network_tools
from mirador.application.tooling.catalog.workspace import workspace_tools
from mirador.application.tooling.envelope import wrap_untrusted
from mirador.application.tooling.pipeline import ToolPipeline
from mirador.application.tooling.registry import ToolRegistry
from mirador.application.tooling.spec import ToolSpec
from mirador.application.use_cases.ask import AskUseCase
from mirador.application.use_cases.doctor import DoctorUseCase
from mirador.application.use_cases.investigate import InvestigateUseCase
from mirador.application.use_cases.memory_admin import MemoryAdminUseCase
from mirador.application.use_cases.run_eval import (
    CaseExpectations,
    CaseRunOutcome,
    JudgeVerdict,
    RunEvalUseCase,
)
from mirador.domain.budget import BudgetExceeded, BudgetLimits, BudgetTracker
from mirador.domain.trigger import Trigger
from mirador.redaction.pseudonymizer import Pseudonymizer


@dataclass
class EvalServices:
    run_eval: RunEvalUseCase


@dataclass
class AppServices:
    ask: AskUseCase
    investigate: InvestigateUseCase
    memory_admin: MemoryAdminUseCase
    doctor: DoctorUseCase
    store: InvestigationStore
    events: ConsoleEventSink


class _KeyProbe:
    def __init__(self, key: str) -> None:
        self.key = key

    async def health_check(self) -> HealthResult:
        if not self.key:
            return HealthResult(
                component="OpenRouter", ok=False, detail="OPENROUTER_API_KEY missing"
            )
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {self.key}"},
            )
            response.raise_for_status()
        return HealthResult(component="OpenRouter", ok=True, detail="key valid")


def _hmac_key(directory: Path) -> bytes:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "hmac.key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        path.chmod(0o600)
    else:
        with os.fdopen(fd, "wb") as output:
            output.write(secrets.token_bytes(32))
    print(f"Using HMAC key: {path}", file=sys.stderr)
    return path.read_bytes()


@asynccontextmanager
async def build_app(
    config_path: Path,
    *,
    eval_mode: bool = False,
    doctor_mode: bool = False,
    cases_dir: Path = Path("evals/cases"),
) -> AsyncIterator[AppServices | EvalServices]:
    config = await asyncio.to_thread(load_config, config_path)
    secret = load_secrets(
        {**os.environ, "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", "")}
    )
    key = secret.openrouter_api_key.get_secret_value()
    hmac = (
        secret.hmac_key.get_secret_value().encode()
        if secret.hmac_key
        else await asyncio.to_thread(_hmac_key, config.data_dir)
    )
    await asyncio.to_thread(
        config.data_dir.mkdir, mode=0o700, parents=True, exist_ok=True
    )
    redactor = Pseudonymizer(hmac, config.redaction.extra_patterns)
    clock = SystemClock()
    events = ConsoleEventSink(Console(stderr=True), enabled=True)
    async with AsyncExitStack() as stack:
        store = await SqliteStore.open(config.data_dir / "mirador.db")
        stack.push_async_callback(store.close)
        if eval_mode:
            runner = _EvalCaseRunner(config, store, redactor, key)
            stack.push_async_callback(runner.close)
            yield EvalServices(
                run_eval=RunEvalUseCase(
                    runner,
                    cases_dir=cases_dir,
                    redactor=redactor,
                    redaction_dir=Path("evals/redaction"),
                    judge=runner.judge,
                ),
            )
            return
        pricing = (
            await PricingCatalog.fetch(key)
            if key and not doctor_mode
            else PricingCatalog(
                {
                    name: (Decimal(0), Decimal(0))
                    for names in (
                        config.models.orchestrator,
                        config.models.subagent,
                        config.models.reporter,
                    )
                    for name in names
                }
            )
        )

        async def usage(input_tokens: int, output_tokens: int, model: str) -> None:
            usd = pricing.cost(model, input_tokens, output_tokens)
            ctx = current_run_context.get()
            await (ctx.store if ctx else store).add_spend(
                clock.now(), usd, ctx.spend_kind if ctx else "investigation"
            )
            if ctx:
                try:
                    ctx.budget.charge_llm(
                        input_tokens + output_tokens, usd, clock.now()
                    )
                finally:
                    if ctx.budget.take_warning(clock.now()):
                        ctx.events.emit(BudgetWarningEvent(spend=ctx.budget.snapshot()))

        memory: IncidentMemory
        probes: list[HealthCheckable] = [
            _KeyProbe(key),
            _LocalProbe(store, config.data_dir),
        ]
        try:
            if not key:
                raise RuntimeError("OPENROUTER_API_KEY missing")
            url = urlparse(config.memory.graphiti.falkordb_url)
            backend = await GraphitiIncidentMemory.create(
                initialize=not doctor_mode,
                falkordb_host=url.hostname or "localhost",
                falkordb_port=url.port or 6379,
                api_key=key or "missing",
                extraction_model=config.models.memory_extraction[0],
                embedding_model=config.models.embeddings[0],
                states=store,
                on_llm_usage=usage,
                on_warning=lambda message: events.emit(
                    WarningEvent(source="memory", message=message)
                ),
            )
            stack.push_async_callback(backend.aclose)
            memory = backend
            probes.append(backend)
        except Exception as exc:
            disabled = DisabledIncidentMemory(
                redactor.redact_text(f"{type(exc).__name__}: {exc}")[:200]
            )
            memory = disabled
            probes.append(disabled)
            events.emit(
                WarningEvent(source="memory", message="memory backend unavailable")
            )
        docs = (
            await LocalDocsSource.open(config.docs.path, config.data_dir / "docs.db")
            if await asyncio.to_thread(config.docs.path.exists)
            else None
        )
        registry = ToolRegistry()
        specs = memory_tools(memory) + workspace_tools(
            config.workspace.max_rows_returned
        )
        if docs:
            specs += docs_tools(docs)
            probes.append(docs)
        specs += await _provider_tools(config, secret, stack, probes)
        for spec in specs:
            registry.register(spec)
        pipeline = ToolPipeline(
            redactor=redactor,
            store=store,
            events=events,
            clock=clock,
            enabled_sensitive=frozenset(
                s.name for s in specs if s.verb == "read-sensitive"
            ),
            quotas=config.quotas,
        )
        registry.bound(pipeline)
        workspaces = DuckDBWorkspaceFactory(
            config.data_dir,
            config.workspace.memory_limit,
            config.workspace.query_timeout,
            config.workspace.retention,
        )
        await workspaces.purge_expired(clock.now())
        runtime = OpenAIAgentsRuntime(
            api_key=key or "missing",
            models={
                "orchestrator": config.models.orchestrator,
                "subagent": config.models.subagent,
                "reporter": config.models.reporter,
                "judge": config.models.orchestrator,
            },
            pricing=pricing,
        )
        stack.push_async_callback(runtime.client.close)
        investigator = Investigator(
            runtime=runtime,
            registry=registry,
            pipeline=pipeline,
            memory=memory,
            docs=docs,
            store=store,
            events=events,
            clock=clock,
            redactor=redactor,
            workspaces=workspaces,
            config=config,
        )
        yield AppServices(
            AskUseCase(investigator),
            InvestigateUseCase(investigator),
            MemoryAdminUseCase(memory),
            DoctorUseCase(probes),
            store,
            events,
        )


async def _provider_tools(
    config: MiradorConfig,
    secret: Secrets,
    stack: AsyncExitStack,
    probes: list[HealthCheckable],
) -> list[ToolSpec[Any]]:
    specs: list[ToolSpec[Any]] = []
    if secret.dd_api_key and secret.dd_app_key:
        client = DatadogClient(
            config.providers.datadog.site,
            secret.dd_api_key.get_secret_value(),
            secret.dd_app_key.get_secret_value(),
        )
        stack.push_async_callback(client.aclose)
        logs = DatadogLogs(client)
        metrics = DatadogMetrics(client)
        probes.extend([logs, metrics])
        specs += logs_tools(logs, []) + metrics_tools(metrics, [])
    else:
        probes.append(_StaticProbe("Datadog", True, "not configured"))
    if secret.github_token:
        github = GitHubClient(
            secret.github_token.get_secret_value(), config.providers.github.repos
        )
        stack.push_async_callback(github.aclose)
        code = GitHubCode(github)
        changes = GitHubChanges(github, config.providers.github.service_repos)
        probes.extend([code, changes])
        specs += code_tools(code, config.providers.github.repos) + changes_tools(
            changes, []
        )
    else:
        probes.append(_StaticProbe("GitHub", True, "not configured"))
    if secret.grafana_token:
        grafana = GrafanaClient(
            config.providers.grafana.url, secret.grafana_token.get_secret_value()
        )
        stack.push_async_callback(grafana.aclose)
        probes.append(grafana)
        if config.providers.grafana.loki_uid:
            loki = GrafanaLoki(grafana, config.providers.grafana.loki_uid)
            probes.append(loki)
            specs += logs_tools(loki, [])
        if config.providers.grafana.prometheus_uid:
            prometheus = GrafanaPrometheus(
                grafana, config.providers.grafana.prometheus_uid
            )
            probes.append(prometheus)
            specs += metrics_tools(prometheus, [])
    else:
        probes.append(_StaticProbe("Grafana", True, "not configured"))
    aws_targets = config.targets.aws
    if aws_targets:
        sessions = AwsSessions(aws_targets)
        targets = [target.name for target in aws_targets]
        aws_logs = CloudWatchLogs(sessions)
        aws_metrics = CloudWatchMetrics(sessions)
        aws_changes = CloudTrailChanges(sessions)
        network = AwsNetwork(sessions)
        probes.extend([aws_logs, aws_metrics, aws_changes, network])
        specs += (
            logs_tools(aws_logs, targets)
            + metrics_tools(aws_metrics, targets)
            + changes_tools(aws_changes, targets)
            + network_tools(network, targets)
        )
        sensitive = config.sensitive_tools.alb_access_logs
        if sensitive.enabled:
            alb = AlbAccessLogs(sessions, sensitive.bucket, sensitive.prefix)
            probes.append(alb)
            specs += [
                replace(spec, verb="read-sensitive")
                for spec in logs_tools(alb, targets)
            ]
    else:
        probes.append(_StaticProbe("AWS", True, "not configured"))
    return specs


class _StaticProbe:
    def __init__(self, component: str, ok: bool, detail: str) -> None:
        self.result = HealthResult(component=component, ok=ok, detail=detail)

    async def health_check(self) -> HealthResult:
        return self.result


class _LocalProbe:
    def __init__(self, store: InvestigationStore, directory: Path) -> None:
        self.store, self.directory = store, directory

    async def health_check(self) -> HealthResult:
        await self.store.monthly_spend(
            datetime.now(UTC).date().replace(day=1), "investigation"
        )

        def probe() -> None:
            with tempfile.TemporaryFile(dir=self.directory) as output:
                output.write(b"mirador")
                output.flush()

        await asyncio.to_thread(probe)
        return HealthResult(
            component="store/data_dir", ok=True, detail="readable and writable"
        )


class _ElapsedFixtureClock:
    def __init__(self, at: datetime) -> None:
        self.at = at
        self.started = perf_counter()

    def now(self) -> datetime:
        return self.at + timedelta(seconds=perf_counter() - self.started)


class _EvalCaseRunner:
    """Own one isolated case at a time, retaining its context through judging."""

    def __init__(
        self,
        config: MiradorConfig,
        store: InvestigationStore,
        redactor: Pseudonymizer,
        key: str,
    ) -> None:
        self.config, self.main_store = config, store
        self.redactor, self.key = redactor, key
        self.stack = AsyncExitStack()
        self.ctx: RunContext | None = None
        self.runtime: AgentRuntime | None = None
        self.outcome: CaseRunOutcome | None = None
        self.started = 0.0
        self.accounted = Decimal(0)

    async def _refresh(self) -> None:
        if self.ctx is None or self.outcome is None:
            return
        calls = await self.ctx.store.llm_calls(self.ctx.investigation_id)
        total = sum((c.usd for c in calls), Decimal(0))
        await self.main_store.add_spend(
            datetime.now(UTC), total - self.accounted, "eval"
        )
        self.accounted = total
        self.outcome.llm_calls = calls
        self.outcome.usd = total
        self.outcome.duration_s = perf_counter() - self.started

    async def close(self) -> None:
        try:
            await self._refresh()
        finally:
            await self.stack.aclose()
            self.ctx = None
            self.outcome = None

    async def run_case(self, case_dir: Path, run: int, dry_run: bool) -> CaseRunOutcome:
        await self.close()
        self.stack = AsyncExitStack()
        self.accounted = Decimal(0)
        self.started = perf_counter()
        outcome = CaseRunOutcome(
            name=case_dir.name,
            expectations=CaseExpectations(
                root_cause="",
                must_mention=[],
                must_not_conclude=[],
                min_confidence="low",
                secrets=[],
            ),
        )
        self.outcome = outcome
        try:
            now = datetime.now(UTC)
            monthly_remaining = (
                self.config.budgets.eval.monthly_usd
                - await self.main_store.monthly_spend(now.date().replace(day=1), "eval")
            )
            if monthly_remaining <= 0:
                raise BudgetExceeded("monthly_usd")
            if not dry_run and not self.key:
                raise ValueError("OPENROUTER_API_KEY missing (required for live evals)")
            dataset = await asyncio.to_thread(CaseDataset.load, case_dir)
            spec = dataset.spec
            outcome.name = spec.name
            outcome.expectations = CaseExpectations(
                **spec.expected.model_dump(), secrets=spec.secrets
            )
            temporary = await asyncio.to_thread(
                tempfile.TemporaryDirectory, prefix="mirador-eval-", delete=False
            )
            interrupted = False

            def cleanup_case() -> None:
                # An interrupted investigation must remain available for inspection.
                if not interrupted:
                    temporary.cleanup()

            self.stack.push_async_callback(asyncio.to_thread, cleanup_case)
            directory = Path(temporary.name)
            store = await SqliteStore.open(directory / "eval.db")
            self.stack.push_async_callback(store.close)
            clock = _ElapsedFixtureClock(spec.trigger.now)
            memory = FakeMemory(dataset)
            docs_root = dataset.docs
            if not await asyncio.to_thread(docs_root.exists):
                docs_root = directory / "docs"
                await asyncio.to_thread(docs_root.mkdir)
            docs = await LocalDocsSource.open(docs_root, directory / "docs.db")
            registry = ToolRegistry()
            tools = (
                memory_tools(memory)
                + docs_tools(docs)
                + workspace_tools(self.config.workspace.max_rows_returned)
            )
            for provider in spec.providers.logs:
                tools += logs_tools(
                    FakeLogs(provider, dataset, provider in spec.auth_error_providers),
                    [],
                )
            for provider in spec.providers.metrics:
                tools += metrics_tools(
                    FakeMetrics(
                        provider, dataset, provider in spec.auth_error_providers
                    ),
                    [],
                )
            for provider in spec.providers.changes:
                tools += changes_tools(
                    FakeChanges(
                        provider, dataset, provider in spec.auth_error_providers
                    ),
                    [],
                )
            for provider in spec.providers.network:
                tools += network_tools(
                    FakeNetwork(
                        provider, dataset, provider in spec.auth_error_providers
                    ),
                    [],
                )
            repos = sorted({path.split("/")[0] for path in dataset.code})
            for provider in spec.providers.code:
                tools += code_tools(
                    FakeCode(provider, dataset, provider in spec.auth_error_providers),
                    repos,
                )
            for tool in tools:
                registry.register(tool)
            events = ConsoleEventSink(Console(stderr=True), enabled=False)
            pipeline = ToolPipeline(
                redactor=self.redactor,
                store=store,
                events=events,
                clock=clock,
                enabled_sensitive=frozenset(),
                quotas=self.config.quotas,
                fixture_time=spec.trigger.now,
            )
            limits = BudgetLimits(
                time=spec.budget.time,
                tool_calls=spec.budget.tool_calls,
                usd=min(
                    spec.budget.usd,
                    self.config.budgets.eval.per_run_usd,
                    monthly_remaining,
                ),
                tokens=self.config.budgets.per_investigation.tokens,
            )
            config = self.config.model_copy(
                update={
                    "budgets": self.config.budgets.model_copy(
                        update={"per_investigation": limits}
                    )
                }
            )
            workspaces = _EvalWorkspaces(
                self.stack,
                directory,
                config.workspace.memory_limit,
                config.workspace.query_timeout,
                config.workspace.retention,
            )
            runtime: AgentRuntime

            def observe(request: str, response: str) -> None:
                outcome.model_traffic.extend((request, response))

            if dry_run:
                runtime = ScriptedAgentRuntime(dataset, observer=observe)
            else:
                live = OpenAIAgentsRuntime(
                    observer=observe,
                    api_key=self.key,
                    models={
                        "orchestrator": config.models.orchestrator,
                        "subagent": config.models.subagent,
                        "reporter": config.models.reporter,
                        "judge": config.models.orchestrator,
                    },
                    pricing=await PricingCatalog.fetch(self.key),
                )
                self.stack.push_async_callback(live.client.close)
                runtime = live
            self.runtime = runtime
            investigator = Investigator(
                runtime=runtime,
                registry=registry,
                pipeline=pipeline,
                memory=memory,
                docs=docs,
                store=store,
                events=events,
                clock=clock,
                redactor=self.redactor,
                workspaces=workspaces,
                config=config,
                fixture_time=spec.trigger.now,
            )
            id = str(uuid4())
            # Judge has no tools, but the runtime requires a complete RunContext.
            workspace = await workspaces.create(id + "-judge")
            self.ctx = RunContext(
                id,
                None,
                BudgetTracker(limits, clock.now()),
                workspace,
                store,
                events,
                clock,
                self.redactor,
                "eval",
                config.workspace.inline_rows_threshold,
            )
            trigger = Trigger(
                id=str(uuid4()),
                source="cli_question"
                if spec.trigger.kind == "question"
                else "cli_alert",
                text=self.redactor.redact_text(spec.trigger.text),
                service=None,
                window=None,
                severity=None,
                links=(),
                raw_redacted=None,
                fingerprint=spec.name,
            )
            try:
                outcome.report = await investigator.run(
                    trigger, parent_id=None, spend_kind="eval", investigation_id=id
                )
                outcome.rendered_report = render_json(outcome.report)
            except asyncio.CancelledError:
                interrupted = True
                print(
                    f"Investigación {id} interrumpida; "
                    f'continuar con: mirador ask --thread {id} "..."\n'
                    f"Eval store: {directory / 'eval.db'}",
                    file=sys.stderr,
                )
                raise
            finally:
                outcome.tool_calls = await store.tool_calls(id)
                outcome.findings = await store.findings(id)
                outcome.memory_writes = [e.model_dump_json() for e in memory.recorded]
                await self._refresh()
        except Exception as exc:
            outcome.error = self.redactor.redact_text(f"{type(exc).__name__}: {exc}")
        outcome.duration_s = perf_counter() - self.started
        return outcome

    async def judge(self, outcome: CaseRunOutcome) -> JudgeVerdict:
        ctx, runtime = self.ctx, self.runtime
        if ctx is None or runtime is None or outcome.report is None:
            raise ValueError("No case available to judge")
        # Investigation and judge share the same per-run cost ceiling.
        ctx.budget.charge_llm(0, outcome.usd, ctx.clock.now())
        token = current_run_context.set(ctx)
        try:
            return await runtime.run(
                AgentSpec(
                    name="judge",
                    role="judge",
                    instructions=load_prompt("judge"),
                    tools=[],
                    output_type=JudgeVerdict,
                    max_turns=1,
                ),
                wrap_untrusted(
                    self.redactor.redact_text(
                        json.dumps(
                            {
                                "expected": outcome.expectations.root_cause,
                                "candidate": outcome.report.root_cause,
                            }
                        )
                    ),
                    tool="eval",
                    call_id=ctx.investigation_id,
                ),
                ctx,
            )
        finally:
            current_run_context.reset(token)
            await self._refresh()


class _EvalWorkspaces(DuckDBWorkspaceFactory):
    def __init__(
        self,
        stack: AsyncExitStack,
        directory: Path,
        memory: int,
        timeout: timedelta,
        retention: timedelta,
    ) -> None:
        super().__init__(directory, memory, timeout, retention)
        self.stack = stack

    async def create(self, investigation_id: str) -> Workspace:
        workspace = await super().create(investigation_id)
        self.stack.push_async_callback(workspace.close)
        return workspace
