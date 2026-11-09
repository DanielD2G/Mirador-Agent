"""Term-based incident memory with episode support and state aggregation."""

import re
from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from mirador.application.ports.incident_memory import (
    EpisodeState,
    EpisodeSummary,
    IncidentEpisode,
    IncidentMemory,
    MemoryHit,
)
from mirador.domain.time import ensure_utc

from .dataset import CaseDataset, MemoryFixture


class FakeMemory(IncidentMemory):
    def __init__(self, dataset: CaseDataset) -> None:
        self._episodes: dict[str, MemoryFixture] = {}
        for index, episode in enumerate(dataset.memory):
            id = episode.id or f"episode_{index}"
            if id in self._episodes:
                raise ValueError(f"Duplicate memory episode id: {id}")
            self._episodes[id] = episode.model_copy(deep=True)
        self.recorded: list[IncidentEpisode] = []

    async def recall(
        self,
        query: str,
        services: Sequence[str],
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> list[MemoryHit]:
        since = ensure_utc(since) if since is not None else None
        until = ensure_utc(until) if until is not None else None
        terms = set(re.findall(r"\w+", (query + " " + " ".join(services)).casefold()))
        support: dict[str, list[str]] = {}
        for id, episode in self._episodes.items():
            if since is not None and episode.recorded_at < since:
                continue
            if until is not None and episode.recorded_at > until:
                continue
            for fact in episode.facts:
                if not terms or terms.intersection(re.findall(r"\w+", fact.casefold())):
                    support.setdefault(fact, []).append(id)
        hits = []
        for fact, ids in support.items():
            states = [self._episodes[id].state for id in ids]
            if all(state == "rejected" for state in states):
                continue
            hits.append(
                MemoryHit(
                    fact=fact,
                    valid_at=None,
                    invalid_at=None,
                    recorded_at=min(self._episodes[id].recorded_at for id in ids),
                    state="confirmed" if "confirmed" in states else "unverified",
                    episode_ids=ids,
                    conflict=False,
                )
            )
        return sorted(
            hits,
            key=lambda hit: (
                -len(terms.intersection(re.findall(r"\w+", hit.fact.casefold()))),
                hit.fact,
            ),
        )[: max(0, limit)]

    async def record(self, episode: IncidentEpisode) -> str:
        id = str(uuid4())
        stored = episode.model_copy(deep=True)
        self.recorded.append(stored)
        facts = [episode.symptom, *episode.evidence_summary, *episode.fix]
        if episode.root_cause:
            facts.append(episode.root_cause)
        self._episodes[id] = MemoryFixture(
            **stored.model_dump(), id=id, state="unverified", facts=facts
        )
        return id

    async def list_episodes(self, since: datetime | None) -> list[EpisodeSummary]:
        since = ensure_utc(since) if since is not None else None
        return [
            EpisodeSummary(
                id=id,
                investigation_id=e.investigation_id,
                recorded_at=e.recorded_at,
                symptom=e.symptom,
                root_cause=e.root_cause,
                state=e.state,
            )
            for id, e in sorted(
                self._episodes.items(), key=lambda item: item[1].recorded_at
            )
            if since is None or e.recorded_at >= since
        ]

    async def set_state(self, episode_id: str, state: EpisodeState) -> None:
        self._episodes[episode_id] = self._episodes[episode_id].model_copy(
            update={"state": state}
        )
