"""Deterministic agent replay through the real bound tools and accounting ports.

Hypothesis indices are zero-based subagent invocation order (the mandatory
changes hypothesis is index zero). Entries are consumed once per investigation.
$call:N is one-based and local to the selected entry's tool steps.
"""

import asyncio
import json
import re
from collections.abc import Callable
from decimal import Decimal
from html import unescape
from uuid import uuid4

from pydantic import BaseModel, JsonValue, ValidationError

from mirador.application.orchestration.context import RunContext
from mirador.application.ports.agent_runtime import (
    AgentRunError,
    AgentRuntime,
    AgentSpec,
)
from mirador.application.ports.store import LlmCallRecord

from .fakes.dataset import CaseDataset, ScriptEntry, SleepStep


class ScriptedAgentRuntime(AgentRuntime):
    def __init__(
        self, dataset: CaseDataset, observer: Callable[[str, str], None] | None = None
    ) -> None:
        self.observer = observer
        if dataset.dry_run is None:
            raise ValueError("Scripted runtime requires dry_run.yaml")
        self.entries = dataset.dry_run
        self._used: dict[str, set[int]] = {}
        self._hypotheses: dict[str, int] = {}

    def _entry[T: BaseModel](self, spec: AgentSpec[T], ctx: RunContext) -> ScriptEntry:
        used = self._used.setdefault(ctx.investigation_id, set())
        hypothesis_index = None
        if spec.role == "subagent":
            hypothesis_index = self._hypotheses.get(ctx.investigation_id, 0)
            self._hypotheses[ctx.investigation_id] = hypothesis_index + 1
        for index, entry in enumerate(self.entries):
            if index in used or entry.role != spec.role:
                continue
            if entry.agent is not None and entry.agent != spec.name:
                continue
            if spec.role == "subagent" and entry.hypothesis_index != hypothesis_index:
                continue
            used.add(index)
            return entry
        raise AgentRunError(
            f"No script entry for {spec.name} hypothesis {hypothesis_index}",
            retryable=False,
        )

    async def run[T: BaseModel](
        self, spec: AgentSpec[T], input: str, ctx: RunContext
    ) -> T:
        entry = self._entry(spec, ctx)
        calls: list[str] = []
        for step in entry.steps:
            now = ctx.clock.now()
            ctx.budget.check(now)
            response = ""
            try:
                if isinstance(step, SleepStep):
                    await asyncio.sleep(step.sleep)
                else:
                    tool = next(
                        (tool for tool in spec.tools if tool.name == step.tool), None
                    )
                    if tool is None:
                        raise AgentRunError(
                            f"Tool unavailable to {spec.name}: {step.tool}",
                            retryable=False,
                        )
                    response = await tool.invoke(json.dumps(step.args), ctx)
                    match = re.match(
                        r'<untrusted_data\b[^>]*\bcall_id="([^"]+)"', response
                    )
                    if match is None:
                        raise AgentRunError(
                            "Tool response has no call_id envelope", retryable=False
                        )
                    calls.append(unescape(match[1]))
            finally:
                if self.observer is not None:
                    self.observer(step.model_dump_json(), response)
                # Persist charges even when charging itself crosses a budget limit.
                try:
                    ctx.budget.charge_llm(0, Decimal("0.001"), ctx.clock.now())
                finally:
                    await ctx.store.add_spend(now, Decimal("0.001"), ctx.spend_kind)
                    await ctx.store.save_llm_call(
                        LlmCallRecord(
                            id=str(uuid4()),
                            investigation_id=ctx.investigation_id,
                            agent_name=spec.name,
                            model="scripted",
                            started_at=now,
                            input_tokens=0,
                            output_tokens=0,
                            usd=Decimal("0.001"),
                            request_text=ctx.redactor.redact_text(
                                step.model_dump_json()
                            ),
                            response_text=ctx.redactor.redact_text(response),
                        )
                    )

        global_calls = sorted(
            await ctx.store.tool_calls(ctx.investigation_id),
            key=lambda call: call.started_at,
        )

        def replace(value: JsonValue) -> JsonValue:
            if isinstance(value, str):
                if value.startswith("$tool:"):
                    match = re.fullmatch(r"\$tool:([^#]+)(?:#([1-9]\d*))?", value)
                    matches = [c for c in global_calls if match and c.tool == match[1]]
                    index = int(match[2] or "1") if match else 0
                    if not 1 <= index <= len(matches):
                        raise AgentRunError(
                            f"Unresolvable tool placeholder: {value}", retryable=False
                        )
                    return matches[index - 1].id
                match = re.fullmatch(r"\$call:(\d+)", value)
                if match:
                    index = int(match[1])
                    if not 1 <= index <= len(calls):
                        raise AgentRunError(
                            f"Invalid call placeholder: {value}", retryable=False
                        )
                    return calls[index - 1]
            elif isinstance(value, list):
                return [replace(item) for item in value]
            elif isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            return value

        try:
            return spec.output_type.model_validate(replace(entry.output))
        except ValidationError as exc:
            raise AgentRunError(
                f"Invalid scripted output for {spec.name}", retryable=False
            ) from exc
