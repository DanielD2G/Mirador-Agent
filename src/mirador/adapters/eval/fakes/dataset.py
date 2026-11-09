"""Validated, eagerly loaded fixtures; loading is a synchronous setup boundary.

Network WAF rules are keyed by ACL; sampled requests carry web_acl/rule/ts.
Code fixtures represent one snapshot, so no revision history is synthesized.
Memory fixtures are flat IncidentEpisode fields plus optional id, state and facts.
"""

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)

from mirador.application.config import Duration
from mirador.application.ports.capabilities.changes import Change
from mirador.application.ports.capabilities.filter import Filter
from mirador.application.ports.capabilities.logs import Row
from mirador.application.ports.capabilities.metrics import Series
from mirador.application.ports.capabilities.network import (
    LoadBalancer,
    NatGateway,
    ServiceStatus,
    WafRule,
)
from mirador.application.ports.incident_memory import EpisodeState, IncidentEpisode
from mirador.domain.report import Confidence
from mirador.domain.time import TimeWindow, ensure_utc


class FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseTrigger(FixtureModel):
    kind: Literal["question", "alert"]
    text: str
    now: datetime

    _utc = field_validator("now")(ensure_utc)


class CaseProviders(FixtureModel):
    logs: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    code: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)


class CaseExpected(FixtureModel):
    root_cause: str
    must_mention: list[str]
    must_not_conclude: list[str]
    min_confidence: Confidence


class CaseBudget(FixtureModel):
    usd: Decimal = Field(gt=0)
    tool_calls: int = Field(gt=0)
    time: Duration

    @field_validator("time")
    @classmethod
    def positive_time(cls, value: Duration) -> Duration:
        if value.total_seconds() <= 0:
            raise ValueError("Budget time must be positive")
        return value


class CaseSpec(FixtureModel):
    name: str
    trigger: CaseTrigger
    providers: CaseProviders
    expected: CaseExpected
    secrets: list[str]
    budget: CaseBudget
    auth_error_providers: list[str] = Field(default_factory=list)


class MemoryFixture(IncidentEpisode):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    state: EpisodeState = "unverified"
    facts: list[str] = Field(default_factory=list)


class LoadBalancerFixture(LoadBalancer):
    target: str | None = Field(default=None, exclude=True)


class NatGatewayFixture(NatGateway):
    target: str | None = Field(default=None, exclude=True)


class WafRuleFixture(WafRule):
    target: str | None = Field(default=None, exclude=True)


class ServiceStatusFixture(ServiceStatus):
    target: str | None = Field(default=None, exclude=True)


class NetworkFixture(FixtureModel):
    load_balancers: list[LoadBalancerFixture] = Field(default_factory=list)
    nat_gateways: list[NatGatewayFixture] = Field(default_factory=list)
    waf_rules: dict[str, list[WafRuleFixture]] = Field(default_factory=dict)
    sampled_requests: list[Row] = Field(default_factory=list)
    services: list[ServiceStatusFixture] = Field(default_factory=list)


class ToolStep(FixtureModel):
    tool: str
    args: dict[str, JsonValue] = Field(default_factory=dict)


class SleepStep(FixtureModel):
    sleep: float = Field(ge=0, allow_inf_nan=False)


class ScriptEntry(FixtureModel):
    role: Literal["orchestrator", "subagent", "reporter", "judge"]
    agent: Literal["plan", "completeness"] | None = None
    hypothesis_index: int | None = Field(default=None, ge=0)
    steps: list[ToolStep | SleepStep]
    output: dict[str, JsonValue]

    @model_validator(mode="after")
    def valid_selector(self) -> Self:
        if self.agent is not None and self.role != "orchestrator":
            raise ValueError("agent is only valid for orchestrator entries")
        if (self.hypothesis_index is not None) != (self.role == "subagent"):
            raise ValueError("hypothesis_index is required only for subagent entries")
        return self


class CaseDataset:
    def __init__(
        self,
        *,
        case_dir: Path,
        spec: CaseSpec,
        logs: dict[str, list[Row]],
        metrics: dict[str, list[Series]],
        changes: dict[str, list[Change]],
        code: dict[str, str],
        network: NetworkFixture,
        memory: list[MemoryFixture],
        dry_run: list[ScriptEntry] | None,
    ) -> None:
        self.case_dir, self.spec = case_dir, spec
        self.logs, self.metrics, self.changes = logs, metrics, changes
        self.code, self.network, self.memory = code, network, memory
        self.dry_run = dry_run
        self.docs = case_dir / "docs"

    def code_files(self, repo: str) -> dict[str, str]:
        prefix = repo + "/"
        return {
            path[len(prefix) :]: text
            for path, text in self.code.items()
            if path.startswith(prefix)
        }

    @classmethod
    def load(cls, case_dir: Path) -> "CaseDataset":
        """Load once before a run; async callers should use asyncio.to_thread."""
        spec = CaseSpec.model_validate(
            yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
        )
        data = case_dir / "data"

        def rows[T](
            kind: str, providers: list[str], adapter: TypeAdapter[T]
        ) -> dict[str, list[T]]:
            result = {}
            for provider in providers:
                path = data / f"{kind}.{provider}.jsonl"
                result[provider] = (
                    [
                        adapter.validate_python(json.loads(line))
                        for line in path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                    if path.exists()
                    else []
                )
            return result

        code: dict[str, str] = {}
        root = data / "code"
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    code[path.relative_to(root).as_posix()] = path.read_text(
                        encoding="utf-8"
                    )

        def optional(name: Path, default: object) -> object:
            return (
                yaml.safe_load(name.read_text(encoding="utf-8"))
                if name.exists()
                else default
            )

        return cls(
            case_dir=case_dir,
            spec=spec,
            logs=rows("logs", spec.providers.logs, TypeAdapter(Row)),
            metrics=rows("metrics", spec.providers.metrics, TypeAdapter(Series)),
            changes=rows("changes", spec.providers.changes, TypeAdapter(Change)),
            code=code,
            network=NetworkFixture.model_validate(
                optional(data / "network.aws.yaml", {})
            ),
            memory=TypeAdapter(list[MemoryFixture]).validate_python(
                optional(case_dir / "memory.yaml", [])
            ),
            dry_run=TypeAdapter(list[ScriptEntry] | None).validate_python(
                optional(case_dir / "dry_run.yaml", None)
            ),
        )


def matches(row: Mapping[str, JsonValue], f: Filter) -> bool:
    for clause in f.clauses:
        value = str(row.get(clause.field or "message", ""))
        if clause.op == "eq" and value != clause.value:
            return False
        if clause.op == "neq" and value == clause.value:
            return False
        if clause.op == "contains" and clause.value.casefold() not in value.casefold():
            return False
    return True


def row_time(row: Mapping[str, JsonValue]) -> datetime:
    return ensure_utc(datetime.fromisoformat(str(row["ts"])))


def in_window(row: Mapping[str, JsonValue], window: TimeWindow) -> bool:
    return window.start <= row_time(row) < window.end
