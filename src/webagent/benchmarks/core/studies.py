"""Explicit CLI plumbing for canonical study-run identity."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from webagent.evaluation import (
    StudyLayout,
    StudyManifest,
    StudyRunContext,
    read_study_manifest,
)
from webagent.evaluation.task_binding import task_set_sha256_from_manifest_bytes


def add_study_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Add an all-or-none set of study-ledger identity arguments."""
    parser.add_argument("--study-root", type=Path, default=None)
    parser.add_argument("--study-id", default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--condition-id", default=None)
    parser.add_argument("--repetition", type=int, default=None)


def study_context_from_args(
    args: argparse.Namespace,
    *,
    model: str,
) -> StudyRunContext | None:
    """Return typed study identity, rejecting incomplete provenance."""
    values = {
        "study_root": getattr(args, "study_root", None),
        "study_id": getattr(args, "study_id", None),
        "provider": getattr(args, "provider", None),
        "condition_id": getattr(args, "condition_id", None),
        "repetition": getattr(args, "repetition", None),
    }
    supplied = {key for key, value in values.items() if value is not None}
    if not supplied:
        return None
    if supplied != set(values):
        missing = sorted(set(values) - supplied)
        raise ValueError("incomplete canonical study context; missing: " + ", ".join(missing))
    study_root = values["study_root"]
    repetition = values["repetition"]
    assert study_root is not None
    assert repetition is not None
    root = Path(study_root).resolve()
    manifest = read_study_manifest(StudyLayout.from_root(root).manifest_path)
    if str(values["study_id"]) != manifest.study_id:
        raise ValueError("study context id does not match the retained study manifest")
    retained = (
        StudyLayout.from_root(root).task_manifests_dir / f"{manifest.task_manifest_sha256}.json"
    )
    try:
        retained_bytes = retained.read_bytes()
    except OSError as exc:
        raise ValueError(f"preregistered task manifest is missing: {retained}") from exc
    retained_sha256 = hashlib.sha256(retained_bytes).hexdigest()
    if retained_sha256 != manifest.task_manifest_sha256:
        raise ValueError("retained task manifest does not match the study manifest hash")
    return StudyRunContext(
        study_root=root,
        study_id=str(values["study_id"]),
        provider=str(values["provider"]),
        model=model,
        condition_id=str(values["condition_id"]),
        repetition=int(repetition),
        task_manifest_sha256=retained_sha256,
        task_set_sha256=task_set_sha256_from_manifest_bytes(retained_bytes),
    )


def initialize_matrix_study(
    root: Path,
    proposed: StudyManifest,
    *,
    task_manifest_bytes: bytes,
) -> StudyManifest:
    """Publish one immutable matrix contract or validate an equivalent retry."""
    layout = StudyLayout.from_root(root)
    digest = hashlib.sha256(task_manifest_bytes).hexdigest()
    if digest != proposed.task_manifest_sha256:
        raise ValueError("retained task manifest bytes do not match study manifest hash")
    task_set_sha256_from_manifest_bytes(task_manifest_bytes)
    layout.prepare()
    retained = layout.task_manifests_dir / f"{digest}.json"
    if retained.is_file():
        if retained.read_bytes() != task_manifest_bytes:
            raise ValueError("retained task manifest hash path contains different bytes")
    else:
        temporary = retained.with_suffix(".json.tmp")
        temporary.write_bytes(task_manifest_bytes)
        temporary.replace(retained)
    if layout.manifest_path.is_file():
        existing = read_study_manifest(layout.manifest_path)
        retry = proposed.model_copy(update={"created_at": existing.created_at})
        if retry != existing:
            raise ValueError(
                "matrix configuration differs from the existing immutable study manifest"
            )
        return existing
    layout.initialize(proposed)
    return proposed


__all__ = [
    "add_study_run_arguments",
    "initialize_matrix_study",
    "study_context_from_args",
]
