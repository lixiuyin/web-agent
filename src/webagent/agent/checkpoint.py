"""Versioned, atomic checkpoints for resumable agent runs."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from webagent.agent.state import PlanningState
from webagent.agent.strategy import StrategyState

CHECKPOINT_SCHEMA_VERSION = 2
_CHECKPOINT_FORMAT = "webagent-checkpoint"
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "base64",
        "cookie",
        "cookies",
        "data_url",
        "image",
        "password",
        "screenshot",
        "secret",
        "storage_state",
        "token",
    }
)

type ReplayPolicy = Literal["safe", "reconcile", "forbid"]


class CheckpointError(RuntimeError):
    """Base checkpoint error."""


class CheckpointCorruptError(CheckpointError):
    """Checkpoint bytes, checksum, or schema are invalid."""


class CheckpointCompatibilityError(CheckpointError):
    """Checkpoint belongs to another task/config/source revision."""


class PendingAction(BaseModel):
    """Write-ahead marker for an action whose outcome may be ambiguous after a crash."""

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(min_length=1)
    parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    external_effect: str = Field(min_length=1)
    replay_policy: ReplayPolicy = "reconcile"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BrowserResumeState(BaseModel):
    """Non-secret browser coordinates; cookies/storage state are intentionally excluded."""

    model_config = ConfigDict(frozen=True)

    current_url: str | None = None
    tab_urls: tuple[str, ...] = ()
    active_tab_index: int = Field(default=0, ge=0)


class ArtifactRecord(BaseModel):
    """Artifact integrity entry checked before planner state is resumed."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_path(cls, path: Path, *, root: Path) -> ArtifactRecord:
        resolved = path.resolve()
        root = root.resolve()
        if resolved != root and not resolved.is_relative_to(root):
            raise ValueError("checkpoint artifact must remain under the run output root")
        raw = resolved.read_bytes()
        return cls(
            path=str(resolved.relative_to(root)),
            size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )


class AgentCheckpoint(BaseModel):
    """Complete controller state needed to continue at ``next_step``."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[2] = 2
    checkpoint_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    run_id: str = Field(min_length=1)
    resume_count: int = Field(default=0, ge=0)
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = "running"
    next_step: int = Field(default=1, ge=1)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    history: tuple[dict[str, Any], ...] = ()
    planner_attempts: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    planning_state: PlanningState | None = None
    strategy_state: StrategyState = Field(default_factory=StrategyState)
    loop_state: dict[str, Any] = Field(default_factory=dict)
    policy_state: dict[str, Any] = Field(default_factory=dict)
    browser_state: BrowserResumeState = Field(default_factory=BrowserResumeState)
    artifacts: tuple[ArtifactRecord, ...] = ()
    last_figure_path: str | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    final_result: dict[str, Any] = Field(default_factory=dict)
    pending_action: PendingAction | None = None
    previous_checkpoint_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class CheckpointStore:
    """Atomically persist one checkpoint plus a last-known-good backup."""

    def __init__(self, path: str | Path, *, keep_backup: bool = True) -> None:
        self.path = Path(path).expanduser().resolve()
        self.backup_path = self.path.with_name(f"{self.path.name}.bak")
        self.keep_backup = keep_backup

    def exists(self) -> bool:
        return self.path.is_file() or (self.keep_backup and self.backup_path.is_file())

    def save(self, checkpoint: AgentCheckpoint) -> Path:
        """Redact secret-bearing fields, checksum, and atomically replace the file."""
        updated = checkpoint.model_copy(update={"updated_at": datetime.now(UTC)})
        safe_data = _redact_checkpoint_value(updated.model_dump(mode="json"))
        safe_checkpoint = AgentCheckpoint.model_validate(safe_data)
        payload = _canonical_json(safe_checkpoint.model_dump(mode="json"))
        envelope = {
            "format": _CHECKPOINT_FORMAT,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_sha256": hashlib.sha256(payload).hexdigest(),
            "checkpoint": json.loads(payload),
        }
        encoded = json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.keep_backup and self.path.is_file():
            try:
                # Do not replace a good backup with a corrupt primary.
                self._load_path(self.path)
            except CheckpointError:
                pass
            else:
                _atomic_write(self.backup_path, self.path.read_bytes())
        _atomic_write(self.path, encoded)
        return self.path

    def load(
        self,
        *,
        expected_task: str | None = None,
        expected_config_fingerprint: str | None = None,
        expected_source_fingerprint: str | None = None,
    ) -> AgentCheckpoint:
        """Load the primary, falling back to backup only for corruption/missing bytes."""
        candidates = [self.path]
        if self.keep_backup:
            candidates.append(self.backup_path)
        last_error: Exception | None = None
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                checkpoint = self._load_path(candidate)
            except (CheckpointCorruptError, OSError) as exc:
                last_error = exc
                continue
            _validate_compatibility(
                checkpoint,
                expected_task=expected_task,
                expected_config_fingerprint=expected_config_fingerprint,
                expected_source_fingerprint=expected_source_fingerprint,
            )
            return checkpoint
        if last_error is not None:
            raise CheckpointCorruptError(f"No valid checkpoint copy: {last_error}") from last_error
        raise FileNotFoundError(self.path)

    def digest(self) -> str | None:
        """Return the verified payload digest for checkpoint chaining."""
        if not self.path.is_file():
            return None
        checkpoint = self._load_path(self.path)
        payload = _canonical_json(checkpoint.model_dump(mode="json"))
        return hashlib.sha256(payload).hexdigest()

    def missing_artifacts(self, checkpoint: AgentCheckpoint, *, root: Path) -> list[str]:
        """Return missing, escaped, size-changed, or hash-changed artifact paths."""
        root = root.resolve()
        invalid: list[str] = []
        for record in checkpoint.artifacts:
            path = (root / record.path).resolve()
            if path != root and not path.is_relative_to(root):
                invalid.append(record.path)
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                invalid.append(record.path)
                continue
            if len(raw) != record.size or hashlib.sha256(raw).hexdigest() != record.sha256:
                invalid.append(record.path)
        return invalid

    @staticmethod
    def _load_path(path: Path) -> AgentCheckpoint:
        envelope = _decode_envelope(path.read_bytes())
        checkpoint_data = envelope.get("checkpoint")
        if not isinstance(checkpoint_data, dict):
            raise CheckpointCorruptError("checkpoint payload is missing")
        payload = _canonical_json(checkpoint_data)
        expected = envelope.get("checkpoint_sha256")
        actual = hashlib.sha256(payload).hexdigest()
        if not isinstance(expected, str) or expected != actual:
            raise CheckpointCorruptError("checkpoint checksum mismatch")
        try:
            return AgentCheckpoint.model_validate(checkpoint_data)
        except ValueError as exc:
            raise CheckpointCorruptError(f"checkpoint schema validation failed: {exc}") from exc


def checkpoint_fingerprint(value: Any) -> str:
    """Stable SHA-256 for config snapshots, parameters, or other JSON state."""
    return hashlib.sha256(_canonical_json(_redact_checkpoint_value(value))).hexdigest()


def _validate_compatibility(
    checkpoint: AgentCheckpoint,
    *,
    expected_task: str | None,
    expected_config_fingerprint: str | None,
    expected_source_fingerprint: str | None,
) -> None:
    mismatches: list[str] = []
    if (
        expected_task is not None
        and checkpoint.task_sha256 != hashlib.sha256(expected_task.encode("utf-8")).hexdigest()
    ):
        mismatches.append("task")
    if (
        expected_config_fingerprint is not None
        and checkpoint.config_fingerprint != expected_config_fingerprint
    ):
        mismatches.append("config_fingerprint")
    if (
        expected_source_fingerprint is not None
        and checkpoint.source_fingerprint != expected_source_fingerprint
    ):
        mismatches.append("source_fingerprint")
    if mismatches:
        raise CheckpointCompatibilityError(
            "Checkpoint is incompatible with this run: " + ", ".join(mismatches)
        )


def _decode_envelope(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckpointCorruptError("checkpoint is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("format") != _CHECKPOINT_FORMAT:
        raise CheckpointCorruptError("unknown checkpoint format")
    if value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointCorruptError(
            f"unsupported checkpoint schema_version: {value.get('schema_version')!r}"
        )
    return value


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CheckpointCorruptError(f"checkpoint contains non-JSON state: {exc}") from exc


def _redact_checkpoint_value(value: Any, key: str = "") -> Any:
    if key.casefold() in _SECRET_KEYS:
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _redact_checkpoint_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_checkpoint_value(item) for item in value]
    return value


def _atomic_write(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        # Best effort directory sync makes rename durable on POSIX filesystems.
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "AgentCheckpoint",
    "ArtifactRecord",
    "BrowserResumeState",
    "CheckpointCompatibilityError",
    "CheckpointCorruptError",
    "CheckpointError",
    "CheckpointStore",
    "PendingAction",
    "ReplayPolicy",
    "checkpoint_fingerprint",
]
