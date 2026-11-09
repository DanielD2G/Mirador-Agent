from typing import Protocol

from pydantic import BaseModel


class DocHit(BaseModel):
    """Typed data exchanged across the application boundary."""

    path: str
    heading: str | None
    snippet: str
    score: float


class DocPage(BaseModel):
    """Typed data exchanged across the application boundary."""

    path: str
    heading: str | None
    content: str


class DocsSource(Protocol):
    """Adapter contract for DocsSource."""

    async def search(self, query: str, limit: int) -> list[DocHit]:
        """Respect the requested limit and return matching results."""
        ...

    async def read(self, path: str, heading: str | None) -> DocPage:
        """Return the requested data, preserving source identifiers."""
        ...
