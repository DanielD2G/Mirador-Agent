"""Boundary for redacting externally sourced data."""

from typing import Protocol

from pydantic import JsonValue


class Redactor(Protocol):
    def redact_text(self, text: str) -> str: ...

    def redact_json(self, value: JsonValue) -> JsonValue: ...
