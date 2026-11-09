"""Read-only GitHub transport with repository access boundaries."""

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import httpx

from mirador.application.ports.health import HealthCheckable, HealthResult
from mirador.application.tooling.spec import ToolError


class GitHubClient(HealthCheckable):
    def __init__(self, token: str, repos: Sequence[str]) -> None:
        self.repos = tuple(repos)
        self.branches: dict[str, str] = {}
        self.http = httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=15,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def repo_path(self, repo: str) -> str:
        if repo not in self.repos:
            raise ToolError(
                "Repository is not configured", actionable=True, kind="permission"
            )
        return "/repos/" + quote(repo, safe="/")

    async def aclose(self) -> None:
        await self.http.aclose()

    async def request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        if httpx.URL(path).is_absolute_url and httpx.URL(path).host != "api.github.com":
            raise ToolError(
                "Invalid pagination host", actionable=False, kind="upstream"
            )
        try:
            response = await self.http.get(path, params=params)
        except httpx.RequestError as exc:
            raise ToolError(
                "GitHub request failed", actionable=False, kind="upstream"
            ) from exc
        self.check_response(response)
        return response

    @staticmethod
    def check_response(response: httpx.Response) -> None:
        status = response.status_code
        if response.is_error:
            if status == 401:
                raise ToolError(
                    "credentials rejected or expired: re-run SSO login / check token",
                    actionable=True,
                    kind="auth",
                )
            if status == 429 or (
                status == 403
                and (
                    response.headers.get("x-ratelimit-remaining") == "0"
                    or "retry-after" in response.headers
                )
            ):
                raise ToolError(
                    "GitHub rate limit exceeded", actionable=True, kind="rate_limited"
                )
            if status == 403:
                raise ToolError(
                    f"GitHub permission denied: {response.text}",
                    actionable=True,
                    kind="permission",
                )
            if status == 404:
                raise ToolError(
                    "GitHub resource not found", actionable=True, kind="not_found"
                )
            raise ToolError(
                f"GitHub HTTP {status}",
                actionable=False,
                kind="upstream" if status >= 500 else "invalid",
            )

    async def read_raw(self, path: str, ref: str) -> bytes:
        try:
            async with self.http.stream(
                "GET",
                path,
                params={"ref": ref},
                headers={"Accept": "application/vnd.github.raw"},
            ) as response:
                if response.is_error:
                    await response.aread()
                self.check_response(response)
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=200_001):
                    raw.extend(chunk[: 200_001 - len(raw)])
                    if len(raw) >= 200_001:
                        break
                return bytes(raw)
        except httpx.RequestError as exc:
            raise ToolError(
                "GitHub request failed", actionable=False, kind="upstream"
            ) from exc

    async def pages(
        self,
        path: str,
        params: dict[str, Any],
        limit: int | None = None,
        key: str | None = None,
    ) -> list[Any]:
        rows: list[Any] = []
        while path and (limit is None or len(rows) < limit):
            response = await self.request(path, params or None)
            data = response.json()
            rows.extend(data[key] if key else data)
            path = response.links.get("next", {}).get("url", "")
            params = {}
        return rows if limit is None else rows[:limit]

    async def default_branch(self, repo: str) -> str:
        path = self.repo_path(repo)
        if repo not in self.branches:
            self.branches[repo] = (await self.request(path)).json()["default_branch"]
        return self.branches[repo]

    async def health_check(self) -> HealthResult:
        await self.request("/user")
        for repo in self.repos:
            await self.request(self.repo_path(repo))
        return HealthResult(
            component="github",
            ok=True,
            detail="Account and configured repositories accessible",
        )
