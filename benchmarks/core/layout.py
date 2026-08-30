"""Canonical filesystem layout for benchmark studies and their task runs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from webagent.evaluation.artifacts import StudyLayout, safe_component, safe_slug


def packaged_manifest_path(filename: str) -> Path:
    """Return a manifest shipped with the installed ``benchmarks`` package.

    Resolving from this module, rather than the caller's working directory,
    keeps CLI defaults valid both in a repository checkout and after wheel or
    source-distribution installation.
    """
    normalized = safe_component(filename, "manifest filename")
    if Path(normalized).suffix != ".json":
        raise ValueError("manifest filename must end with .json")
    path = Path(__file__).resolve().parents[1] / "manifests" / normalized
    if not path.is_file():
        raise FileNotFoundError(f"packaged benchmark manifest is missing: {path}")
    return path


def default_study_dir(study_id: str) -> Path:
    """Return the repository-relative default directory for one benchmark study."""
    normalized = safe_slug(study_id, fallback="study")
    return Path("outputs") / "studies" / normalized


def execution_model_label(*, mode: str, configured_model: str) -> str:
    """Return the real model label, or the explicit non-model baseline label."""
    if mode == "scripted-harness-baseline":
        return mode
    if mode != "agent":
        raise ValueError(f"unsupported benchmark mode: {mode}")
    if not configured_model.strip():
        raise ValueError("configured model name must not be empty")
    return configured_model


def task_run_dir(study_dir: Path, task_id: str) -> Path:
    """Return one task run below an exact benchmark execution directory."""
    normalized = safe_component(task_id, "task_id")
    return study_dir / "runs" / normalized


def allocate_execution_dir(
    study_dir: Path,
    *,
    model: str,
    condition: str,
    now: datetime | None = None,
    execution_id: str | None = None,
) -> Path:
    """Return a unique, date/model/condition-scoped execution directory."""
    return (
        StudyLayout.from_root(study_dir)
        .allocate_execution(
            model=model,
            condition=condition,
            now=now,
            execution_id=execution_id,
        )
        .root
    )


__all__ = [
    "allocate_execution_dir",
    "default_study_dir",
    "execution_model_label",
    "packaged_manifest_path",
    "task_run_dir",
]
