"""Derived date/model catalogs; raw run evidence is never rewritten."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from uuid import uuid4

from webagent.agent.report import report_verification


def discover_traces(root: Path) -> list[Path]:
    root = root.resolve()
    return sorted(
        (p for p in root.rglob("trajectory/trace.json") if p.resolve().is_relative_to(root)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _row(root: Path, path: Path) -> str:
    run = path.parent.parent
    label = html.escape(run.relative_to(root).as_posix())
    link = html.escape((run / "report/index.html").relative_to(root).as_posix(), quote=True)
    try:
        trace = json.loads(path.read_text())
        checked = report_verification(run, trace)
        compliance = (
            checked["valid"]
            if checked["applicability"] == "applicable"
            else checked["applicability"]
        )
        status = html.escape(str(trace.get("status", "unknown")))
        return f'<tr><td><a href="{link}">{label}</a></td><td>{status}</td><td>{compliance}</td><td>{len(trace.get("steps", []))}</td></tr>'
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return f'<tr><td>{label}</td><td colspan="3">Unreadable trace: {html.escape(str(exc))}</td></tr>'


def write_catalog(root: Path) -> Path:
    root = root.resolve()
    rows = "".join(_row(root, path) for path in discover_traces(root))
    content = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>Run catalog</title><style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse;width:100%;table-layout:fixed}td,th{text-align:left;padding:10px;border-bottom:1px solid #ddd;overflow-wrap:anywhere}th:first-child{width:55%}</style><h1>Run evidence catalog</h1><p>Newest trace first. Execution status and compliance are not independent answer correctness. Historical evidence is unchanged.</p><table><tr><th>Model / run</th><th>Execution</th><th>Current compliance</th><th>Actions</th></tr>'
        + rows
        + "</table>"
    )
    path = root / "index.html"
    temporary = root / f".index-{uuid4().hex}.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return path


def refresh_parent_catalogs(run: Path) -> None:
    """Only auto-publish under the recognized date/model/run layout."""
    date_root = run.parent.parent
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_root.name):
        write_catalog(run.parent)
        write_catalog(date_root)
