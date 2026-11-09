"""Code snapshot fixtures, without fabricated revision history."""

from mirador.application.ports.capabilities.code import (
    CodeHit,
    CodeSource,
    Diff,
    FileContent,
)
from mirador.application.tooling.spec import ToolError

from .dataset import CaseDataset


class FakeCode(CodeSource):
    def __init__(self, provider: str, dataset: CaseDataset, auth_error: bool) -> None:
        self.provider, self.dataset, self.auth_error = provider, dataset, auth_error

    def _authorize(self) -> None:
        if self.auth_error:
            raise ToolError(
                "Fixture authentication failure", actionable=False, kind="auth"
            )

    async def read_file(self, repo: str, path: str, ref: str) -> FileContent:
        self._authorize()
        content = self.dataset.code_files(repo).get(path)
        if content is None:
            raise ToolError("Fixture file not found", actionable=True, kind="not_found")
        return FileContent(
            repo=repo, path=path, ref=ref, content=content, truncated=False
        )

    async def search_code(self, repo: str, query: str, limit: int) -> list[CodeHit]:
        self._authorize()
        return [
            CodeHit(repo=repo, path=path, snippet=content, url=None)
            for path, content in sorted(self.dataset.code_files(repo).items())
            if query.casefold() in content.casefold()
            or query.casefold() in path.casefold()
        ][: max(0, limit)]

    async def diff(self, repo: str, base: str, head: str) -> Diff:
        self._authorize()
        raise ToolError(
            "Code fixtures have no revision history", actionable=True, kind="not_found"
        )
