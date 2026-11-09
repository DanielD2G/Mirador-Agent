import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from uuid import uuid4

from agents import (
    Agent,
    FunctionTool,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunContextWrapper,
    RunHooks,
    Runner,
    set_tracing_disabled,
)
from agents.items import ModelResponse, TResponseInputItem
from agents.tool_context import ToolContext
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI

from mirador.application.orchestration.context import RunContext
from mirador.application.ports.agent_runtime import (
    AgentRole,
    AgentRunError,
    AgentRuntime,
    AgentSpec,
    T,
)
from mirador.application.ports.events import BudgetWarningEvent
from mirador.application.ports.store import LlmCallRecord
from mirador.application.tooling.pipeline import BoundTool
from mirador.domain.budget import BudgetExceeded

from .pricing import PricingCatalog


class _BudgetHooks(RunHooks[RunContext]):
    def __init__(
        self,
        ctx: RunContext,
        model: str,
        pricing: PricingCatalog,
        observer: Callable[[str, str], None] | None = None,
    ) -> None:
        self.ctx, self.model, self.pricing = ctx, model, pricing
        self.observer = observer
        self.request = ""
        self.started_at = ctx.clock.now()
        self.budget_error: BudgetExceeded | None = None

    async def on_llm_start(
        self,
        context: RunContextWrapper[RunContext],
        agent: Agent[RunContext],
        system_prompt: str | None,
        input_items: list[TResponseInputItem],
    ) -> None:
        if self.budget_error is not None:
            raise self.budget_error
        self.ctx.budget.check(self.ctx.clock.now())
        self.started_at = self.ctx.clock.now()
        self.request = json.dumps(
            {"system": system_prompt, "input": input_items}, default=str
        )
        if self.observer is not None:
            self.observer(self.request, "")

    async def on_llm_end(
        self,
        context: RunContextWrapper[RunContext],
        agent: Agent[RunContext],
        response: ModelResponse,
    ) -> None:
        ctx, usage = self.ctx, response.usage
        response_text = json.dumps([item.model_dump() for item in response.output])
        if self.observer is not None:
            self.observer("", response_text)
        usd = self.pricing.cost(self.model, usage.input_tokens, usage.output_tokens)
        try:
            ctx.budget.charge_llm(
                usage.input_tokens + usage.output_tokens, usd, ctx.clock.now()
            )
        finally:
            if ctx.budget.take_warning(ctx.clock.now()):
                ctx.events.emit(BudgetWarningEvent(spend=ctx.budget.snapshot()))
            await ctx.store.save_llm_call(
                LlmCallRecord(
                    id=str(uuid4()),
                    investigation_id=ctx.investigation_id,
                    agent_name=agent.name,
                    model=self.model,
                    started_at=self.started_at,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    usd=usd,
                    request_text=ctx.redactor.redact_text(self.request),
                    response_text=ctx.redactor.redact_text(response_text),
                )
            )
            await ctx.store.add_spend(ctx.clock.now(), usd, ctx.spend_kind)


def _tool(bound: BoundTool, ctx: RunContext, hooks: _BudgetHooks) -> FunctionTool:
    async def invoke(context: ToolContext[RunContext], args: str) -> str:
        try:
            return await bound.invoke(args, ctx)
        except BudgetExceeded as exc:
            hooks.budget_error = exc
            raise

    return FunctionTool(
        name=bound.name.replace(".", "__"),
        description=bound.description,
        params_json_schema=bound.params_schema,
        on_invoke_tool=invoke,
        strict_json_schema=False,
        timeout_behavior="raise_exception",
    )


class OpenAIAgentsRuntime(AgentRuntime):
    def __init__(
        self,
        *,
        api_key: str,
        models: Mapping[AgentRole, Sequence[str]],
        pricing: PricingCatalog,
        observer: Callable[[str, str], None] | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        set_tracing_disabled(True)
        self.models = {role: tuple(names) for role, names in models.items()}
        self.observer = observer
        self.pricing = pricing
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        for names in self.models.values():
            for name in names:
                pricing.cost(name, 0, 0)

    async def run(self, spec: AgentSpec[T], input: str, ctx: RunContext) -> T:
        last_failure = "No usable models remain"
        for name in self.models.get(spec.role, ()):
            hooks = _BudgetHooks(ctx, name, self.pricing, self.observer)
            tools = [_tool(bound, ctx, hooks) for bound in spec.tools]
            if len({tool.name for tool in tools}) != len(tools):
                raise AgentRunError("Tool names collide after mapping", retryable=False)
            agent: Agent[RunContext] = Agent(
                name=spec.name,
                instructions=spec.instructions,
                tools=[*tools],
                model=OpenAIChatCompletionsModel(name, self.client),
                model_settings=ModelSettings(extra_body={"usage": {"include": True}}),
                output_type=spec.output_type,
            )
            try:
                async with asyncio.timeout(
                    ctx.budget.remaining_time(ctx.clock.now()).total_seconds()
                ):
                    result = await Runner.run(
                        agent, input, context=ctx, max_turns=spec.max_turns, hooks=hooks
                    )
                if hooks.budget_error is not None:
                    raise hooks.budget_error
                return spec.output_type.model_validate(result.final_output)
            except Exception as exc:
                if hooks.budget_error is not None:
                    raise hooks.budget_error from exc
                if isinstance(exc, TimeoutError):
                    raise BudgetExceeded("time") from exc
                if isinstance(exc, BudgetExceeded):
                    raise
                if isinstance(exc, MaxTurnsExceeded):
                    raise AgentRunError(str(exc), retryable=False) from exc
                if isinstance(exc, ModelBehaviorError) or (
                    isinstance(exc, APIStatusError)
                    and (exc.status_code == 429 or exc.status_code >= 500)
                ):
                    last_failure = type(exc).__name__
                    continue
                if isinstance(exc, APIError):
                    raise AgentRunError(
                        type(exc).__name__,
                        retryable=isinstance(exc, APIConnectionError),
                    ) from exc
                raise
        raise AgentRunError(
            last_failure, retryable=last_failure != "No usable models remain"
        )
