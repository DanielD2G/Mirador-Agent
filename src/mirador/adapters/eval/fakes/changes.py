"""Direct change fixture filtering."""

from mirador.application.ports.capabilities.changes import Change, ChangesSource
from mirador.application.tooling.spec import ToolError
from mirador.domain.time import TimeWindow

from .dataset import CaseDataset


class FakeChanges(ChangesSource):
    def __init__(self, provider: str, dataset: CaseDataset, auth_error: bool) -> None:
        self.provider, self.dataset, self.auth_error = provider, dataset, auth_error

    async def recent_changes(
        self, window: TimeWindow, service: str | None, target: str | None, limit: int
    ) -> list[Change]:
        if self.auth_error:
            raise ToolError(
                "Fixture authentication failure", actionable=False, kind="auth"
            )
        return [
            c
            for c in self.dataset.changes.get(self.provider, [])
            if window.start <= c.at < window.end
            and (
                service is None
                or c.details.get("service") == service
                or c.resource == service
            )
            and (
                target is None
                or c.details.get("target") == target
                or c.resource == target
            )
        ][: max(0, limit)]
