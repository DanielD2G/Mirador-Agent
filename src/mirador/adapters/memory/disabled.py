"""Explicit unavailable-memory fallback."""

from collections.abc import Sequence
from datetime import datetime

from mirador.application.ports.health import HealthResult
from mirador.application.ports.incident_memory import (
    EpisodeState,
    EpisodeSummary,
    IncidentEpisode,
    MemoryHit,
)


class DisabledIncidentMemory:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def recall(
        self,
        query: str,
        services: Sequence[str],
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> list[MemoryHit]:
        return []

    async def list_episodes(self, since: datetime | None) -> list[EpisodeSummary]:
        return []

    async def record(self, episode: IncidentEpisode) -> str:
        raise RuntimeError("memory backend unavailable")

    async def set_state(self, episode_id: str, state: EpisodeState) -> None:
        raise RuntimeError("memory backend unavailable")

    async def health_check(self) -> HealthResult:
        return HealthResult(component="memory", ok=False, detail=self.reason)
