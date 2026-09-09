"""Freeze a compact, reviewable evaluation evidence bundle for publication."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SelectedFile:
    source: Path
    published: Path
    purpose: str


class BundleSelection:
    """Collect unique source files under stable publication paths."""

    def __init__(self, repository: Path) -> None:
        self.repository = repository.resolve()
        self.files: dict[Path, SelectedFile] = {}

    def add(self, source: Path, published: Path, purpose: str) -> None:
        source = source.resolve()
        if not source.is_file() or not source.is_relative_to(self.repository):
            raise ValueError(f"evidence source is missing or outside repository: {source}")
        if published.is_absolute() or ".." in published.parts:
            raise ValueError(f"invalid publication path: {published}")
        previous = self.files.get(published)
        selected = SelectedFile(source=source, published=published, purpose=purpose)
        if previous is not None and previous != selected:
            raise ValueError(f"publication path collision: {published}")
        self.files[published] = selected


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _add_if_present(
    selection: BundleSelection,
    source: Path,
    published: Path,
    purpose: str,
) -> None:
    if source.is_file():
        selection.add(source, published, purpose)


def _add_tree_matches(
    selection: BundleSelection,
    root: Path,
    published_root: Path,
    pattern: str,
    purpose: str,
) -> None:
    for source in sorted(root.glob(pattern)):
        if source.is_file():
            selection.add(source, published_root / source.relative_to(root), purpose)


def _add_run_core(
    selection: BundleSelection,
    run: Path,
    published: Path,
    *,
    observations: bool,
    checkpoint: bool,
    tool_results: bool = True,
) -> None:
    core_files = {
        "manifest.json": "run_manifest",
        "evaluation/task.json": "independent_task_judgment",
        "trajectory/trace.json": "trajectory_trace",
        "trajectory/events.jsonl": "controller_events",
        "trajectory/verification.json": "strict_certificate",
        "result/summary.txt": "final_answer",
    }
    for relative, purpose in core_files.items():
        _add_if_present(selection, run / relative, published / relative, purpose)
    if tool_results:
        _add_tree_matches(
            selection,
            run,
            published,
            "evidence/tool-results/*.json",
            "hash_bound_tool_result",
        )
    if observations:
        for pattern, purpose in (
            ("observations/step_*/*.json", "paired_observation_state"),
            ("observations/step_*/pre.png", "paired_pre_image"),
            ("observations/step_*/post.png", "paired_post_image"),
        ):
            _add_tree_matches(selection, run, published, pattern, purpose)
    if checkpoint:
        _add_if_present(
            selection,
            run / "control/checkpoints/latest.json",
            published / "control/checkpoints/latest.json",
            "resume_checkpoint",
        )


def _failed_campaign_run(campaign: Path) -> Path:
    failed_runs = [
        task_path.parent.parent
        for task_path in campaign.glob("studies/**/runs/*/evaluation/task.json")
        if "/shards/" not in task_path.as_posix() and _load_json(task_path).get("passed") is False
    ]
    if len(failed_runs) != 1:
        raise ValueError(f"expected exactly one failed campaign run, found {len(failed_runs)}")
    return failed_runs[0]


def _add_long_horizon_runs(selection: BundleSelection, campaign: Path) -> None:
    long_runs = sorted(
        task_path.parent.parent
        for task_path in campaign.glob("studies/long-horizon/**/runs/*/evaluation/task.json")
    )
    if len(long_runs) != 2:
        raise ValueError(f"expected two long-horizon runs, found {len(long_runs)}")
    for index, run in enumerate(long_runs, start=1):
        model = _load_json(run / "manifest.json").get("model", f"model-{index}")
        label = re.sub(r"[^a-z0-9]+", "-", str(model).casefold()).strip("-")
        _add_run_core(
            selection,
            run,
            Path("long-horizon") / label / run.name,
            observations=False,
            checkpoint=True,
            tool_results=False,
        )


def _campaign_selection(selection: BundleSelection, campaign: Path) -> None:
    prefix = Path("campaign")
    selection.add(campaign / "campaign.json", prefix / "campaign.json", "campaign_contract")
    batches = sorted(campaign.glob("batches/*/*/batch.json"))
    if len(batches) != 1:
        raise ValueError(f"expected exactly one campaign batch, found {len(batches)}")
    batch = batches[0]
    batch_root = batch.parent
    selection.add(batch, prefix / batch.relative_to(campaign), "campaign_batch")
    for relative, purpose in (
        ("analysis/portfolio.json", "cross_suite_portfolio"),
        ("evidence/endpoint-probes.json", "endpoint_preflight"),
    ):
        _add_if_present(
            selection,
            batch_root / relative,
            prefix / (batch_root / relative).relative_to(campaign),
            purpose,
        )
    report_paths = _load_json(batch).get("report_paths")
    if not isinstance(report_paths, list) or len(report_paths) != 6:
        raise ValueError("campaign batch must reference exactly six suite reports")
    for raw_path in report_paths:
        source = campaign / str(raw_path)
        selection.add(source, prefix / Path(str(raw_path)), "suite_report")
        _add_if_present(
            selection,
            source.parent / "analysis/failures.json",
            prefix / (source.parent / "analysis/failures.json").relative_to(campaign),
            "suite_failure_analysis",
        )

    failed = _failed_campaign_run(campaign)
    _add_run_core(
        selection,
        failed,
        Path("failed-trajectory") / failed.name,
        observations=True,
        checkpoint=False,
    )

    _add_long_horizon_runs(selection, campaign)


def _strict_selection(selection: BundleSelection, strict: Path) -> None:
    model_roots = sorted(path for path in strict.iterdir() if (path / "results.json").is_file())
    if len(model_roots) != 2:
        raise ValueError(f"expected two strict model roots, found {len(model_roots)}")
    for model_root in model_roots:
        prefix = Path("strict") / model_root.name
        for name, purpose in (
            ("execution.json", "strict_execution"),
            ("results.json", "strict_suite_report"),
        ):
            selection.add(model_root / name, prefix / name, purpose)
        runs = sorted(path for path in (model_root / "runs").iterdir() if path.is_dir())
        if len(runs) != 1:
            raise ValueError(f"expected one strict run below {model_root}, found {len(runs)}")
        run = runs[0]
        published = prefix / "runs" / run.name
        _add_run_core(
            selection,
            run,
            published,
            observations=True,
            checkpoint=False,
        )
        _add_tree_matches(
            selection,
            run,
            published,
            "artifacts/downloads/*",
            "downloaded_report",
        )
        _add_tree_matches(
            selection,
            run,
            published,
            "result/attachments/*",
            "figure_attachment",
        )


def select_evidence(repository: Path, campaign: Path, strict: Path) -> BundleSelection:
    selection = BundleSelection(repository)
    _campaign_selection(selection, campaign.resolve())
    _strict_selection(selection, strict.resolve())
    return selection


def _privacy_failures(files: list[Path]) -> list[str]:
    patterns = {
        "OpenAI-style secret": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
        "Bearer credential": re.compile(rb"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}"),
        "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    }
    failures: list[str] = []
    for path in files:
        if path.name.startswith(".env") or path.name in {"cookies.json", "storage_state.json"}:
            failures.append(f"sensitive filename selected: {path}")
            continue
        if path.suffix.casefold() not in {".json", ".jsonl", ".txt", ".md"}:
            continue
        payload = path.read_bytes()
        for label, pattern in patterns.items():
            if pattern.search(payload):
                failures.append(f"{label} detected in {path}")
    return failures


def _archive_path(published: Path) -> Path | None:
    parts = published.parts
    is_trace_evidence = "observations" in parts or ("evidence" in parts and "tool-results" in parts)
    if not is_trace_evidence:
        return None
    if parts[0] == "strict":
        return Path("archives") / f"strict-{parts[1]}-trace-evidence.tar.gz"
    if parts[0] == "failed-trajectory":
        return Path("archives/failed-trajectory-evidence.tar.gz")
    return None


def _write_archive(target: Path, members: list[tuple[Path, SelectedFile]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        target.open("wb") as raw_handle,
        gzip.GzipFile(fileobj=raw_handle, mode="wb", mtime=0) as gzip_handle,
        tarfile.open(fileobj=gzip_handle, mode="w") as archive,
    ):
        for published, item in members:
            info = archive.gettarinfo(item.source, arcname=published.as_posix())
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644
            info.mtime = 0
            with item.source.open("rb") as source_handle:
                archive.addfile(info, source_handle)


def _copy_selected_files(
    temporary: Path,
    repository: Path,
    selection: BundleSelection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    archive_groups: dict[Path, list[tuple[Path, SelectedFile]]] = {}
    for published, item in sorted(selection.files.items()):
        archive_path = _archive_path(published)
        record = {
            "path": published.as_posix(),
            "purpose": item.purpose,
            "source_path": item.source.relative_to(repository).as_posix(),
            "sha256": _sha256(item.source),
            "bytes": item.source.stat().st_size,
            "byte_identical_to_source": True,
        }
        if archive_path is None:
            target = temporary / published
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item.source, target)
            record["storage"] = {"kind": "direct", "path": published.as_posix()}
        else:
            archive_groups.setdefault(archive_path, []).append((published, item))
            record["storage"] = {
                "kind": "tar_gzip_member",
                "archive": archive_path.as_posix(),
                "member": published.as_posix(),
            }
        records.append(record)

    archive_records: list[dict[str, Any]] = []
    for archive_path, members in sorted(archive_groups.items()):
        target = temporary / archive_path
        _write_archive(target, members)
        archive_records.append(
            {
                "path": archive_path.as_posix(),
                "sha256": _sha256(target),
                "bytes": target.stat().st_size,
                "member_count": len(members),
                "extract_at": ".",
            }
        )
    return records, archive_records


def freeze_bundle(
    repository: Path,
    campaign: Path,
    strict: Path,
    output: Path,
) -> dict[str, Any]:
    repository = repository.resolve()
    output = output.resolve()
    published_root = (repository / "outputs/published").resolve()
    if not output.is_relative_to(published_root) or output == published_root:
        raise ValueError("output must be a dated subdirectory below outputs/published")
    if output.exists():
        raise FileExistsError(f"refusing to replace existing evidence bundle: {output}")
    selection = select_evidence(repository, campaign, strict)
    privacy_failures = _privacy_failures([item.source for item in selection.files.values()])
    if privacy_failures:
        raise ValueError("publication privacy scan failed:\n" + "\n".join(privacy_failures))

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        records, archive_records = _copy_selected_files(temporary, repository, selection)
        manifest = {
            "schema_version": 1,
            "bundle_id": output.name,
            "created_at": datetime.now(UTC).isoformat(),
            "selection_policy": {
                "campaign": "contracts, batch, portfolio, six suite reports, failure analyses",
                "strict": "complete hash-verifiable trace closure without duplicate render trees",
                "failure": "the only failed trajectory with paired observations and tool evidence",
                "long_horizon": "trace, task judgment, events, final answer, and resume checkpoint",
                "excluded": [
                    "ordinary successful task run directories",
                    "promoted shard duplicates",
                    "content-addressed image blobs duplicated by paired images",
                    "compatibility screenshot copies",
                    "HTML reports and planner-turn duplicates",
                    "checkpoint backups and logs",
                ],
            },
            "restoration": {
                "command": 'for archive in archives/*.tar.gz; do tar -xzf "$archive"; done',
                "note": (
                    "Extract archives at the bundle root to restore hash-bound observation "
                    "and tool-result paths before re-running artifact verification."
                ),
            },
            "privacy_review": {
                "credential_scan_passed": True,
                "files_are_byte_identical": True,
                "notice": (
                    "Hash-bound evidence is copied byte-for-byte. Some records retain the "
                    "non-secret local workspace paths emitted during the original run."
                ),
            },
            "file_count": len(records),
            "total_bytes": sum(int(record["bytes"]) for record in records),
            "physical_evidence_file_count": len(records)
            - sum(record["member_count"] for record in archive_records)
            + len(archive_records),
            "archives": archive_records,
            "files": records,
        }
        (temporary / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        readme = (
            f"# Frozen evaluation evidence: {output.name}\n\n"
            "This reviewed bundle retains the machine-readable aggregates, the complete "
            "strict-certificate evidence closure, the only failed trajectory, and compact "
            "long-horizon resume evidence. See `MANIFEST.json` for source paths, purposes, "
            "sizes, and SHA-256 digests. High-value records remain directly browsable; "
            "archives contain the many small observation and tool-result files. Extract "
            "each archive at this directory before re-running artifact verification. "
            "Evidence members are byte-identical to the local source runs; bulk successful "
            "trajectories and duplicate render trees remain local.\n"
        )
        (temporary / "README.md").write_text(readme, encoding="utf-8")
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--strict", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    manifest = freeze_bundle(repository, args.campaign, args.strict, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "file_count": manifest["physical_evidence_file_count"] + 2,
                "evidence_file_count": manifest["file_count"],
                "total_evidence_bytes": manifest["total_bytes"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
