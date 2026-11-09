"""Read-only AWS network inventory and service status."""

import json
from typing import Any

from mirador.application.ports.capabilities.logs import LogSearchResult
from mirador.application.ports.capabilities.network import (
    LoadBalancer,
    NatGateway,
    NetworkSource,
    ServiceStatus,
    WafRule,
)
from mirador.application.ports.health import HealthResult
from mirador.application.tooling.spec import ToolError
from mirador.domain.time import TimeWindow

from .session import AwsSessions


def acl_parameters(arn: str) -> dict[str, str]:
    parts = arn.split("/")
    if len(parts) != 4 or parts[1] != "webacl":
        raise ToolError("Expected a WAFv2 web ACL ARN", actionable=True, kind="invalid")
    return {
        "Name": parts[2],
        "Id": parts[3],
        "Scope": "CLOUDFRONT" if parts[0].endswith(":global") else "REGIONAL",
    }


class AwsNetwork(NetworkSource):
    provider = "aws"

    def __init__(self, sessions: AwsSessions) -> None:
        self.sessions = sessions

    async def health_check(self) -> HealthResult:
        return await self.sessions.health_check()

    async def load_balancers(self, target: str | None) -> list[LoadBalancer]:
        result = []
        params: dict[str, Any] = {}
        async with self.sessions.client("elbv2", target) as client:
            while True:
                response = await client.describe_load_balancers(**params)
                for row in response.get("LoadBalancers", []):
                    listeners: list[str] = []
                    marker: dict[str, Any] = {}
                    while True:
                        page = await client.describe_listeners(
                            LoadBalancerArn=row["LoadBalancerArn"], **marker
                        )
                        listeners.extend(
                            f"{v['Protocol']}:{v['Port']}"
                            for v in page.get("Listeners", [])
                        )
                        if not page.get("NextMarker"):
                            break
                        marker["Marker"] = page["NextMarker"]
                    acl = None
                    if row.get("Type") == "application":
                        async with self.sessions.client("wafv2", target) as waf:
                            association = await waf.get_web_acl_for_resource(
                                ResourceArn=row["LoadBalancerArn"]
                            )
                            acl = association.get("WebACL", {}).get("ARN")
                    result.append(
                        LoadBalancer(
                            name=row["LoadBalancerName"],
                            arn=row["LoadBalancerArn"],
                            dns_name=row["DNSName"],
                            scheme=row["Scheme"],
                            listeners=listeners,
                            web_acl=acl,
                        )
                    )
                if not response.get("NextMarker"):
                    break
                params["Marker"] = response["NextMarker"]
        return result

    async def nat_gateways(self, target: str | None) -> list[NatGateway]:
        result = []
        params: dict[str, Any] = {}
        async with self.sessions.client("ec2", target) as client:
            while True:
                response = await client.describe_nat_gateways(**params)
                for row in response.get("NatGateways", []):
                    addresses = row.get("NatGatewayAddresses", [])
                    result.append(
                        NatGateway(
                            id=row["NatGatewayId"],
                            vpc_id=row["VpcId"],
                            subnet_id=row["SubnetId"],
                            public_ips=[
                                v["PublicIp"] for v in addresses if "PublicIp" in v
                            ],
                            private_ips=[
                                v["PrivateIp"] for v in addresses if "PrivateIp" in v
                            ],
                        )
                    )
                if not response.get("NextToken"):
                    break
                params["NextToken"] = response["NextToken"]
        return result

    async def waf_rules(self, web_acl: str, target: str | None) -> list[WafRule]:
        async with self.sessions.client("wafv2", target) as client:
            response = await client.get_web_acl(**acl_parameters(web_acl))
        return [
            WafRule(
                name=r["Name"],
                priority=r["Priority"],
                action=json.dumps(r.get("Action", r.get("OverrideAction", {}))),
                statement_summary=json.dumps(r["Statement"]),
            )
            for r in response["WebACL"].get("Rules", [])
        ]

    async def waf_sampled_requests(
        self,
        web_acl: str,
        rule: str | None,
        window: TimeWindow,
        limit: int,
        target: str | None,
    ) -> LogSearchResult:
        if window.duration().total_seconds() > 10800:
            raise ToolError(
                "WAF sampled requests supports at most 3 hours",
                actionable=True,
                kind="invalid",
            )
        if not 1 <= limit <= 500:
            raise ToolError("WAF limit must be 1..500", actionable=True, kind="invalid")
        async with self.sessions.client("wafv2", target) as client:
            acl = await client.get_web_acl(**acl_parameters(web_acl))
            rules = acl["WebACL"].get("Rules", [])
            rows = []
            for item in rules:
                if rule and item["Name"] != rule:
                    continue
                response = await client.get_sampled_requests(
                    WebAclArn=web_acl,
                    RuleMetricName=item["VisibilityConfig"]["MetricName"],
                    Scope=acl_parameters(web_acl)["Scope"],
                    TimeWindow={"StartTime": window.start, "EndTime": window.end},
                    MaxItems=min(limit + 1, 500),
                )
                rows.extend(
                    json.loads(
                        json.dumps(response.get("SampledRequests", []), default=str)
                    )
                )
        return LogSearchResult(rows=rows[:limit], truncated=len(rows) >= limit)

    async def service_status(
        self, cluster: str, service: str, target: str | None
    ) -> ServiceStatus:
        async with self.sessions.client("ecs", target) as client:
            response = await client.describe_services(
                cluster=cluster, services=[service]
            )
        if not response.get("services"):
            raise ToolError(
                f"ECS service not found: {response.get('failures', [])}",
                actionable=True,
                kind="not_found",
            )
        row = response["services"][0]
        return ServiceStatus(
            cluster=cluster,
            service=service,
            desired=row["desiredCount"],
            running=row["runningCount"],
            deployments=json.loads(json.dumps(row.get("deployments", []), default=str)),
            events=[r["message"] for r in row.get("events", [])[:20]],
        )
