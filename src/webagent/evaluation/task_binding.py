"""Content-addressed bindings between preregistered manifests and executed tasks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from webagent.evaluation.models import BenchmarkTask


def tasks_from_manifest_bytes(payload: bytes) -> tuple[BenchmarkTask, ...]:
    """Parse either a suite manifest object or a retained bare task list."""
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("retained task manifest is not valid UTF-8 JSON") from exc
    raw_tasks: object
    if isinstance(decoded, dict):
        raw_tasks = decoded.get("tasks")
    else:
        raw_tasks = decoded
    if not isinstance(raw_tasks, list):
        raise ValueError("retained task manifest must contain a tasks list")
    try:
        tasks = tuple(BenchmarkTask.model_validate(item) for item in raw_tasks)
    except ValueError as exc:
        raise ValueError("retained task manifest contains an invalid benchmark task") from exc
    _require_unique_task_ids(tasks)
    return tasks


def task_set_sha256(tasks: Sequence[BenchmarkTask]) -> str:
    """Hash the ordered, complete task contract independently of run budgets.

    ``max_steps`` is governed by :class:`StudyBudgets` and is validated at run
    time.  Controlled-environment origins are allocated dynamically, so their
    loopback or ``.invalid`` authorities are replaced with stable role tokens;
    paths, assertions, goals, splits, feedback, and all other task semantics
    remain hash-bound.
    """
    _require_unique_task_ids(tasks)
    controlled_origins: dict[str, str] = {}
    canonical: list[dict[str, Any]] = []
    for task in tasks:
        raw = task.model_dump(mode="json")
        raw.pop("max_steps", None)
        if task.environment == "sandbox":
            raw = _normalize_controlled_value(raw, controlled_origins)
        canonical.append(raw)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def task_set_sha256_from_manifest_bytes(payload: bytes) -> str:
    """Return the stable task-set digest encoded by retained manifest bytes."""
    return task_set_sha256(tasks_from_manifest_bytes(payload))


def _require_unique_task_ids(tasks: Sequence[BenchmarkTask]) -> None:
    ids = [task.id for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark task set contains duplicate task ids")


def _normalize_controlled_value(value: Any, origins: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _normalize_controlled_url(value, origins)
    if isinstance(value, list):
        return [_normalize_controlled_value(item, origins) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _normalize_controlled_value(item, origins) for key, item in value.items()}
    return value


def _normalize_controlled_url(value: str, origins: dict[str, str]) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return value
    hostname = parsed.hostname.casefold()
    if hostname not in {"127.0.0.1", "localhost", "::1"} and not hostname.endswith(".invalid"):
        return value
    origin = f"{parsed.scheme}://{parsed.netloc}"
    token = origins.setdefault(origin, f"controlled-origin-{len(origins) + 1}.invalid")
    return urlunsplit(("https", token, parsed.path, parsed.query, parsed.fragment))


__all__ = [
    "task_set_sha256",
    "task_set_sha256_from_manifest_bytes",
    "tasks_from_manifest_bytes",
]
