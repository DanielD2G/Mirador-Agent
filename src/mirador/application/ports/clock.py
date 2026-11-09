from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware UTC timestamp."""
        ...


class SystemClock:
    def now(self) -> datetime:
        """Return the current aware UTC timestamp."""
        return datetime.now(UTC)
