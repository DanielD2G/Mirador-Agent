"""AWS network fixture queries."""

from mirador.application.ports.capabilities.logs import LogSearchResult
from mirador.application.ports.capabilities.network import (
    LoadBalancer,
    NatGateway,
    NetworkSource,
    ServiceStatus,
    WafRule,
)
from mirador.application.tooling.spec import ToolError
from mirador.domain.time import TimeWindow

from .dataset import CaseDataset, in_window


class FakeNetwork(NetworkSource):
    def __init__(self, provider: str, dataset: CaseDataset, auth_error: bool) -> None:
        self.provider, self.dataset, self.auth_error = provider, dataset, auth_error

    def _authorize(self) -> None:
        if self.auth_error:
            raise ToolError(
                "Fixture authentication failure", actionable=False, kind="auth"
            )

    async def load_balancers(self, target: str | None) -> list[LoadBalancer]:
        self._authorize()
        return [
            lb
            for lb in self.dataset.network.load_balancers
            if target is None or lb.target is None or lb.target == target
        ]

    async def nat_gateways(self, target: str | None) -> list[NatGateway]:
        self._authorize()
        return [
            nat
            for nat in self.dataset.network.nat_gateways
            if target is None or nat.target is None or nat.target == target
        ]

    async def waf_rules(self, web_acl: str, target: str | None) -> list[WafRule]:
        self._authorize()
        return [
            rule
            for rule in self.dataset.network.waf_rules.get(web_acl, [])
            if target is None or rule.target is None or rule.target == target
        ]

    async def waf_sampled_requests(
        self,
        web_acl: str,
        rule: str | None,
        window: TimeWindow,
        limit: int,
        target: str | None,
    ) -> LogSearchResult:
        self._authorize()
        rows = [
            r
            for r in self.dataset.network.sampled_requests
            if r.get("web_acl") == web_acl
            and (rule is None or r.get("rule") == rule)
            and in_window(r, window)
            and (target is None or r.get("target") is None or r.get("target") == target)
        ]
        return LogSearchResult(
            rows=rows[: max(0, limit)], truncated=len(rows) > max(0, limit)
        )

    async def service_status(
        self, cluster: str, service: str, target: str | None
    ) -> ServiceStatus:
        self._authorize()
        for status in self.dataset.network.services:
            if (
                status.cluster == cluster
                and status.service == service
                and (target is None or status.target is None or status.target == target)
            ):
                return status
        raise ToolError("Fixture service not found", actionable=True, kind="not_found")
