"""Synthetic noise, memory and lifecycle fixtures, using shared incident helpers."""

import asyncio
import random
from datetime import UTC, datetime, timedelta

from _incidents import CASES, evidence, script, spec, stamp, step, write

MEMORY_ROOT = (
    "502 de checkout-api por agotamiento del pool de conexiones a payments-db "
    "tras subir réplicas"
)
NOISE_ROOT = (
    "payments-api latency increased after the GitHub deploy at 15:00; "
    "the new release doubled database queries per request."
)


def nightly() -> None:
    rng = random.Random(1803)  # noqa: S311 -- reproducible invented traffic
    root = CASES / "nightly-noise"
    rows = []
    for day in range(1, 8):
        for i in range(12):
            rows.append(
                dict(
                    ts=(
                        datetime(2026, 11, 8, 3, tzinfo=UTC)
                        - timedelta(days=day)
                        + timedelta(seconds=i * 60)
                    ).isoformat(),
                    service="cron-cleanup",
                    status=500,
                    message="payments-db payment cleanup query timeout",
                    database="payments-db",
                    latency_ms=1400,
                )
            )
    for day in range(8):
        for hour in range(24 if day else 16):
            rows.append(
                dict(
                    ts=(
                        datetime(2026, 11, 8, hour, tzinfo=UTC) - timedelta(days=day)
                    ).isoformat(),
                    service="payments-api",
                    level="warning",
                    status=200,
                    message="payments-db payment query latency warning",
                    database="payments-db",
                    latency_ms=100,
                )
            )
    for i in range(120):
        deployed = i >= 60
        rows.append(
            dict(
                ts=stamp(14, i * 60),
                service="payments-api",
                status=200,
                release="pay-v42" if deployed else "pay-v41",
                latency_ms=rng.randrange(1200, 1500)
                if deployed
                else rng.randrange(80, 120),
                db_queries=20 if deployed else 10,
                message="Payment request completed",
                database="payments-db",
            )
        )
    rows.sort(key=lambda row: str(row["ts"]))
    write(root, "data/logs.datadog.jsonl", rows)
    write(
        root,
        "data/changes.github.jsonl",
        [
            dict(
                at=stamp(15),
                source="github",
                actor="synthetic-release-bot",
                kind="deployment",
                resource="payments-api",
                summary="deploy pay-v42 at 15:00",
                details=dict(service="payments-api", release="pay-v42", db_queries=20),
                link=None,
            )
        ],
    )
    write(
        root,
        "case.yaml",
        spec(
            "nightly-noise",
            NOISE_ROOT,
            "payments-api latency increased at 15:00 UTC",
            16,
            dict(logs=["datadog"], changes=["github"]),
            [],
            ["deploy"],
            ["cron-cleanup"],
        ),
    )
    window = dict(start=stamp(14), end=stamp(16))
    write(
        root,
        "dry_run.yaml",
        script(
            NOISE_ROOT,
            ["logs"],
            [
                step(
                    "logs.datadog.search",
                    **window,
                    filter="service:payments-api",
                    limit=200,
                ),
                step(
                    "logs.datadog.histogram",
                    start="2026-11-01T00:00:00+00:00",
                    end=stamp(16),
                    filter="service:cron-cleanup",
                    interval="PT1H",
                ),
                step(
                    "logs.datadog.count",
                    start="2026-11-01T00:00:00+00:00",
                    end=stamp(16),
                    filter="level:warning",
                    group_by=["service"],
                ),
            ],
            [
                evidence(
                    "logs.datadog.search",
                    "payments-api latency and database queries rise with pay-v42",
                    "deployment payments-api release pay-v42",
                ),
                evidence(
                    "logs.datadog.histogram",
                    "Cleanup errors recur at 03:00 on all seven previous nights",
                    "2026-11-01",
                ),
                evidence(
                    "logs.datadog.count",
                    "Warnings are a constant background signal",
                    "payments-api",
                ),
            ],
            [step("changes.github.recent", **window, service="payments-api", limit=20)],
            [
                evidence(
                    "changes.github.recent",
                    "GitHub deploy at 15:00 precedes latency",
                    "deploy pay-v42",
                )
            ],
        ),
    )


def memory(name: str) -> None:
    rng = random.Random(1806)  # noqa: S311 -- identical current data in both cases
    root = CASES / name
    rows = [
        dict(
            ts=stamp(14, i * 60),
            service="checkout-api",
            status=502 if i % 3 == 0 else 200,
            message="payments-db connection pool exhausted"
            if i % 3 == 0
            else "Checkout OK",
            replicas=12,
            pool_active=100 if i % 3 == 0 else rng.randrange(80, 99),
            pool_limit=100,
        )
        for i in range(60)
    ]
    write(root, "data/logs.datadog.jsonl", rows)
    write(
        root,
        "data/changes.github.jsonl",
        [
            dict(
                at=stamp(13, 55 * 60),
                source="github",
                actor="synthetic-release-bot",
                kind="deployment",
                resource="checkout-api",
                summary="checkout-api replicas increased from 3 to 12",
                details=dict(
                    service="checkout-api", replicas_before=3, replicas_after=12
                ),
                link=None,
            )
        ],
    )
    write(
        root,
        "memory.yaml",
        []
        if name.endswith("-empty")
        else [
            dict(
                id="checkout-pool-12-days-ago",
                investigation_id="synthetic-prior-checkout",
                recorded_at="2026-10-27T15:00:00+00:00",
                services=["checkout-api", "payments-db"],
                symptom="checkout-api 502 intermitente",
                root_cause=MEMORY_ROOT,
                confidence="high",
                evidence_summary=["payments-db pool active=100 limit=100"],
                fix=["Reduce replicas and cap aggregate database connections"],
                state="confirmed",
                facts=[MEMORY_ROOT],
            )
        ],
    )
    write(
        root,
        "case.yaml",
        spec(
            name,
            MEMORY_ROOT,
            "checkout-api 502 intermitente",
            15,
            dict(logs=["datadog"], changes=["github"]),
            [],
            ["payments-db", "pool"],
        ),
    )
    window = dict(start=stamp(13), end=stamp(15))
    steps = [step("logs.datadog.search", **window, filter="status:502", limit=50)]
    entries = script(
        MEMORY_ROOT,
        ["logs"],
        steps,
        [
            evidence(
                "logs.datadog.search",
                "Current payments-db connection pool exhausted causes checkout-api 502",
                "payments-db connection pool exhausted",
            )
        ],
        [step("changes.github.recent", **window, service="checkout-api", limit=20)],
        [
            evidence(
                "changes.github.recent",
                "checkout-api replicas increased from 3 to 12",
                "replicas increased",
            )
        ],
    )
    # Early explicit recall widens the automatic seven-day window to include day -12.
    entries[0]["steps"] = [
        step(
            "memory.recall",
            query="checkout-api 502 pool",
            services=["checkout-api"],
            since="2026-10-20T00:00:00+00:00",
            limit=5,
        )
    ]
    write(root, "dry_run.yaml", entries)


def lifecycle(name: str) -> None:
    rng = random.Random(1807)  # noqa: S311 -- deterministic synthetic request IDs
    root = CASES / name
    cause = "Synthetic lifecycle probe; diagnostic coverage is incomplete"
    config = spec(
        name,
        cause,
        "Investigate synthetic lifecycle-api failures",
        16,
        dict(logs=["datadog"]),
        [],
        [],
    )
    if name == "budget-exhausted":
        config["budget"]["tool_calls"] = 5
    if name == "provider-auth-error":
        config["auth_error_providers"] = ["datadog"]
    write(root, "case.yaml", config)
    write(
        root,
        "data/logs.datadog.jsonl",
        [
            dict(
                ts=stamp(15),
                service="lifecycle-api",
                status=500,
                request_id=f"synthetic-{rng.randrange(100000)}",
                message="Synthetic lifecycle failure",
            )
        ],
    )
    calls = [
        step(
            "logs.datadog.search",
            start=stamp(14, i),
            end=stamp(16),
            filter="status:500",
            limit=10,
        )
        for i in range(10 if name == "budget-exhausted" else 1)
    ]
    entries = script(cause, ["logs"], [{"sleep": 30}] if name == "slow" else calls, [])
    if name == "provider-auth-error":
        entries[2]["output"]["not_checked"] = [
            "datadog logs unavailable: provider authentication ToolError"
        ]
        entries[4]["output"]["not_checked"] = [
            "datadog logs unavailable: provider authentication ToolError"
        ]
    write(root, "dry_run.yaml", entries)


async def generate(name: str) -> None:
    if name == "nightly-noise":
        await asyncio.to_thread(nightly)
    else:
        await asyncio.to_thread(
            memory if name.startswith("memory-helps") else lifecycle, name
        )
