from typing import Protocol

from pydantic import BaseModel, JsonValue

from mirador.application.ports.capabilities.logs import LogSearchResult
from mirador.domain.time import TimeWindow


class LoadBalancer(BaseModel):
    """Typed data exchanged across the application boundary."""

    name: str
    arn: str
    dns_name: str
    scheme: str
    listeners: list[str]
    web_acl: str | None


class NatGateway(BaseModel):
    """Typed data exchanged across the application boundary."""

    id: str
    vpc_id: str
    subnet_id: str
    public_ips: list[str]
    private_ips: list[str]


class WafRule(BaseModel):
    """Typed data exchanged across the application boundary."""

    name: str
    priority: int
    action: str
    statement_summary: str


class ServiceStatus(BaseModel):
    """Typed data exchanged across the application boundary."""

    cluster: str
    service: str
    desired: int
    running: int
    deployments: list[dict[str, JsonValue]]
    events: list[str]


class NetworkSource(Protocol):
    """Adapter contract for NetworkSource."""

    provider: str

    async def load_balancers(self, target: str | None) -> list[LoadBalancer]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def nat_gateways(self, target: str | None) -> list[NatGateway]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def waf_rules(self, web_acl: str, target: str | None) -> list[WafRule]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def waf_sampled_requests(
        self,
        web_acl: str,
        rule: str | None,
        window: TimeWindow,
        limit: int,
        target: str | None,
    ) -> LogSearchResult:
        """Respect the requested limit and return matching results."""
        ...

    async def service_status(
        self, cluster: str, service: str, target: str | None
    ) -> ServiceStatus:
        """Return the requested data, preserving source identifiers."""
        ...
