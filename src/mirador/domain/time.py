from datetime import UTC, datetime, timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Datetime must be timezone-aware")
    return dt.astimezone(UTC)


class TimeWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: datetime
    end: datetime

    _utc = field_validator("start", "end")(ensure_utc)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Window start must precede end")
        return self

    def duration(self) -> timedelta:
        return self.end - self.start
