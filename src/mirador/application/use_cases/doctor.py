"""Read-only diagnostics over supplied health probes."""

from collections.abc import Sequence

from mirador.application.ports.health import HealthCheckable, HealthResult


class DoctorUseCase:
    def __init__(self, components: Sequence[HealthCheckable]) -> None:
        self.components = components

    async def execute(self) -> list[HealthResult]:
        results = []
        for component in self.components:
            try:
                results.append(await component.health_check())
            except Exception as exc:
                results.append(
                    HealthResult(
                        component=type(component).__name__, ok=False, detail=str(exc)
                    )
                )
        return results
