"""Read-only Markdown documentation backed by a persistent FTS5 index."""

import asyncio
import os
import re
import sqlite3
from pathlib import Path

from mirador.application.ports.capabilities.docs import DocHit, DocPage, DocsSource
from mirador.application.ports.health import HealthCheckable, HealthResult
from mirador.application.tooling.spec import ToolError


def _sections(text: str) -> list[tuple[str | None, str]]:
    sections: list[tuple[str | None, str]] = []
    heading: str | None = None
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append(line)
            continue
        match = (
            re.match(r"^ {0,3}#{1,3}\s+(.+?)\s*#*\s*$", line) if fence is None else None
        )
        if match:
            if lines or heading is not None:
                sections.append((heading, "".join(lines)))
            heading, lines = match.group(1), []
        lines.append(line)
    if lines or heading is not None:
        sections.append((heading, "".join(lines)))
    return sections


class LocalDocsSource(DocsSource, HealthCheckable):
    def __init__(self, root: Path, index_path: Path) -> None:
        self.root = root
        self.index_path = index_path

    @classmethod
    async def open(cls, root: Path, index_path: Path) -> "LocalDocsSource":
        source = cls(root, index_path)
        await asyncio.to_thread(source._initialize)
        return source

    def _initialize(self) -> None:
        self.root = self.root.resolve()
        if not self.root.is_dir() or not os.access(self.root, os.R_OK | os.X_OK):
            raise OSError(f"Documentation root is not readable: {self.root}")
        manifest: list[tuple[str, int, int]] = []
        for path in sorted(self.root.rglob("*.md")):
            if path.resolve().is_relative_to(self.root) and path.is_file():
                stat = path.stat()
                manifest.append(
                    (
                        path.relative_to(self.root).as_posix(),
                        stat.st_mtime_ns,
                        stat.st_size,
                    )
                )
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.index_path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS manifest(path TEXT, mtime INTEGER, "
                "size INTEGER)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS metadata(root TEXT)")
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS sections USING fts5(path, "
                "heading, content, tokenize='unicode61 remove_diacritics 2')"
            )
            if (
                db.execute("SELECT root FROM metadata").fetchall()
                == [(str(self.root),)]
                and db.execute(
                    "SELECT path, mtime, size FROM manifest ORDER BY path"
                ).fetchall()
                == manifest
            ):
                return
            db.execute("DELETE FROM sections")
            for relative_path, _, _ in manifest:
                text = (self.root / relative_path).read_text(encoding="utf-8")
                db.executemany(
                    "INSERT INTO sections VALUES (?, ?, ?)",
                    [
                        (relative_path, heading, content)
                        for heading, content in _sections(text)
                    ],
                )
            db.execute("DELETE FROM manifest")
            db.executemany("INSERT INTO manifest VALUES (?, ?, ?)", manifest)
            db.execute("DELETE FROM metadata")
            db.execute("INSERT INTO metadata VALUES (?)", (str(self.root),))

    async def search(self, query: str, limit: int) -> list[DocHit]:
        terms = re.findall(r"[^\W_]+", query, re.UNICODE)
        if not terms or limit <= 0:
            return []
        safe_query = " OR ".join('"' + term + '"' for term in terms)
        return await asyncio.to_thread(self._search, safe_query, limit)

    def _search(self, query: str, limit: int) -> list[DocHit]:
        with sqlite3.connect(self.index_path) as db:
            rows = db.execute(
                "SELECT path, heading, snippet(sections, 2, '', '', ' … ', 32), "
                "bm25(sections) FROM sections WHERE sections MATCH ? "
                "ORDER BY bm25(sections) LIMIT ?",
                (query, limit),
            ).fetchall()
        return [
            DocHit(path=p, heading=h, snippet=s, score=score) for p, h, s, score in rows
        ]

    async def read(self, path: str, heading: str | None) -> DocPage:
        return await asyncio.to_thread(self._read, path, heading)

    def _read(self, path: str, heading: str | None) -> DocPage:
        target = (self.root / path).resolve()
        if Path(path).is_absolute() or not target.is_relative_to(self.root):
            raise ToolError(
                "Documentation path must stay within the root",
                actionable=True,
                kind="invalid",
            )
        if target.suffix.lower() != ".md":
            raise ToolError(
                "Only Markdown documents can be read", actionable=True, kind="invalid"
            )
        try:
            content = target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ToolError(
                f"Document not found: {path}", actionable=True, kind="not_found"
            ) from exc
        if heading is not None:
            sections = _sections(content)
            matches = [text for title, text in sections if title == heading]
            if not matches:
                available = [title for title, _ in sections if title is not None][:20]
                raise ToolError(
                    f"Heading not found: {heading}. Available headings: "
                    f"{', '.join(available)}",
                    actionable=True,
                    kind="not_found",
                )
            content = "".join(matches)
        return DocPage(
            path=target.relative_to(self.root).as_posix(),
            heading=heading,
            content=content,
        )

    async def health_check(self) -> HealthResult:
        return await asyncio.to_thread(self._health_check)

    def _health_check(self) -> HealthResult:
        try:
            if not self.root.is_dir() or not os.access(self.root, os.R_OK | os.X_OK):
                raise OSError("Documentation root is missing or unreadable")
            with sqlite3.connect(
                f"{self.index_path.resolve().as_uri()}?mode=ro", uri=True
            ) as db:
                db.execute("SELECT count(*) FROM sections").fetchone()
            return HealthResult(
                component="localdocs", ok=True, detail="Root readable and index usable"
            )
        except (OSError, sqlite3.Error) as exc:
            return HealthResult(component="localdocs", ok=False, detail=str(exc))
