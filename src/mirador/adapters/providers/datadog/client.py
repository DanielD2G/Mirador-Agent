"""Read-only Datadog HTTP transport."""

from typing import Any, Literal

import httpx

from mirador.application.ports.health import HealthCheckable, HealthResult
from mirador.application.tooling.spec import ToolError


class DatadogClient(HealthCheckable):
    def __init__(self, site: str, api_key: str, app_key: str) -> None:
        self.http = httpx.AsyncClient(
            base_url=f"https://api.{site}",
            timeout=httpx.Timeout(15),
            headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key},
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise ToolError(
                "Datadog request failed", actionable=False, kind="upstream"
            ) from exc
        if response.is_error:
            kind: Literal[
                "auth", "permission", "not_found", "rate_limited", "invalid", "upstream"
            ]
            kind = {
                401: "auth",
                403: "permission",
                404: "not_found",
                429: "rate_limited",
            }.get(
                response.status_code,
                "upstream" if response.status_code >= 500 else "invalid",
            )  # type: ignore[assignment]
            message = (
                "credentials rejected or expired: re-run SSO login / check token"
                if kind == "auth"
                else f"Datadog {path}: HTTP {response.status_code}"
            )
            if kind == "permission":
                message += f": {response.text}"
            raise ToolError(message, actionable=kind != "upstream", kind=kind)
        try:
            return response.json()
        except ValueError as exc:
            raise ToolError(
                "Invalid Datadog JSON response", actionable=False, kind="upstream"
            ) from exc

    async def health_check(self) -> HealthResult:
        result = await self.request("GET", "/api/v1/validate")
        return HealthResult(
            component="datadog",
            ok=result.get("valid") is True,
            detail="API key validation",
        )
