"""Deterministic, local-key pseudonymization of text and JSON values."""

import hashlib
import hmac
import json
import math
import re
from collections import Counter
from collections.abc import Sequence

from pydantic import JsonValue

from mirador.redaction.patterns import ENTROPY_CONTEXT, PRESERVE, SECRETS

_TOKEN = re.compile(r"<[A-Z_]+_[0-9a-f]{4}>")
_SENSITIVE = re.compile(r"authorization|api[-_]?key|token|secret|password|cookie", re.I)
_PAIR_NAME_KEYS = frozenset({"name", "header", "field"})


class Pseudonymizer:
    def __init__(self, hmac_key: bytes, extra_patterns: Sequence[str] = ()) -> None:
        if not hmac_key:
            raise ValueError("hmac_key must not be empty")
        self._key = hmac_key
        self._rules = [(kind, re.compile(rule)) for kind, rule in SECRETS]
        self._rules.append(("ENTROPY", re.compile(ENTROPY_CONTEXT)))
        self._rules.extend(("SECRET", re.compile(rule)) for rule in extra_patterns)

    def _token(self, kind: str, value: str) -> str:
        digest = hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()[:4]
        return f"<{kind}_{digest}>"

    def redact_text(self, text: str) -> str:
        protected = [m.span() for rule in PRESERVE for m in re.finditer(rule, text)]
        protected.extend(m.span() for m in _TOKEN.finditer(text))
        replacements: list[tuple[int, int, str]] = []
        # Header boundaries are resolved before generic credential patterns.
        for header in re.finditer(
            r"(?i)\b(?:proxy-authorization|authorization|set-cookie|cookie)"
            r"(?P<keyquote>[\"']?)\s*[:=]\s*",
            text,
        ):
            start = header.end()
            quote = text[start : start + 1]
            if "authorization" not in header.group().lower() and quote in ('"', "'"):
                start += 1
                # Skip escaped characters while locating the closing delimiter.
                end = start
                while end < len(text) and text[end] not in (quote, "\r", "\n"):
                    end += 2 if text[end] == "\\" else 1
            else:
                end = start
                while end < len(text) and text[end] not in "\r\n":
                    end += 1
            value = text[start:end]
            if "authorization" in header.group().lower():
                scheme = re.match(r"[A-Za-z][A-Za-z0-9_-]*[ \t]+", value)
                if scheme:
                    start += scheme.end()
                if start < end and not _TOKEN.fullmatch(text[start:end]):
                    replacements.append(
                        (start, end, self._token("SECRET", text[start:end]))
                    )
            else:
                for cookie in re.finditer(r"(?:^|;)[ \t]*[\w-]+=([^;\r\n]+)", value):
                    a, b = cookie.span(1)
                    raw = value[a:b]
                    if not _TOKEN.fullmatch(raw):
                        replacements.append(
                            (start + a, start + b, self._token("SECRET", raw))
                        )
        for kind, rule in self._rules:
            for match in rule.finditer(text):
                start, end = (
                    match.span("value") if "value" in rule.groupindex else match.span()
                )
                value = text[start:end]
                if kind == "ENTROPY":
                    if len(value) < 24:
                        continue
                    entropy = -sum(
                        (count / len(value)) * math.log2(count / len(value))
                        for count in Counter(value).values()
                    )
                    if entropy < 4.0:
                        continue
                if kind == "CARD":
                    digits = [int(c) for c in value if c.isdigit()]
                    checksum = sum(
                        (d * 2 - 9 if d >= 5 else d * 2) if i % 2 else d
                        for i, d in enumerate(reversed(digits))
                    )
                    if checksum % 10:
                        continue
                # Explicit credential contexts override evidence-shaped values.
                contextual = "value" in rule.groupindex and match.start() < start
                if any(start < b and end > a for a, b in protected) and not contextual:
                    continue
                if _TOKEN.fullmatch(value):
                    continue
                if any(start < b and end > a for a, b, _ in replacements):
                    continue
                replacements.append((start, end, self._token(kind, value)))
        for start, end, token in sorted(replacements, reverse=True):
            text = text[:start] + token + text[end:]
        return text

    def redact_json(self, value: JsonValue) -> JsonValue:
        if isinstance(value, dict):
            result: dict[str, JsonValue] = {}
            # Name/Value pairs (e.g. WAF sampled-request headers) carry the
            # sensitive name in a sibling field instead of the key.
            label = next(
                (
                    v
                    for k, v in value.items()
                    if k.lower() in _PAIR_NAME_KEYS and isinstance(v, str)
                ),
                None,
            )
            paired = label is not None and bool(_SENSITIVE.search(label))
            for key, item in value.items():
                if paired and key.lower() == "value":
                    raw = (
                        item
                        if isinstance(item, str)
                        else json.dumps(item, sort_keys=True)
                    )
                    result[key] = (
                        raw if _TOKEN.fullmatch(raw) else self._token("SECRET", raw)
                    )
                elif _SENSITIVE.search(key):
                    raw = (
                        item
                        if isinstance(item, str)
                        else json.dumps(item, sort_keys=True)
                    )
                    kind = (
                        "APIKEY" if re.search(r"api[-_]?key", key, re.I) else "SECRET"
                    )
                    if re.search("password", key, re.I):
                        kind = "PASSWORD"
                    result[key] = (
                        raw if _TOKEN.fullmatch(raw) else self._token(kind, raw)
                    )
                else:
                    result[key] = self.redact_json(item)
            return result
        if isinstance(value, list):
            return [self.redact_json(item) for item in value]
        return self.redact_text(value) if isinstance(value, str) else value
