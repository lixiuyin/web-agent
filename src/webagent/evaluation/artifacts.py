"""Typed, ownership-aware filesystem layout for runs and output workspaces.

``AgentConfig.output_dir`` remains the exact directory for one programmatic
run.  The CLI treats its configured value as an :class:`OutputWorkspace` when
``--output`` is omitted and allocates an isolated child below ``runs/``.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from webagent.evaluation.studies import StudyManifest

RUN_MANIFEST_FORMAT = "webagent-run"
RUN_LAYOUT_VERSION = 1
RUN_MANIFEST_SCHEMA_VERSION = 1
RUN_MANIFEST_SCHEMA_URI = (
    "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
    "src/webagent/schemas/run-manifest-v1.schema.json"
)
STUDY_EXECUTION_FORMAT = "webagent-study-execution"
STUDY_EXECUTION_LAYOUT_VERSION = 2
_STUDY_EXECUTION_CLAIM_FIELDS = frozenset(
    {
        "format",
        "layout_version",
        "execution_id",
        "claimed_at",
        "study_id",
        "task_manifest_sha256",
        "task_set_sha256",
    }
)
_RUN_MANIFEST_FIELDS = frozenset(
    {
        "$schema",
        "schema_version",
        "format",
        "layout_version",
        "run_id",
        "created_at",
        "task_sha256",
        "model",
    }
)


class RunOwnershipError(ValueError):
    """Raised when a non-empty directory is not owned by webagent."""


def safe_slug(value: str, *, fallback: str, max_length: int = 64) -> str:
    """Return one portable, non-empty filesystem component.

    Slugging is used for descriptive labels such as model names. Identifiers
    that must remain byte-for-byte stable should use :func:`safe_component`.
    """
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    return (slug or fallback)[:max_length].rstrip("-") or fallback


def safe_component(value: str, field_name: str) -> str:
    """Validate one literal path component on POSIX and Windows."""
    normalized = value.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or Path(normalized).name != normalized
    ):
        raise ValueError(f"{field_name} must be a plain non-empty path component")
    return normalized


def _turn_number(turn_index: int) -> str:
    if isinstance(turn_index, bool) or turn_index < 1:
        raise ValueError("turn_index must be a positive integer")
    return f"{turn_index:03d}"


@dataclass(frozen=True, slots=True)
class RunLayout:
    """Canonical paths for one run, plus legacy read locations.

    Tool-created files deliberately stay below ``artifacts/`` so existing
    containment checks based on ``artifacts_dir.parent`` remain valid.
    Controller state, observations, answers, and evaluation products no longer
    share that evidence directory.
    """

    root: Path

    @classmethod
    def from_root(cls, root: str | Path) -> RunLayout:
        return cls(Path(root).expanduser().resolve())

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def trajectory_dir(self) -> Path:
        return self.root / "trajectory"

    @property
    def trace_path(self) -> Path:
        return self.trajectory_dir / "trace.json"

    @property
    def trajectory_turns_dir(self) -> Path:
        return self.trajectory_dir / "turns"

    def turn_trace_path(self, turn_index: int) -> Path:
        """Return the immutable trace snapshot path for one session turn."""
        return self.trajectory_turns_dir / f"turn-{_turn_number(turn_index)}.json"

    @property
    def verification_path(self) -> Path:
        return self.trajectory_dir / "verification.json"

    @property
    def observations_dir(self) -> Path:
        return self.root / "observations"

    @property
    def screenshots_dir(self) -> Path:
        return self.observations_dir / "screenshots"

    @property
    def control_dir(self) -> Path:
        return self.root / "control"

    @property
    def checkpoints_dir(self) -> Path:
        return self.control_dir / "checkpoints"

    @property
    def checkpoint_path(self) -> Path:
        return self.checkpoints_dir / "latest.json"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def downloads_dir(self) -> Path:
        return self.artifacts_dir / "downloads"

    @property
    def documents_dir(self) -> Path:
        return self.artifacts_dir / "documents"

    @property
    def figures_dir(self) -> Path:
        return self.artifacts_dir / "figures"

    @property
    def files_dir(self) -> Path:
        return self.artifacts_dir / "files"

    @property
    def result_dir(self) -> Path:
        return self.root / "result"

    @property
    def summary_path(self) -> Path:
        return self.result_dir / "summary.txt"

    @property
    def attachments_dir(self) -> Path:
        return self.result_dir / "attachments"

    @property
    def result_turns_dir(self) -> Path:
        return self.result_dir / "turns"

    def turn_result_dir(self, turn_index: int) -> Path:
        return self.result_turns_dir / f"turn-{_turn_number(turn_index)}"

    def turn_summary_path(self, turn_index: int) -> Path:
        return self.turn_result_dir(turn_index) / "summary.txt"

    def turn_attachments_dir(self, turn_index: int) -> Path:
        return self.turn_result_dir(turn_index) / "attachments"

    @property
    def evaluation_dir(self) -> Path:
        return self.root / "evaluation"

    @property
    def legacy_trace_path(self) -> Path:
        return self.artifacts_dir / "run.json"

    @property
    def legacy_verification_path(self) -> Path:
        return self.artifacts_dir / "verification.json"

    @property
    def legacy_checkpoint_path(self) -> Path:
        return self.artifacts_dir / "checkpoint.json"

    def trace_path_for_read(self) -> Path:
        """Prefer the current trace, falling back to a legacy run artifact."""
        return self.trace_path if self.trace_path.is_file() else self.legacy_trace_path

    def verification_path_for_read(self) -> Path:
        """Prefer the current certificate, falling back to the v0 location."""
        if self.verification_path.is_file():
            return self.verification_path
        return self.legacy_verification_path

    def checkpoint_path_for_read(self) -> Path:
        """Prefer the current checkpoint, falling back to the v0 location."""
        return (
            self.checkpoint_path if self.checkpoint_path.is_file() else self.legacy_checkpoint_path
        )

    @classmethod
    def root_from_checkpoint(cls, path: str | Path) -> Path:
        """Infer a run root from either current or legacy checkpoint paths."""
        checkpoint = Path(path).expanduser().resolve()
        if checkpoint.parent.name == "checkpoints" and checkpoint.parent.parent.name == "control":
            return checkpoint.parent.parent.parent
        if checkpoint.parent.name == "artifacts":
            return checkpoint.parent.parent
        return checkpoint.parent

    def prepare(self, *, run_id: str, task: str, model: str) -> None:
        """Create a clean owned run without deleting an arbitrary output root.

        A non-empty directory without a valid ownership manifest is rejected.
        For an owned run only known generated entries are cleared; unknown
        sibling files are preserved.
        """
        self._validate_safe_root()
        if self.root.exists():
            entries = list(self.root.iterdir())
            if entries:
                self._require_owned_manifest()
                self._clear_generated_entries()
        self._create_directories()
        self._write_manifest(run_id=run_id, task=task, model=model)

    def ensure_for_resume(self, *, run_id: str, task: str, model: str) -> None:
        """Migrate a validated checkpoint run without clearing legacy data.

        Callers must validate the checkpoint before invoking this method. A
        legacy run has no ownership manifest, so successful validation is the
        authority for adopting it into the current layout.
        """
        self._validate_safe_root()
        if self.manifest_path.exists():
            self._require_owned_manifest()
        self._create_directories()
        if not self.manifest_path.exists():
            self._write_manifest(run_id=run_id, task=task, model=model)

    def _validate_safe_root(self) -> None:
        root = self.root.resolve()
        if root == root.parent or root == Path.cwd().resolve():
            raise ValueError(f"Refusing to prepare unsafe output directory: {root}")

    def _require_owned_manifest(self) -> dict[str, Any]:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunOwnershipError(
                f"Refusing to clean non-empty unowned run directory: {self.root}"
            ) from exc
        if not isinstance(value, dict):
            raise RunOwnershipError(f"Invalid run ownership manifest: {self.manifest_path}")
        try:
            created_at = datetime.fromisoformat(str(value.get("created_at", "")))
        except ValueError as exc:
            raise RunOwnershipError(
                f"Invalid run ownership manifest: {self.manifest_path}"
            ) from exc
        valid = bool(
            set(value) == _RUN_MANIFEST_FIELDS
            and value.get("$schema") == RUN_MANIFEST_SCHEMA_URI
            and value.get("schema_version") == RUN_MANIFEST_SCHEMA_VERSION
            and value.get("format") == RUN_MANIFEST_FORMAT
            and value.get("layout_version") == RUN_LAYOUT_VERSION
            and isinstance(value.get("run_id"), str)
            and bool(value["run_id"])
            and created_at.tzinfo is not None
            and isinstance(value.get("task_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", value["task_sha256"]) is not None
            and isinstance(value.get("model"), str)
            and bool(value["model"])
        )
        if not valid:
            raise RunOwnershipError(f"Invalid run ownership manifest: {self.manifest_path}")
        return value

    def _clear_generated_entries(self) -> None:
        for path in (
            self.manifest_path,
            self.trajectory_dir,
            self.observations_dir,
            self.control_dir,
            self.artifacts_dir,
            self.result_dir,
            self.evaluation_dir,
        ):
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()

    def _create_directories(self) -> None:
        for path in (
            self.trajectory_dir,
            self.trajectory_turns_dir,
            self.screenshots_dir,
            self.checkpoints_dir,
            self.downloads_dir,
            self.documents_dir,
            self.figures_dir,
            self.files_dir,
            self.attachments_dir,
            self.result_turns_dir,
            self.evaluation_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _write_manifest(self, *, run_id: str, task: str, model: str) -> None:
        manifest = {
            "$schema": RUN_MANIFEST_SCHEMA_URI,
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "format": RUN_MANIFEST_FORMAT,
            "layout_version": RUN_LAYOUT_VERSION,
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
            "model": model,
        }
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.manifest_path)


@dataclass(frozen=True, slots=True)
class OutputWorkspace:
    """Root that allocates unique, date/model/task-addressable run directories."""

    root: Path

    @classmethod
    def from_root(cls, root: str | Path) -> OutputWorkspace:
        return cls(Path(root).expanduser().resolve())

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def studies_dir(self) -> Path:
        return self.root / "studies"

    def study(self, study_id: str) -> StudyLayout:
        """Return a canonical study layout without creating it."""
        return StudyLayout.from_root(self.studies_dir / safe_component(study_id, "study_id"))

    def allocate_run(
        self,
        *,
        task: str,
        model: str,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> RunLayout:
        """Return a unique run layout without mutating the workspace."""
        timestamp = now or datetime.now(UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        identifier = run_id or str(uuid4())
        model_slug = safe_slug(model, fallback="model")
        task_slug = safe_slug(task, fallback="task")
        leaf = f"{task_slug}-{identifier.replace('-', '')[:10]}"
        return RunLayout.from_root(
            self.runs_dir / timestamp.astimezone(UTC).date().isoformat() / model_slug / leaf
        )


@dataclass(frozen=True, slots=True)
class StudyLayout:
    """Canonical paths for a collection of comparable benchmark runs."""

    root: Path

    @classmethod
    def from_root(cls, root: str | Path) -> StudyLayout:
        return cls(Path(root).expanduser().resolve())

    @property
    def manifest_path(self) -> Path:
        return self.root / "study.json"

    @property
    def inputs_dir(self) -> Path:
        return self.root / "inputs"

    @property
    def task_manifests_dir(self) -> Path:
        return self.inputs_dir / "task-manifests"

    @property
    def runs_dir(self) -> Path:
        """Legacy study-run namespace retained for one compatibility cycle."""
        return self.root / "runs"

    @property
    def executions_dir(self) -> Path:
        return self.root / "executions"

    @property
    def ledger_dir(self) -> Path:
        return self.root / "ledger"

    @property
    def ledger_path(self) -> Path:
        return self.ledger_dir / "runs.jsonl"

    @property
    def time_slices_path(self) -> Path:
        """Aggregate longitudinal summaries kept separate from task-run rows."""
        return self.ledger_dir / "time-slices.jsonl"

    @property
    def evidence_dir(self) -> Path:
        return self.root / "evidence"

    @property
    def logs_dir(self) -> Path:
        return self.evidence_dir / "logs"

    @property
    def analysis_dir(self) -> Path:
        return self.root / "analysis"

    @property
    def matrix_snapshots_dir(self) -> Path:
        return self.analysis_dir / "matrices"

    @property
    def matrix_latest_path(self) -> Path:
        """Compatibility view of the latest immutable matrix snapshot."""
        return self.root / "matrix.json"

    @property
    def report_path(self) -> Path:
        """Compatibility entry point for the complete benchmark report."""
        return self.root / "results.json"

    def task_run(self, task_id: str) -> RunLayout:
        """Legacy direct task lookup retained for existing study readers."""
        return RunLayout.from_root(self.runs_dir / safe_component(task_id, "task_id"))

    def allocate_execution(
        self,
        *,
        model: str,
        condition: str,
        now: datetime | None = None,
        execution_id: str | None = None,
    ) -> StudyExecutionLayout:
        """Allocate a unique comparison execution without creating it."""
        timestamp = now or datetime.now(UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        timestamp = timestamp.astimezone(UTC)
        identifier = execution_id or uuid4().hex[:12]
        leaf = (
            f"{timestamp.strftime('%H%M%S-%f')}-"
            f"{safe_slug(identifier, fallback='execution', max_length=32)}"
        )
        return StudyExecutionLayout.from_root(
            self.executions_dir
            / timestamp.date().isoformat()
            / safe_slug(model, fallback="model")
            / safe_slug(condition, fallback="condition")
            / leaf
        )

    def prepare(self) -> None:
        """Create study namespaces without deleting or overwriting prior evidence."""
        for path in (
            self.inputs_dir,
            self.task_manifests_dir,
            self.executions_dir,
            self.ledger_dir,
            self.evidence_dir,
            self.logs_dir,
            self.analysis_dir,
            self.matrix_snapshots_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def initialize(self, manifest: StudyManifest) -> None:
        """Create the study namespaces and publish its immutable typed contract."""
        from webagent.evaluation.studies import write_study_manifest

        if manifest.study_id != self.root.name:
            raise ValueError(
                "study manifest id must match the canonical study directory name: "
                f"{manifest.study_id!r} != {self.root.name!r}"
            )
        self.prepare()
        write_study_manifest(self.manifest_path, manifest)


@dataclass(frozen=True, slots=True)
class StudyExecutionLayout:
    """One non-overwriting benchmark execution within a study."""

    root: Path

    @classmethod
    def from_root(cls, root: str | Path) -> StudyExecutionLayout:
        return cls(Path(root).expanduser().resolve())

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def claim_path(self) -> Path:
        return self.root / "execution.json"

    @property
    def inputs_dir(self) -> Path:
        return self.root / "inputs"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def analysis_dir(self) -> Path:
        return self.root / "analysis"

    @property
    def evidence_dir(self) -> Path:
        return self.root / "evidence"

    @property
    def logs_dir(self) -> Path:
        return self.evidence_dir / "logs"

    @property
    def retained_manifests_dir(self) -> Path:
        return self.evidence_dir / "manifests"

    @property
    def retained_reports_dir(self) -> Path:
        return self.evidence_dir / "reports"

    @property
    def ledger_dir(self) -> Path:
        return self.root / "ledger"

    @property
    def ledger_path(self) -> Path:
        """Execution-local aggregate time-slice evidence (not the study ledger)."""
        return self.ledger_dir / "time-slices.jsonl"

    @property
    def shards_dir(self) -> Path:
        return self.root / "shards"

    @property
    def control_dir(self) -> Path:
        return self.root / "control"

    @property
    def browser_profiles_dir(self) -> Path:
        return self.control_dir / "browser-profiles"

    @property
    def report_path(self) -> Path:
        return self.root / "results.json"

    def task_run(self, task_id: str) -> RunLayout:
        return RunLayout.from_root(self.runs_dir / safe_component(task_id, "task_id"))

    def prepare(
        self,
        *,
        study_id: str | None = None,
        task_manifest_sha256: str | None = None,
        task_set_sha256: str | None = None,
    ) -> None:
        """Atomically claim and prepare a fresh execution exactly once.

        Even empty managed directories from an interrupted or manual setup are
        treated as a partial execution.  Once ``execution.json`` is published,
        callers must use :meth:`require_prepared` rather than preparing again.
        Study executions bind both the retained manifest bytes and the complete
        canonical task set; ordinary one-off benchmark runs leave all three
        study binding fields null.
        """
        self._validate_study_binding(
            study_id=study_id,
            task_manifest_sha256=task_manifest_sha256,
            task_set_sha256=task_set_sha256,
        )
        self._validate_safe_root()
        if self.root.exists() and any(self.root.iterdir()):
            raise FileExistsError(
                f"benchmark execution already contains run evidence or a claim: {self.root}"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        claim = {
            "format": STUDY_EXECUTION_FORMAT,
            "layout_version": STUDY_EXECUTION_LAYOUT_VERSION,
            "execution_id": uuid4().hex,
            "claimed_at": datetime.now(UTC).isoformat(),
            "study_id": study_id,
            "task_manifest_sha256": task_manifest_sha256,
            "task_set_sha256": task_set_sha256,
        }
        try:
            with self.claim_path.open("x", encoding="utf-8") as handle:
                json.dump(claim, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except FileExistsError as exc:
            raise FileExistsError(
                f"benchmark execution has already been claimed: {self.root}"
            ) from exc
        for path in (
            self.inputs_dir,
            self.runs_dir,
            self.artifacts_dir,
            self.analysis_dir,
            self.evidence_dir,
            self.logs_dir,
            self.ledger_dir,
            self.control_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def require_prepared(
        self,
        *,
        study_id: str | None = None,
        task_manifest_sha256: str | None = None,
        task_set_sha256: str | None = None,
    ) -> None:
        """Validate a claim, namespaces, and any expected study/task binding."""
        self._validate_study_binding(
            study_id=study_id,
            task_manifest_sha256=task_manifest_sha256,
            task_set_sha256=task_set_sha256,
        )
        try:
            claim = json.loads(self.claim_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunOwnershipError(
                f"benchmark execution is missing a valid claim: {self.root}"
            ) from exc
        if not isinstance(claim, dict):
            raise RunOwnershipError(f"benchmark execution has an invalid claim: {self.claim_path}")
        try:
            claimed_at = datetime.fromisoformat(str(claim.get("claimed_at", "")))
        except ValueError as exc:
            raise RunOwnershipError(
                f"benchmark execution has an invalid claim: {self.claim_path}"
            ) from exc
        valid = bool(
            set(claim) == _STUDY_EXECUTION_CLAIM_FIELDS
            and claim.get("format") == STUDY_EXECUTION_FORMAT
            and claim.get("layout_version") == STUDY_EXECUTION_LAYOUT_VERSION
            and isinstance(claim.get("execution_id"), str)
            and bool(claim["execution_id"])
            and claimed_at.tzinfo is not None
            and self._claim_study_binding_is_valid(claim)
        )
        required = (
            self.inputs_dir,
            self.runs_dir,
            self.artifacts_dir,
            self.analysis_dir,
            self.evidence_dir,
            self.logs_dir,
            self.ledger_dir,
            self.control_dir,
        )
        if not valid or any(not path.is_dir() for path in required):
            raise RunOwnershipError(f"benchmark execution is only partially prepared: {self.root}")
        expected = (study_id, task_manifest_sha256, task_set_sha256)
        if any(value is not None for value in expected) and expected != (
            claim["study_id"],
            claim["task_manifest_sha256"],
            claim["task_set_sha256"],
        ):
            raise RunOwnershipError(
                "benchmark execution claim differs from the preregistered task binding: "
                f"{self.claim_path}"
            )

    def study_binding(self) -> tuple[str, str, str] | None:
        """Return the validated study/task binding carried by this execution."""
        self.require_prepared()
        claim = json.loads(self.claim_path.read_text(encoding="utf-8"))
        if claim["study_id"] is None:
            return None
        return (
            str(claim["study_id"]),
            str(claim["task_manifest_sha256"]),
            str(claim["task_set_sha256"]),
        )

    @staticmethod
    def _validate_study_binding(
        *,
        study_id: str | None,
        task_manifest_sha256: str | None,
        task_set_sha256: str | None,
    ) -> None:
        values = (study_id, task_manifest_sha256, task_set_sha256)
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError(
                "study_id, task_manifest_sha256, and task_set_sha256 must be supplied together"
            )
        if study_id is not None:
            safe_component(study_id, "study_id")
        for field_name, digest in (
            ("task_manifest_sha256", task_manifest_sha256),
            ("task_set_sha256", task_set_sha256),
        ):
            if digest is not None and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")

    @staticmethod
    def _claim_study_binding_is_valid(claim: dict[str, Any]) -> bool:
        values = (
            claim.get("study_id"),
            claim.get("task_manifest_sha256"),
            claim.get("task_set_sha256"),
        )
        if values == (None, None, None):
            return True
        study_id, manifest_digest, task_set_digest = values
        return bool(
            isinstance(study_id, str)
            and study_id
            and Path(study_id).name == study_id
            and isinstance(manifest_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", manifest_digest)
            and isinstance(task_set_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", task_set_digest)
        )

    def _validate_safe_root(self) -> None:
        root = self.root.resolve()
        if root == root.parent or root == Path.cwd().resolve():
            raise ValueError(f"Refusing to prepare unsafe benchmark execution: {root}")


__all__ = [
    "RUN_LAYOUT_VERSION",
    "RUN_MANIFEST_FORMAT",
    "RUN_MANIFEST_SCHEMA_URI",
    "RUN_MANIFEST_SCHEMA_VERSION",
    "STUDY_EXECUTION_FORMAT",
    "STUDY_EXECUTION_LAYOUT_VERSION",
    "OutputWorkspace",
    "RunLayout",
    "RunOwnershipError",
    "StudyExecutionLayout",
    "StudyLayout",
    "safe_component",
    "safe_slug",
]
