from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from mirador.application.orchestration.context import RunContext
from mirador.application.tooling.pipeline import BoundTool

AgentRole = Literal["orchestrator", "subagent", "reporter", "judge"]
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class AgentSpec[T: BaseModel]:
    name: str
    role: AgentRole
    instructions: str
    tools: Sequence[BoundTool]
    output_type: type[T]
    max_turns: int


class AgentRuntime(Protocol):
    async def run(self, spec: AgentSpec[T], input: str, ctx: RunContext) -> T: ...


class AgentRunError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
