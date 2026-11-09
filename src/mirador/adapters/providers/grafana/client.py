"""Read-only Grafana HTTP transport."""

from typing import Any, Literal

import httpx

from mirador.application.ports.health import HealthCheckable, HealthResult
from mirador.application.tooling.spec import ToolError


class GrafanaClient(HealthCheckable):
    def __init__(self, url: str, token: str) -> None:
        self.http = httpx.AsyncClient(
            base_url=url.rstrip("/") + "/",
            timeout=httpx.Timeout(15),
            headers={"Authorization": f"Bearer {token}"},
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise ToolError(
                "Grafana request failed", actionable=False, kind="upstream"
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
                else f"Grafana {path}: HTTP {response.status_code}"
            )
            if kind == "permission":
                message += f": {response.text}"
            raise ToolError(message, actionable=kind != "upstream", kind=kind)
        try:
            return response.json()
        except ValueError as exc:
            raise ToolError(
                "Invalid Grafana JSON response", actionable=False, kind="upstream"
            ) from exc

    async def health_check(self) -> HealthResult:
        result = await self.request("GET", "/api/health")
        return HealthResult(
            component="grafana",
            ok=result.get("database") == "ok",
            detail="Grafana database",
        )

    async def datasource_health(self, uid: str) -> HealthResult:
        from urllib.parse import quote

        health = await self.health_check()
        await self.request("GET", f"/api/datasources/uid/{quote(uid, safe='')}")
        return health
