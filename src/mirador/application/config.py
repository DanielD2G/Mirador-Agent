"""Typed startup configuration and environment secrets."""

import re
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SecretStr

from mirador.domain.budget import BudgetLimits


def _duration(value: object) -> object:
    if isinstance(value, str):
        match = re.fullmatch(r"(\d+)(s|m|h|d)", value)
        if match:
            return timedelta(
                seconds=int(match[1])
                * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
            )
    return value


def _bytes(value: object) -> object:
    if isinstance(value, str):
        match = re.fullmatch(r"(\d+)(B|KB|MB|GB)", value)
        if match:
            return int(match[1]) * 1000 ** {"B": 0, "KB": 1, "MB": 2, "GB": 3}[match[2]]
    return value


def _model_names(value: object) -> object:
    return [value] if isinstance(value, str) else value


def _path(value: object) -> object:
    return Path(value).expanduser() if isinstance(value, (str, Path)) else value


Duration = Annotated[timedelta, BeforeValidator(_duration), Field(gt=timedelta(0))]
ModelNames = Annotated[list[str], BeforeValidator(_model_names), Field(min_length=1)]
ExpandedPath = Annotated[Path, BeforeValidator(_path)]


class ConfigSection(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class ModelsConfig(ConfigSection):
    provider: Literal["openrouter"] = "openrouter"
    orchestrator: ModelNames
    subagent: ModelNames
    reporter: ModelNames
    memory_extraction: ModelNames
    embeddings: ModelNames


def _budget(value: object) -> object:
    if isinstance(value, dict):
        return {**value, "time": _duration(value.get("time"))}
    return value


class EvalBudgets(ConfigSection):
    monthly_usd: Decimal = Decimal("50")
    per_run_usd: Decimal = Decimal("10")


class BudgetsConfig(ConfigSection):
    per_investigation: Annotated[BudgetLimits, BeforeValidator(_budget)] = Field(
        default_factory=lambda: BudgetLimits(
            time=timedelta(minutes=10),
            tool_calls=80,
            usd=Decimal("1.00"),
            tokens=2000000,
        )
    )
    monthly_usd: Decimal = Decimal("30")
    eval: EvalBudgets = Field(default_factory=EvalBudgets)


class InvestigationConfig(ConfigSection):
    max_subagents: int = 4
    changes_window: Duration = timedelta(hours=6)


class AwsTarget(ConfigSection):
    log_groups: dict[str, list[str]] = Field(default_factory=dict)
    name: str
    profile: str | None = None
    region: str
    role_arn: str | None = None


class TargetsConfig(ConfigSection):
    aws: list[AwsTarget] = Field(default_factory=list)


class DatadogConfig(ConfigSection):
    site: str = "datadoghq.com"


class GrafanaConfig(ConfigSection):
    url: str = "http://localhost:3000"
    loki_uid: str | None = None
    prometheus_uid: str | None = None


class GithubConfig(ConfigSection):
    service_repos: dict[str, list[str]] = Field(default_factory=dict)
    repos: list[str] = Field(default_factory=list)


class ProvidersConfig(ConfigSection):
    datadog: DatadogConfig = Field(default_factory=DatadogConfig)
    grafana: GrafanaConfig = Field(default_factory=GrafanaConfig)
    github: GithubConfig = Field(default_factory=GithubConfig)


class DocsConfig(ConfigSection):
    path: ExpandedPath = Path("~/docs-equipo")


class WorkspaceConfig(ConfigSection):
    inline_rows_threshold: int = Field(default=50, gt=0)
    max_rows_returned: int = Field(default=200, gt=0)
    query_timeout: Duration = timedelta(seconds=15)
    memory_limit: Annotated[int, BeforeValidator(_bytes), Field(gt=0)] = 1000000000
    retention: Duration = Field(default=timedelta(days=7), ge=timedelta(hours=1))


class GraphitiConfig(ConfigSection):
    falkordb_url: str = "redis://localhost:6379"


class MemoryConfig(ConfigSection):
    graphiti: GraphitiConfig = Field(default_factory=GraphitiConfig)


class AlbAccessLogsConfig(ConfigSection):
    enabled: bool = False
    bucket: str = ""
    prefix: str = ""


class SensitiveToolsConfig(ConfigSection):
    alb_access_logs: AlbAccessLogsConfig = Field(
        default_factory=AlbAccessLogsConfig, alias="aws.alb_access_logs"
    )


class RedactionConfig(ConfigSection):
    extra_patterns: list[str] = Field(default_factory=list)


class MiradorConfig(ConfigSection):
    quotas: dict[str, Annotated[int, Field(gt=0)]] = Field(
        default_factory=lambda: {"datadog-logs": 60}
    )
    models: ModelsConfig
    budgets: BudgetsConfig = Field(default_factory=BudgetsConfig)
    investigation: InvestigationConfig = Field(default_factory=InvestigationConfig)
    targets: TargetsConfig = Field(default_factory=TargetsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    docs: DocsConfig = Field(default_factory=DocsConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    sensitive_tools: SensitiveToolsConfig = Field(default_factory=SensitiveToolsConfig)
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)
    data_dir: ExpandedPath = Path("~/.mirador")


def load_config(path: Path) -> MiradorConfig:
    """Load YAML from an expanded filesystem path and validate all sections."""
    return MiradorConfig.model_validate(yaml.safe_load(path.expanduser().read_text()))


class Secrets(ConfigSection):
    openrouter_api_key: SecretStr
    dd_api_key: SecretStr | None = None
    dd_app_key: SecretStr | None = None
    grafana_token: SecretStr | None = None
    github_token: SecretStr | None = None
    hmac_key: SecretStr | None = None


def load_secrets(env: Mapping[str, str]) -> Secrets:
    """Read only the supported environment variables; never retain other values."""
    names = {
        "openrouter_api_key": "OPENROUTER_API_KEY",
        "dd_api_key": "DD_API_KEY",
        "dd_app_key": "DD_APP_KEY",
        "grafana_token": "GRAFANA_TOKEN",
        "github_token": "GITHUB_TOKEN",
        "hmac_key": "MIRADOR_HMAC_KEY",
    }
    return Secrets.model_validate(
        {field: env[name] for field, name in names.items() if name in env}
    )
