from collections.abc import Sequence
from typing import Any

from mirador.application.ports.capabilities.network import NetworkSource
from mirador.application.tooling.spec import (
    DataOutput,
    RowsOutput,
    ToolCallContext,
    ToolOutput,
    ToolSpec,
)

from ._common import LIMIT, WINDOW, model, spec, target, window


def network_tools(src: NetworkSource, targets: Sequence[str]) -> list[ToolSpec[Any]]:
    tools: list[ToolSpec[Any]] = []
    inputs = model("NetworkLoadBalancers", targets)

    async def load_balancers(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.load_balancers(target(a, targets))
        return DataOutput(
            kind="data", data=[item.model_dump(mode="json") for item in result]
        )

    tools.append(
        spec(
            f"network.{src.provider}.load_balancers",
            "network",
            inputs,
            load_balancers,
            "Return load balancers results; use to investigate network.",
            hours=None,
            medium=False,
        )
    )
    inputs = model("NetworkNatGateways", targets)

    async def nat_gateways(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.nat_gateways(target(a, targets))
        return DataOutput(
            kind="data", data=[item.model_dump(mode="json") for item in result]
        )

    tools.append(
        spec(
            f"network.{src.provider}.nat_gateways",
            "network",
            inputs,
            nat_gateways,
            "Return nat gateways results; use to investigate network.",
            hours=None,
            medium=False,
        )
    )
    inputs = model("NetworkWafRules", targets, web_acl=(str, ...))

    async def waf_rules(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.waf_rules(a.web_acl, target(a, targets))
        return DataOutput(
            kind="data", data=[item.model_dump(mode="json") for item in result]
        )

    tools.append(
        spec(
            f"network.{src.provider}.waf_rules",
            "network",
            inputs,
            waf_rules,
            "Return waf rules results; use to investigate network.",
            hours=None,
            medium=False,
        )
    )
    inputs = model(
        "NetworkWafSampledRequests",
        targets,
        **WINDOW,
        web_acl=(str, ...),
        rule=(str | None, None),
        limit=LIMIT,
    )

    async def waf_sampled_requests(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.waf_sampled_requests(
            a.web_acl, a.rule, window(a), a.limit, target(a, targets)
        )
        return RowsOutput(
            kind="rows",
            rows=result.rows,
            truncated=result.truncated,
            table_hint=f"network_{src.provider}",
        )

    tools.append(
        spec(
            f"network.{src.provider}.waf_sampled_requests",
            "network",
            inputs,
            waf_sampled_requests,
            "Return waf sampled requests results; use to investigate network.",
            hours=None,
            medium=False,
        )
    )
    inputs = model(
        "NetworkServiceStatus", targets, cluster=(str, ...), service=(str, ...)
    )

    async def service_status(a: Any, ctx: ToolCallContext) -> ToolOutput:
        result = await src.service_status(a.cluster, a.service, target(a, targets))
        return DataOutput(kind="data", data=result.model_dump(mode="json"))

    tools.append(
        spec(
            f"network.{src.provider}.service_status",
            "network",
            inputs,
            service_status,
            "Return service status results; use to investigate network.",
            hours=None,
            medium=False,
        )
    )
    return tools
