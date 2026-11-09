"""GitHub code reading, search and comparisons."""

import base64
from urllib.parse import quote

from mirador.application.ports.capabilities.code import (
    CodeHit,
    CodeSource,
    Diff,
    DiffFile,
    FileContent,
)
from mirador.application.ports.health import HealthCheckable, HealthResult

from .client import GitHubClient


class GitHubCode(CodeSource, HealthCheckable):
    provider = "github"

    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    async def health_check(self) -> HealthResult:
        return await self.client.health_check()

    async def read_file(self, repo: str, path: str, ref: str) -> FileContent:
        data = (
            await self.client.request(
                self.client.repo_path(repo) + "/contents/" + quote(path, safe=""),
                {"ref": ref},
            )
        ).json()
        if data.get("encoding") == "base64":
            raw = base64.b64decode(data["content"])
        else:
            raw = await self.client.read_raw(
                self.client.repo_path(repo) + "/contents/" + quote(path, safe=""),
                ref,
            )
        return FileContent(
            repo=repo,
            path=path,
            ref=ref,
            content=raw[:200_000].decode("utf-8", errors="replace"),
            truncated=len(raw) > 200_000 or data.get("size", 0) > len(raw),
        )

    async def search_code(self, repo: str, query: str, limit: int) -> list[CodeHit]:
        self.client.repo_path(repo)
        rows = await self.client.pages(
            "/search/code",
            {"q": f"{query} repo:{repo}", "per_page": 100},
            max(0, limit),
            "items",
        )
        return [
            CodeHit(
                repo=repo,
                path=r["path"],
                snippet="\n".join(t["fragment"] for t in r.get("text_matches", [])),
                url=r.get("html_url"),
            )
            for r in rows
            if r["repository"]["full_name"] == repo
        ]

    async def diff(self, repo: str, base: str, head: str) -> Diff:
        data = (
            await self.client.request(
                self.client.repo_path(repo)
                + f"/compare/{quote(base, safe='')}...{quote(head, safe='')}"
            )
        ).json()
        commits = [c["sha"] for c in data["commits"]]
        missing = data.get("total_commits", len(commits)) - len(commits)
        if missing > 0:
            commits.append(f"… {missing} more commits not shown")
        return Diff(
            repo=repo,
            base=base,
            head=head,
            commits=commits,
            files=[
                DiffFile(path=f["filename"], status=f["status"], patch=f.get("patch"))
                for f in data["files"]
            ],
        )
