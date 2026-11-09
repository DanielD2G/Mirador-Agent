"""Metric-name and exact-label fixture selector."""

import re
from datetime import timedelta

from mirador.application.ports.capabilities.metrics import Alert, MetricsSource, Series
from mirador.application.tooling.spec import ToolError
from mirador.domain.time import TimeWindow

from .dataset import CaseDataset


class FakeMetrics(MetricsSource):
    query_syntax = 'metric_name{label="value"}'

    def __init__(self, provider: str, dataset: CaseDataset, auth_error: bool) -> None:
        self.provider, self.dataset, self.auth_error = provider, dataset, auth_error

    def _authorize(self) -> None:
        if self.auth_error:
            raise ToolError(
                "Fixture authentication failure", actionable=False, kind="auth"
            )

    async def query_range(
        self, expr: str, window: TimeWindow, step: timedelta, target: str | None
    ) -> list[Series]:
        self._authorize()
        if step.total_seconds() <= 0:
            raise ToolError("Step must be positive", actionable=True, kind="invalid")
        # Native Datadog and CloudWatch selectors reduce to a name + labels.
        native_labels: dict[str, str] | None = None
        if self.provider == "datadog":
            native = re.fullmatch(r"(?:avg|sum|min|max):([\w.]+)\{([^{}]*)\}", expr)
            if native:
                expr = native[1]
                native_labels = self._labels(native[2], ":")
        elif self.provider == "aws":
            native = re.fullmatch(
                r"[^:{}]+:([^:{}]+)\{([^{}]*)\}:"
                r"(?:Average|Sum|Minimum|Maximum|SampleCount|p\d+(?:\.\d+)?)",
                expr,
            )
            if native:
                expr = native[1]
                native_labels = self._labels(native[2], "=")
        match = re.fullmatch(r"\s*([\w.:/-]+)(?:\{(.*)\})?\s*", expr)
        if match is None:
            raise ToolError(
                "Expected metric name and optional labels",
                actionable=True,
                kind="invalid",
            )
        labels = native_labels if native_labels is not None else {}
        raw = match[2] or ""
        while raw.strip():
            label = re.match(r'\s*([\w.-]+)\s*=\s*"([^"\\]*)"\s*(,|$)', raw)
            if label is None:
                raise ToolError(
                    "Invalid metric labels", actionable=True, kind="invalid"
                )
            labels[label[1]] = label[2]
            raw = raw[label.end() :]
        return [
            series.model_copy(
                update={
                    "points": [
                        p for p in series.points if window.start <= p.ts < window.end
                    ]
                }
            )
            for series in self.dataset.metrics.get(self.provider, [])
            if series.name == match[1]
            and labels.items() <= series.labels.items()
            and (target is None or series.labels.get("target") == target)
        ]

    @staticmethod
    def _labels(raw: str, separator: str) -> dict[str, str]:
        if raw.strip() in {"", "*"}:
            return {}
        labels = {}
        for item in raw.split(","):
            key, found, value = item.partition(separator)
            if not found or not key.strip() or not value.strip():
                raise ToolError(
                    "Invalid metric labels", actionable=True, kind="invalid"
                )
            labels[key.strip()] = value.strip()
        return labels

    async def list_alerts(self, target: str | None) -> list[Alert]:
        self._authorize()
        return []
