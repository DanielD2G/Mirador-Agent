"""Typer entry points; composition is injected by __main__."""

import asyncio
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

import typer
import uvloop
from rich.console import Console
from rich.table import Table

from mirador.adapters.cli.render import render_json, render_markdown
from mirador.application.config import load_config
from mirador.domain.budget import BudgetExceeded

app = typer.Typer()
memory = typer.Typer()
app.add_typer(memory, name="memory")
_factory: Callable[..., Any] | None = None


def configure(factory: Callable[..., Any]) -> None:
    global _factory
    _factory = factory


def _config(path: Path | None) -> Path:
    return path or (
        Path("mirador.yaml")
        if Path("mirador.yaml").exists()
        else Path.home() / ".mirador/mirador.yaml"
    )


def _run(command: str, config: Path | None, **kwargs: Any) -> None:
    async def execute() -> None:
        if _factory is None:
            raise RuntimeError("CLI composition not configured")
        path = _config(config)
        await asyncio.to_thread(load_config, path)
        if command in ("ask", "investigate") and not os.environ.get(
            "OPENROUTER_API_KEY"
        ):
            raise ValueError("OPENROUTER_API_KEY missing")
        options = (
            {"eval_mode": True, "cases_dir": kwargs["cases_dir"]}
            if command == "eval"
            else {"doctor_mode": command == "doctor"}
        )
        async with _factory(path, **options) as services:
            if command == "eval":
                summary = await services.run_eval.execute(
                    kwargs["suite"], kwargs["case"], kwargs["runs"], kwargs["dry_run"]
                )
                directory = Path("evals/results")
                await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
                result_path = directory / (
                    datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + ".json"
                )
                await asyncio.to_thread(
                    result_path.write_text, summary.model_dump_json(indent=2)
                )
                if kwargs["suite"] == "redaction":
                    typer.echo(
                        f"secrets surviving: {summary.secrets_surviving} · "
                        f"legit ids changed: {summary.legit_ids_changed}"
                    )
                    for failure in summary.failures:
                        typer.echo(failure)
                else:
                    table = Table(
                        "Case",
                        "Run",
                        "Judge",
                        "Mention",
                        "Injection",
                        "Leaks",
                        "Evidence",
                        "Tools",
                        "Dead ends",
                        "Seconds",
                        "USD",
                        "Error",
                    )
                    for result in summary.results:
                        table.add_row(
                            result.case,
                            str(result.run),
                            str(result.root_cause_correct),
                            str(result.must_mention_ok),
                            str(result.injection_ok),
                            str(len(result.secrets_leaked)),
                            f"{result.evidence_precision:.2f}",
                            str(result.tool_calls),
                            str(result.dead_ends),
                            f"{result.duration_s:.2f}",
                            str(result.usd),
                            result.error or "",
                        )
                    Console().print(table)
                typer.echo(f"Results: {result_path}")
                raise typer.Exit(
                    1
                    if summary.secrets_surviving
                    or summary.legit_ids_changed
                    or any(
                        r.error or r.secrets_leaked or not r.injection_ok
                        for r in summary.results
                    )
                    else 0
                )
            if command == "doctor":
                results = await services.doctor.execute()
                for result in results:
                    typer.echo(
                        f"{'✓' if result.ok else '✗'} {result.component}: "
                        f"{result.detail}"
                    )
                raise typer.Exit(0 if all(r.ok for r in results) else 1)
            if command == "list":
                since = kwargs["since"]
                if not since.endswith("d") or not since[:-1].isdigit():
                    raise ValueError("--since must use a duration such as 7d")
                episodes = await services.memory_admin.list(
                    datetime.now(UTC) - timedelta(days=int(since[:-1]))
                )
                for episode in episodes:
                    typer.echo(episode.model_dump_json())
                return
            if command in ("confirm", "reject"):
                await getattr(services.memory_admin, command)(kwargs["id"])
                typer.echo(f"{kwargs['id']}: {command}")
                return
            if not kwargs["json"]:
                services.events.start()
            investigation_id = str(uuid4())
            try:
                if command == "ask":
                    report = await services.ask.execute(
                        kwargs["question"],
                        kwargs["thread"],
                        investigation_id=investigation_id,
                    )
                else:
                    alert = kwargs["alert"]
                    payload = await asyncio.to_thread(
                        sys.stdin.read if str(alert) == "-" else Path(alert).read_text
                    )
                    report = await services.investigate.execute(
                        payload, investigation_id=investigation_id
                    )
            except asyncio.CancelledError as exc:
                services.events.stop()
                typer.echo(
                    f"Investigación {investigation_id} interrumpida; "
                    f'continuar con: mirador ask --thread {investigation_id} "..."',
                    err=True,
                )
                raise typer.Exit(130) from exc
            finally:
                services.events.stop()
            investigation = await services.store.get_investigation(
                report.investigation_id
            )
            typer.echo(
                render_json(report)
                if kwargs["json"]
                else render_markdown(
                    report,
                    ZoneInfo(os.environ.get("TZ", "UTC")),
                    investigation.spent if investigation else None,
                )
            )
            if any("cancelled" in x for x in report.not_checked):
                typer.echo(
                    f"Investigación {report.investigation_id} interrumpida; "
                    "continuar con: mirador ask --thread "
                    f'{report.investigation_id} "..."',
                    err=True,
                )
                raise typer.Exit(130)

    try:
        uvloop.run(execute())
    except (OSError, ValueError, BudgetExceeded) as exc:
        typer.echo(f"Error de configuración o presupuesto: {exc}", err=True)
        raise typer.Exit(2) from exc
    except KeyboardInterrupt as exc:
        raise typer.Exit(130) from exc


@app.command()
def ask(
    question: str,
    json: bool = False,
    thread: str | None = None,
    config: Path | None = None,
) -> None:
    _run("ask", config, question=question, json=json, thread=thread)


@app.command()
def investigate(
    alert: Annotated[Path, typer.Option("--alert")],
    json: bool = False,
    config: Path | None = None,
) -> None:
    _run("investigate", config, alert=alert, json=json)


@app.command()
def doctor(config: Path | None = None) -> None:
    _run("doctor", config)


@memory.command("list")
def memory_list(since: str = "7d", config: Path | None = None) -> None:
    _run("list", config, since=since)


@memory.command()
def confirm(id: str, config: Path | None = None) -> None:
    _run("confirm", config, id=id)


@memory.command()
def reject(id: str, config: Path | None = None) -> None:
    _run("reject", config, id=id)


@app.command("eval")
def evaluate(
    suite: Literal["core", "redaction"] = "core",
    case: str | None = None,
    runs: int = 1,
    dry_run: bool = False,
    cases_dir: Path = Path("evals/cases"),
    config: Path | None = None,
) -> None:
    if runs < 1:
        raise typer.BadParameter("runs must be positive")
    _run(
        "eval",
        config,
        suite=suite,
        case=case,
        runs=runs,
        dry_run=dry_run,
        cases_dir=cases_dir,
    )


@app.command()
def version() -> None:
    typer.echo("mirador 0.1.0")
