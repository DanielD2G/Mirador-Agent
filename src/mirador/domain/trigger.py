from typing import Literal

from pydantic import BaseModel, ConfigDict

from mirador.domain.time import TimeWindow

TriggerSource = Literal["cli_question", "cli_alert"]


class Trigger(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source: TriggerSource
    text: str
    service: str | None
    window: TimeWindow | None
    severity: str | None
    links: tuple[str, ...]
    raw_redacted: str | None
    fingerprint: str
