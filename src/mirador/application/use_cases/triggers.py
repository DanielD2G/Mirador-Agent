"""Deterministic, redacted CLI trigger construction."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from mirador.application.ports.clock import Clock
from mirador.application.ports.redaction import Redactor
from mirador.domain.time import TimeWindow
from mirador.domain.trigger import Trigger, TriggerSource


def _build(
    text: str,
    source: TriggerSource,
    service: str | None = None,
    links: tuple[str, ...] = (),
    window: TimeWindow | None = None,
    raw: str | None = None,
) -> Trigger:
    normalized = " ".join(text.casefold().split())
    return Trigger(
        id=str(uuid4()),
        source=source,
        text=text,
        service=service,
        window=window,
        severity=None,
        links=links,
        raw_redacted=raw,
        fingerprint=hashlib.sha256(
            (source + (service or "") + normalized).encode()
        ).hexdigest(),
    )


def trigger_from_question(text: str, redactor: Redactor, clock: Clock) -> Trigger:
    return _build(redactor.redact_text(text), "cli_question")


def trigger_from_alert(payload: str, redactor: Redactor, clock: Clock) -> Trigger:
    raw = redactor.redact_text(payload)
    try:
        data = json.loads(raw)
    except ValueError:
        return _build(raw, "cli_alert", raw=raw)
    if not isinstance(data, dict):
        return _build(raw, "cli_alert", raw=raw)
    title = data.get("title")
    service = None
    link = None
    start = None
    labels = data.get("commonLabels")
    if isinstance(labels, dict) and isinstance(title, str):
        service = labels.get("service") or labels.get("job")
        start = data.get("startsAt")
        link = data.get("externalURL")
    elif isinstance(data.get("tags"), list) and isinstance(title, str):
        service = next(
            (
                t[8:]
                for t in data["tags"]
                if isinstance(t, str) and t.startswith("service:")
            ),
            None,
        )
        start = data.get("date")
        link = data.get("link")
    else:
        return _build(raw, "cli_alert", raw=raw)
    window = None
    try:
        at = (
            datetime.fromtimestamp(start, UTC)
            if isinstance(start, (int, float))
            else datetime.fromisoformat(start.replace("Z", "+00:00"))
            if isinstance(start, str)
            else None
        )
        if at is not None and at.tzinfo is not None and at < clock.now():
            window = TimeWindow(start=at, end=clock.now())
    except (ValueError, OverflowError, OSError):
        pass
    return _build(
        title,
        "cli_alert",
        service if isinstance(service, str) else None,
        (link,) if isinstance(link, str) else (),
        window,
        raw,
    )
