from typing import Protocol

from pydantic import BaseModel


class HealthResult(BaseModel):
    """Typed data exchanged across the application boundary."""

    component: str
    ok: bool
    detail: str


class HealthCheckable(Protocol):
    """Adapter contract for HealthCheckable."""

    async def health_check(self) -> HealthResult:
        """Return the requested data, preserving source identifiers."""
        ...
