"""Small provider-independent conjunction language for log searches."""

import re
from typing import Literal

from pydantic import BaseModel


class Clause(BaseModel, frozen=True):
    field: str | None
    op: Literal["eq", "neq", "contains"]
    value: str


class Filter(BaseModel, frozen=True):
    clauses: tuple[Clause, ...]


class FilterSyntaxError(ValueError):
    """The expression does not follow the supported filter grammar."""


_VALUE_PATTERN = r'(?:"[^"\n]*"|[^\s":!~=]+)'
_CLAUSE = re.compile(
    rf'(?:(?P<field>[^\s":!~=]+)(?P<op>!=|:|~))?(?P<value>{_VALUE_PATTERN})'
)
_ERROR = (
    "Expected clauses separated by AND: field:value, field!=value, field~value, "
    "or free text; quote values containing spaces with double quotes."
)


def parse_filter(text: str) -> Filter:
    """Parse conjunctions, rejecting unsupported syntax and missing separators."""
    if not text.strip():
        return Filter(clauses=())
    clauses: list[Clause] = []
    position = 0
    while position < len(text):
        match = _CLAUSE.match(text, position)
        if match is None:
            raise FilterSyntaxError(_ERROR)
        value = match["value"]
        op: Literal["eq", "neq", "contains"] = {
            ":": "eq",
            "!=": "neq",
            "~": "contains",
        }.get(match["op"], "contains")  # type: ignore[assignment]
        clauses.append(
            Clause(
                field=match["field"],
                op=op,
                value=value[1:-1] if value.startswith('"') else value,
            )
        )
        position = match.end()
        if position == len(text):
            break
        if not text.startswith(" AND ", position):
            raise FilterSyntaxError(_ERROR)
        position += 5
        if position == len(text):
            raise FilterSyntaxError(_ERROR)
    return Filter(clauses=tuple(clauses))
