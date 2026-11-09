from datetime import datetime

from mirador.application.ports.incident_memory import EpisodeSummary, IncidentMemory


class MemoryAdminUseCase:
    def __init__(self, memory: IncidentMemory) -> None:
        self.memory = memory

    async def list(self, since: datetime | None) -> list[EpisodeSummary]:
        return await self.memory.list_episodes(since)

    async def confirm(self, id: str) -> None:
        await self.memory.set_state(id, "confirmed")

    async def reject(self, id: str) -> None:
        await self.memory.set_state(id, "rejected")
