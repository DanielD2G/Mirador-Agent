from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, field_validator

from mirador.domain.report import Confidence
from mirador.domain.time import ensure_utc

EpisodeState = Literal["unverified", "confirmed", "rejected"]


class IncidentEpisode(BaseModel):
    """Typed data exchanged across the application boundary."""

    investigation_id: str
    recorded_at: datetime
    services: list[str]
    symptom: str
    root_cause: str | None
    confidence: Confidence | None
    evidence_summary: list[str]
    fix: list[str]

    _utc = field_validator("recorded_at")(ensure_utc)


class MemoryHit(BaseModel):
    """Typed data exchanged across the application boundary."""

    fact: str
    valid_at: datetime | None

    @field_validator("valid_at")
    @classmethod
    def validate_valid_at(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    invalid_at: datetime | None

    @field_validator("invalid_at")
    @classmethod
    def validate_invalid_at(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    recorded_at: datetime
    state: EpisodeState
    episode_ids: list[str]
    conflict: bool

    _utc = field_validator("recorded_at")(ensure_utc)


class EpisodeSummary(BaseModel):
    """Typed data exchanged across the application boundary."""

    id: str
    investigation_id: str
    recorded_at: datetime
    symptom: str
    root_cause: str | None
    state: EpisodeState

    _utc = field_validator("recorded_at")(ensure_utc)


class IncidentMemory(Protocol):
    """Adapter contract for IncidentMemory."""

    async def recall(
        self,
        query: str,
        services: Sequence[str],
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> list[MemoryHit]:
        """Respect the requested limit and return matching results."""
        ...

    async def record(self, episode: IncidentEpisode) -> str:
        """Persist the supplied record before returning."""
        ...

    async def list_episodes(self, since: datetime | None) -> list[EpisodeSummary]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def set_state(self, episode_id: str, state: EpisodeState) -> None:
        """Apply the requested episode state before returning."""
        ...


class EpisodeStateRepository(Protocol):
    """Adapter contract for EpisodeStateRepository."""

    async def save_episode(self, summary: EpisodeSummary) -> None:
        """Persist the supplied record before returning."""
        ...

    async def episode_states(self, ids: Sequence[str]) -> dict[str, EpisodeState]:
        """Return the requested data, preserving source identifiers."""
        ...

    async def set_episode_state(self, id: str, state: EpisodeState) -> None:
        """Apply the requested episode state before returning."""
        ...

    async def list_episodes(self, since: datetime | None) -> list[EpisodeSummary]:
        """Return the requested data, preserving source identifiers."""
        ...
