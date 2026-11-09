from collections.abc import Collection
from typing import Any

from mirador.application.tooling.pipeline import BoundTool, ToolPipeline
from mirador.application.tooling.spec import Capability, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec[Any]] = {}

    def register(self, spec: ToolSpec[Any]) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Duplicate tool: {spec.name}")
        self._specs[spec.name] = spec

    def bound(
        self, pipeline: ToolPipeline, capabilities: Collection[Capability] | None = None
    ) -> list[BoundTool]:
        return [
            pipeline.bind(s)
            for s in self._specs.values()
            if capabilities is None or s.capability in capabilities
        ]

    def bound_by_name(self, pipeline: ToolPipeline, name: str) -> BoundTool:
        return pipeline.bind(self._specs[name])

    def names(self) -> list[str]:
        return list(self._specs)
