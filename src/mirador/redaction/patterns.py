"""Ordered recognition rules; secret rules capture the bare value as `value`."""

PRESERVE: list[str] = [
    r"\bRoot=1-[0-9a-f]{8}-[0-9a-f]{24}\b",
    r"\barn:aws[^\s\"]+",
    r"\b(?:i|sg|vpc|subnet|nat|eni)-[0-9a-f]+\b",
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    r"\b[0-9a-f]{32}\b|\b[0-9a-f]{16}\b",
    r"(?i)\bcommit[ :\t]+[0-9a-f]{7,40}\b",
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]*(?!\w)",
]

_VALUE = r"(?P<value>[^\s\"'&,;<>]+)"
SECRETS: list[tuple[str, str]] = [
    (
        "PRIVATE_KEY",
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----(?P<value>[\s\S]*?)"
        r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----",
    ),
    # Credentials embedded in URLs: scheme://user:password@host
    (
        "URL_CREDENTIAL",
        r"\b[a-z][a-z0-9+.-]*://[^\s/:@?#\"'<>,;\\]+:(?P<value>[^\s/@?#\"'<>,;\\]+)@",
    ),
    ("BEARER", r"(?i)\bBearer\s+" + _VALUE),
    ("JWT", r"\b(?P<value>eyJ[\w-]+\.[\w-]+\.[\w-]+)"),
    (
        "AWS_SECRET",
        r"(?i)\baws[_-]?secret(?:[_-]?access)?[_-]?key[\"']?\s*[:=]\s*[\"']?" + _VALUE,
    ),
    (
        "DATADOG_KEY",
        r"(?i)\b(?:datadog|dd)[_-]?(?:api|app)(?:lication)?[_-]?key[\"']?\s*[:=]\s*[\"']?"
        + _VALUE,
    ),
    (
        "APIKEY",
        r"(?i)(?:(?<![A-Za-z0-9])(?:x-api-key|api-key|api_key|apikey)|\bkey)[\"']?\s*[:=]\s*[\"']?"
        + _VALUE,
    ),
    (
        "PASSWORD",
        r"(?i)(?<![A-Za-z0-9])(?:password|passwd)[\"']?\s*[:=]\s*[\"']?" + _VALUE,
    ),
    (
        "SECRET",
        r"(?i)(?<![A-Za-z0-9])(?:secret|token)[\"']?\s*[:=]\s*[\"']?" + _VALUE,
    ),
    ("APIKEY", r"/client/(?P<value>[^/\s?<>]+)/"),
    ("AWS_ACCESS_KEY", r"\b(?P<value>AKIA[0-9A-Z]{16})\b"),
    ("GITHUB_TOKEN", r"\b(?P<value>(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+)\b"),
    ("APIKEY", r"\bsk-(?P<value>(?:or-v1-|ant-|proj-)?[A-Za-z0-9_-]{20,})"),
    ("SLACK_TOKEN", r"\bxox[abposr]-(?P<value>[A-Za-z0-9-]{10,})"),
    ("GITLAB_TOKEN", r"\bglpat-(?P<value>[A-Za-z0-9_-]{20,})"),
    ("EMAIL", r"(?<![\w.+-])(?P<value>[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})"),
    ("CARD", r"(?<!\d)(?P<value>\d(?:[ -]?\d){12,18})(?!\d)"),
]

# Quoted variants precede unquoted variants, so the entire value is reserved first.
_quoted_rules: list[tuple[str, str]] = []
for _kind, _rule in SECRETS:
    if _rule.endswith(_VALUE):
        _prefix = _rule[: -len(_VALUE)]
        # Existing optional opening quote must not consume the delimiter.
        if _prefix.endswith(r"[\"']?"):
            _prefix = _prefix[: -len(r"[\"']?")]
        for _quote in ('"', "'"):
            _body = rf"(?P<value>(?:\\[^\r\n]|[^\\{_quote}\r\n])*)"
            _quoted_rules.append((_kind, _prefix + _quote + _body + _quote))
SECRETS = _quoted_rules + SECRETS

ENTROPY_CONTEXT = (
    r"(?i)\b(?:credential|auth|session|sig|signature|private|access|client_secret)"
    r"(?:[_-](?:key|token|id))?[\"']?\s*[:=]\s*[\"']?" + _VALUE
)
