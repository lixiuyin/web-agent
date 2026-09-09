"""Rebuild offline run readers/catalogs without changing historical evidence."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from webagent.agent.catalog import discover_traces
from webagent.agent.report import report_verification, write_report


def _canonical_copy(path: Path) -> Path | None:
    for parent in path.parents:
        if parent.parent.name == "shards" and re.fullmatch(r"shard-\d+", parent.name):
            return parent.parent.parent / path.relative_to(parent)
    return None


def _same_run(left: Path, right: Path) -> bool:
    try:
        first, second = (json.loads(p.read_text()) for p in (left, right))
        if not first.get("run_id") or first["run_id"] != second.get("run_id"):
            return False
        # Promotion rewrites absolute paths, but must not hide any other change.
        return json.dumps(first, sort_keys=True).replace(
            json.dumps(str(left.parent.parent))[1:-1], "<RUN_ROOT>"
        ) == json.dumps(second, sort_keys=True).replace(
            json.dumps(str(right.parent.parent))[1:-1], "<RUN_ROOT>"
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def catalog_traces(root: Path) -> tuple[list[Path], int]:
    """Collapse only verified shard/promoted copies within this catalog's scope."""
    root = root.resolve()
    traces = discover_traces(root)
    selected = []
    for path in traces:
        canonical = _canonical_copy(path)
        if (
            canonical is not None
            and canonical.resolve().is_relative_to(root)
            and _same_run(path, canonical)
        ):
            continue
        selected.append(path)
    return selected, len(traces) - len(selected)


def _duplicate_pairs(root: Path) -> list[tuple[Path, Path]]:
    root = root.resolve()
    pairs = []
    for path in discover_traces(root):
        canonical = _canonical_copy(path)
        if (
            canonical is not None
            and canonical.resolve().is_relative_to(root)
            and _same_run(path, canonical)
        ):
            pairs.append((path.parent.parent, canonical.parent.parent))
    return pairs


def _share_report(source_run: Path, target_run: Path) -> None:
    source = source_run / "report" / "index.html"
    target = target_run / "report" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".index-{uuid4().hex}.tmp")
    try:
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _judgment_status(run: Path) -> str:
    path = run / "evaluation/task.json"
    if not path.exists():
        return "not evaluated"
    try:
        value = json.loads(path.read_text()).get("passed")
        if not isinstance(value, bool):
            return "invalid judgment"
        return "passed" if value else "failed"
    except (OSError, ValueError, AttributeError):
        return "unreadable judgment"


def _model_label(root: Path, run: Path) -> str:
    for parent in run.parents:
        if parent.parent.name == "executions" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parent.name):
            return run.relative_to(parent).parts[0]
    relative = run.parent.relative_to(root).as_posix()
    return root.name if relative == "." else relative


def _catalog_row(root: Path, path: Path) -> str:
    run = path.parent.parent
    label = html.escape(run.name)
    location = html.escape(run.relative_to(root).as_posix())
    link = html.escape((run / "report/index.html").relative_to(root).as_posix(), quote=True)
    try:
        trace = json.loads(path.read_text())
        checked = report_verification(run, trace)
        compliance = (
            str(checked["valid"])
            if checked["applicability"] == "applicable"
            else checked["applicability"]
        )
        status = html.escape(str(trace.get("status", "unknown")))
        model = html.escape(_model_label(root, run))
        return (
            f'<tr><td><a href="{link}">{label}</a><small>{model}</small>'
            f"<details><summary>Saved path</summary>{location}</details></td>"
            f'<td data-label="Execution">{status}</td>'
            f'<td data-label="Task assertions">{_judgment_status(run)}</td>'
            f'<td data-label="Current compliance">{html.escape(compliance)}</td>'
            f'<td data-label="Actions">{len(trace.get("steps", []))}</td></tr>'
        )
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return (
            f'<tr><td>{label}</td><td class="unreadable" colspan="4">'
            f"Unreadable trace: {html.escape(str(exc))}</td></tr>"
        )


def _write_catalog(root: Path) -> None:
    traces, copies = catalog_traces(root)
    rows = "".join(_catalog_row(root, path) for path in traces)
    content = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>Run catalog</title><style>body{font:15px system-ui;margin:24px}"
        "table{border-collapse:collapse;width:100%;table-layout:fixed}"
        "td,th{text-align:left;padding:10px;border-bottom:1px solid #ddd;overflow-wrap:anywhere}"
        "th:first-child{width:40%}small{display:block;color:#555;margin-top:6px}"
        "summary{cursor:pointer;color:#666;margin-top:6px}"
        "@media(max-width:700px){body{margin:16px;font-size:14px}h1{font-size:1.5rem}"
        "table,tbody,tr,td{display:block;width:auto}table{border:0}"
        "thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}"
        "tr{border:1px solid #ddd;border-radius:8px;margin:0 0 12px;padding:10px}"
        "td{border:0;padding:5px 0;overflow-wrap:break-word}"
        "td:first-child{padding-bottom:8px}td:not(:first-child){display:grid;"
        "grid-template-columns:9rem minmax(0,1fr);gap:8px}"
        "td:not(:first-child)::before{content:attr(data-label);font-weight:600;color:#555}"
        "td.unreadable{display:block;color:#8a1c1c}}"
        "</style><h1>Run evidence catalog</h1>"
        f"<p>{len(traces)} run entries; {copies} identical shard copies collapsed. "
        "Copies remain on disk and in shard-level catalogs. Conflicting copies are not hidden.</p>"
        "<p>Newest trace first. Execution and compliance are not independent answer correctness. "
        "Historical evidence is unchanged.</p><table><thead><tr><th>Model / run</th>"
        "<th>Execution</th><th>Task assertions</th><th>Current compliance</th>"
        "<th>Actions</th></tr></thead><tbody>" + rows + "</tbody></table>"
    )
    temporary = root / f".index-{uuid4().hex}.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(root / "index.html")


def render_reports(directory: Path) -> Path:
    root = directory.resolve()
    if (root / "trajectory" / "trace.json").is_file():
        trace = json.loads((root / "trajectory" / "trace.json").read_text())
        return write_report(root, trace)
    traces = discover_traces(root)
    if not traces:
        raise ValueError("No run trace found below the directory")
    selected, _ = catalog_traces(root)
    parents = {root}
    for path in selected:
        run = path.parent.parent
        try:
            write_report(run, json.loads(path.read_text()))
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            print(f"Reader unavailable for {run.name}: {exc}")
    for shard_run, canonical_run in _duplicate_pairs(root):
        _share_report(canonical_run, shard_run)
    for path in traces:
        run = path.parent.parent
        parents.update(p for p in run.parents if p.is_relative_to(root))
    for parent in sorted(parents, key=lambda p: len(p.parts), reverse=True):
        _write_catalog(parent)
    return root / "index.html"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="A run, model directory, or date directory")
    print(render_reports(parser.parse_args().directory))


if __name__ == "__main__":
    main()
