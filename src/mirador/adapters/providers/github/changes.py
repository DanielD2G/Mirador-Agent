"""Merge recent GitHub events across repositories and kinds."""

from collections.abc import Mapping, Sequence
from datetime import datetime

from mirador.application.ports.capabilities.changes import Change, ChangesSource
from mirador.application.ports.health import HealthCheckable, HealthResult
from mirador.domain.time import TimeWindow

from .client import GitHubClient


class GitHubChanges(ChangesSource, HealthCheckable):
    provider = "github"

    def __init__(
        self,
        client: GitHubClient,
        service_repos: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.client = client
        self.service_repos = service_repos or {}

    async def health_check(self) -> HealthResult:
        return await self.client.health_check()

    async def recent_changes(
        self, window: TimeWindow, service: str | None, target: str | None, limit: int
    ) -> list[Change]:
        if limit <= 0:
            return []
        changes = []
        for repo in self.service_repos.get(service or "", self.client.repos):
            root = self.client.repo_path(repo)
            branch = await self.client.default_branch(repo)
            for endpoint, kind in [
                ("commits", "commit"),
                ("pulls", "pull_request_merged"),
                ("deployments", "deployment"),
                ("releases", "release"),
            ]:
                params: dict[str, str | int] = {"per_page": 100}
                if endpoint == "commits":
                    params.update(
                        sha=branch,
                        since=window.start.isoformat(),
                        until=window.end.isoformat(),
                    )
                if endpoint == "pulls":
                    params.update(state="closed", sort="updated", direction="desc")
                count = 0
                path = root + "/" + endpoint
                while path:
                    response = await self.client.request(path, params or None)
                    rows = response.json()
                    stop = False
                    for row in rows:
                        cutoff = row.get(
                            {
                                "pulls": "updated_at",
                                "deployments": "created_at",
                                "releases": "published_at",
                            }.get(endpoint, "")
                        )
                        if (
                            cutoff
                            and datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
                            < window.start
                        ):
                            stop = True
                            break
                        timestamp = (
                            row["commit"]["committer"]["date"]
                            if endpoint == "commits"
                            else row.get(
                                {
                                    "pulls": "merged_at",
                                    "deployments": "created_at",
                                    "releases": "published_at",
                                }[endpoint]
                            )
                        )
                        if not timestamp:
                            continue
                        at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        if not window.start <= at <= window.end:
                            continue
                        actor = (
                            row.get("author")
                            if endpoint == "commits"
                            else row.get("user", row.get("creator", row.get("author")))
                        )
                        changes.append(
                            Change(
                                at=at,
                                source="github",
                                actor=actor.get("login") if actor else None,
                                kind=kind,
                                resource=repo,
                                summary=row["commit"]["message"]
                                if endpoint == "commits"
                                else row.get(
                                    "title",
                                    row.get("name", row.get("description", "")),
                                )
                                or "",
                                details={"id": str(row.get("sha", row.get("id", "")))},
                                link=row.get("html_url"),
                            )
                        )
                        count += 1
                        if count >= limit:
                            stop = True
                            break
                    if stop:
                        break
                    path = response.links.get("next", {}).get("url", "")
                    params = {}
        return sorted(changes, key=lambda c: c.at, reverse=True)[:limit]
