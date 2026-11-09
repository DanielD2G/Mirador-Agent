from typing import Protocol

from pydantic import BaseModel


class FileContent(BaseModel):
    """Typed data exchanged across the application boundary."""

    repo: str
    path: str
    ref: str
    content: str
    truncated: bool


class CodeHit(BaseModel):
    """Typed data exchanged across the application boundary."""

    repo: str
    path: str
    snippet: str
    url: str | None


class DiffFile(BaseModel):
    """Typed data exchanged across the application boundary."""

    path: str
    status: str
    patch: str | None


class Diff(BaseModel):
    """Typed data exchanged across the application boundary."""

    repo: str
    base: str
    head: str
    commits: list[str]
    files: list[DiffFile]


class CodeSource(Protocol):
    """Adapter contract for CodeSource."""

    provider: str

    async def read_file(self, repo: str, path: str, ref: str) -> FileContent:
        """Return the requested data, preserving source identifiers."""
        ...

    async def search_code(self, repo: str, query: str, limit: int) -> list[CodeHit]:
        """Respect the requested limit and return matching results."""
        ...

    async def diff(self, repo: str, base: str, head: str) -> Diff:
        """Return the requested data, preserving source identifiers."""
        ...
