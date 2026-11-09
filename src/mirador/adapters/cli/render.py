"""Stable human and machine report presentation."""

from zoneinfo import ZoneInfo

from mirador.domain.budget import Spend
from mirador.domain.report import Report


def render_json(report: Report) -> str:
    return report.model_dump_json(indent=2)


def render_markdown(report: Report, user_tz: ZoneInfo, spend: Spend | None) -> str:
    lines: list[str] = []
    if report.partial:
        reasons = [
            x
            for x in report.not_checked
            if x.startswith("budget exhausted")
            or any(
                s in x.lower() for s in ("cancel", "interrupt", "reporter unavailable")
            )
        ]
        lines.append(
            "⚠ Reporte parcial: " + ("; ".join(reasons) or "investigación incompleta")
        )
    confidence = {"high": "alta", "medium": "media", "low": "baja"}
    lines += [
        (
            "**Causa raíz** (confianza: "
            + confidence.get(report.confidence or "", "no determinada")
            + f")\n{report.root_cause}"
        )
        if report.root_cause
        else "**Causa raíz:** no determinada con evidencia",
        "",
        "**Evidencia**",
    ]
    lines += [
        f"{i}. {e.claim} — {e.source} · {e.query} · {e.result_summary}"
        + (f" · {e.link}" if e.link else "")
        for i, e in enumerate(report.evidence, 1)
    ]
    lines += [
        "",
        "**Logs relacionados**",
        *report.related_logs,
        "",
        "**Línea de tiempo**",
    ]
    lines += [
        f"{e.at:%H:%M} UTC ({e.at.astimezone(user_tz):%H:%M} {user_tz.key}) {e.event}"
        for e in report.timeline
    ]
    lines += ["", "**Impacto**", report.impact, "", "**Qué hacer**"]
    lines += [
        "- Mitigación inmediata (la ejecuta el humano): " + x
        for x in report.actions_immediate
    ]
    lines += ["- Fix de fondo: " + x for x in report.actions_fix]
    lines += [
        "",
        "**Descartado**",
        *[f"- {x.hypothesis}: {x.reason}" for x in report.ruled_out],
        "",
        "**Sin revisar**",
        *["- " + x for x in report.not_checked],
        "",
        "**Contexto usado**",
        *["- " + x for x in report.context_used],
        "",
    ]
    footer = f"Costo: USD {report.cost_usd:.2f}"
    if spend is not None:
        seconds = int(spend.elapsed.total_seconds())
        footer += (
            f" · tool calls: {spend.tool_calls}"
            f" · duración: {seconds // 60}:{seconds % 60:02}"
        )
    lines.append(footer + f" · investigación: {report.investigation_id}")
    return "\n".join(lines)
