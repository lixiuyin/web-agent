"""Derived readers/catalogs must not rewrite historical evidence."""

import importlib.util
import json
from pathlib import Path

import pytest


def _renderer():
    path = Path(__file__).resolve().parents[2] / "scripts" / "render_run_report.py"
    spec = importlib.util.spec_from_file_location("render_run_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render_reports


def test_catalog_keeps_traces_and_stored_certificates_immutable(tmp_path):
    trace = {"task": "Audit <script>", "status": "failed", "steps": []}
    before = {}
    for name in ("first-run", "second-run"):
        directory = tmp_path / name / "trajectory"
        directory.mkdir(parents=True)
        for filename, value in (("trace.json", trace), ("verification.json", {"valid": True})):
            path = directory / filename
            path.write_text(json.dumps(value))
            before[path] = path.read_bytes()
    renderer = _renderer()
    catalog = renderer(tmp_path)
    assert catalog == tmp_path / "index.html"
    assert "first-run/report/index.html" in catalog.read_text()
    assert "second-run/report/index.html" in catalog.read_text()
    assert "&lt;script&gt;" in renderer(tmp_path / "first-run").read_text()
    assert all(path.read_bytes() == value for path, value in before.items())


def test_catalog_without_traces_reports_actionable_error(tmp_path):
    with pytest.raises(ValueError, match="No run trace"):
        _renderer()(tmp_path)


def test_date_catalog_handles_models_corrupt_traces_and_external_symlinks(tmp_path):
    from webagent.agent.catalog import refresh_parent_catalogs

    root = tmp_path / "2026-09-08"
    run = root / "model" / "run"
    (run / "trajectory").mkdir(parents=True)
    (run / "trajectory/trace.json").write_text(
        json.dumps(
            {
                "task": "Audit",
                "status": "blocked",
                "steps": [],
                "events": [{"type": "captcha_detected", "reason": "challenge"}],
            }
        )
    )
    bad = root / "another-model/bad/trajectory"
    bad.mkdir(parents=True)
    (bad / "trace.json").write_text("invalid JSON")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "external").symlink_to(outside, target_is_directory=True)
    _renderer()(root)
    assert "model/run/report/index.html" in (root / "index.html").read_text()
    assert "Unreadable trace" in (root / "index.html").read_text()
    assert (root / "model/index.html").exists()
    assert "captcha_detected" in (run / "report/index.html").read_text()
    assert "reconstructed_from_trace" in (run / "report/index.html").read_text()
    assert not (outside / "index.html").exists()
    refresh_parent_catalogs(run)
    refresh_parent_catalogs(outside)


def _copy_trace(run, **changes):
    directory = run / "trajectory"
    directory.mkdir(parents=True)
    trace = {
        "run_id": "same-run",
        "task": "Audit",
        "status": "completed",
        "steps": [],
        "local_path": str(run / "artifacts/file.pdf"),
        **changes,
    }
    path = directory / "trace.json"
    path.write_text(json.dumps(trace))
    return path


@pytest.mark.parametrize("root_name", ["", "中文 workspace"])
def test_catalog_collapses_only_identical_promoted_copies(tmp_path, root_name):
    tmp_path = tmp_path / root_name
    canonical = _copy_trace(tmp_path / "runs/task")
    shard = _copy_trace(tmp_path / "shards/shard-00/runs/task")
    before = {p: p.read_bytes() for p in (canonical, shard)}
    catalog = _renderer()(tmp_path).read_text()
    assert "1 run entries; 1 identical shard copies collapsed" in catalog
    assert 'href="runs/task/report/index.html"' in catalog
    assert 'href="shards/shard-00/runs/task/report/index.html"' not in catalog
    assert (
        "1 run entries; 0 identical shard copies collapsed"
        in (tmp_path / "shards/shard-00/index.html").read_text()
    )
    canonical_report = tmp_path / "runs/task/report/index.html"
    shard_report = tmp_path / "shards/shard-00/runs/task/report/index.html"
    assert canonical_report.read_bytes() == shard_report.read_bytes()
    assert canonical_report.stat().st_ino == shard_report.stat().st_ino
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize("changes", [{"status": "failed"}, {"run_id": "different"}])
def test_catalog_retains_conflicting_copies(tmp_path, changes):
    _copy_trace(tmp_path / "runs/task")
    _copy_trace(tmp_path / "shards/shard-00/runs/task", **changes)
    catalog = _renderer()(tmp_path).read_text()
    assert "2 run entries; 0 identical shard copies collapsed" in catalog


def test_catalog_does_not_hide_unpromoted_shards_or_same_ids_in_other_runs(tmp_path):
    _copy_trace(tmp_path / "shards/shard-00/runs/task")
    _copy_trace(tmp_path / "another-execution/runs/task")
    assert "2 run entries; 0 identical shard copies collapsed" in _renderer()(tmp_path).read_text()


@pytest.mark.parametrize(
    ("judgment", "expected"),
    [
        (None, "not evaluated"),
        ('{"passed": true}', "passed"),
        ('{"passed": false}', "failed"),
        ('{"passed": "yes"}', "invalid judgment"),
        ("broken", "unreadable judgment"),
    ],
)
def test_catalog_separates_execution_from_independent_judgment(tmp_path, judgment, expected):
    run = tmp_path / "executions/2026-09-08/model/run"
    _copy_trace(run)
    if judgment is not None:
        (run / "evaluation").mkdir()
        (run / "evaluation/task.json").write_text(judgment)
    catalog = _renderer()(tmp_path).read_text()
    assert '<td data-label="Execution">completed</td>' in catalog
    assert f'<td data-label="Task assertions">{expected}</td>' in catalog
    assert "<small>model</small>" in catalog
    assert "Task assertions" in catalog


def test_catalog_uses_mobile_card_layout(tmp_path):
    _copy_trace(tmp_path / "runs/task")
    catalog = _renderer()(tmp_path).read_text()
    assert "@media(max-width:700px)" in catalog
    assert "grid-template-columns:9rem minmax(0,1fr)" in catalog
    assert '<td data-label="Current compliance">' in catalog
    assert "<thead><tr>" in catalog
    assert "<tbody><tr>" in catalog
