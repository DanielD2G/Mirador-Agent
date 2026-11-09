from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mirador.domain.time import ensure_utc


class BudgetLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    time: timedelta = Field(gt=timedelta(0))
    tool_calls: int
    usd: Decimal
    tokens: int


class Spend(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_calls: int
    tokens: int
    usd: Decimal
    elapsed: timedelta


class BudgetExceeded(Exception):
    def __init__(
        self, reason: Literal["time", "tool_calls", "usd", "tokens", "monthly_usd"]
    ) -> None:
        self.reason = reason
        super().__init__(reason)


class BudgetTracker:
    def __init__(self, limits: BudgetLimits, started_at: datetime) -> None:
        self.limits = limits
        self.started_at = ensure_utc(started_at)
        self._tool_calls = 0
        self._tokens = 0
        self._usd = Decimal(0)
        self._elapsed = timedelta(0)
        self._warning_emitted = False

    def charge_tool_call(self, now: datetime) -> None:
        self._tool_calls += 1
        self.check(now)

    def charge_llm(self, tokens: int, usd: Decimal, now: datetime) -> None:
        if tokens < 0 or usd < 0:
            raise ValueError("Charges must be nonnegative")
        self._tokens += tokens
        self._usd += usd
        self.check(now)

    def check(self, now: datetime) -> None:
        self._elapsed = max(timedelta(0), ensure_utc(now) - self.started_at)
        if self._elapsed >= self.limits.time:
            raise BudgetExceeded("time")
        if self._tool_calls >= self.limits.tool_calls:
            raise BudgetExceeded("tool_calls")
        if self._usd >= self.limits.usd:
            raise BudgetExceeded("usd")
        if self._tokens >= self.limits.tokens:
            raise BudgetExceeded("tokens")

    def snapshot(self) -> Spend:
        return Spend(
            tool_calls=self._tool_calls,
            tokens=self._tokens,
            usd=self._usd,
            elapsed=self._elapsed,
        )

    def remaining_time(self, now: datetime) -> timedelta:
        elapsed = ensure_utc(now) - self.started_at
        return max(timedelta(0), self.limits.time - max(timedelta(0), elapsed))

    def warning(self, now: datetime) -> bool:
        elapsed = max(timedelta(0), ensure_utc(now) - self.started_at)
        return (
            elapsed >= self.limits.time * 0.8
            or self._tool_calls * 5 >= self.limits.tool_calls * 4
            or self._tokens * 5 >= self.limits.tokens * 4
            or self._usd * 5 >= self.limits.usd * 4
        )

    def take_warning(self, now: datetime) -> bool:
        if not self._warning_emitted and self.warning(now):
            self._warning_emitted = True
            return True
        return False
