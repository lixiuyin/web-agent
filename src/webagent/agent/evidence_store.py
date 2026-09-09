"""Immutable full tool evidence and append-only execution lifecycle events."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SECRETS = {
    "api_key",
    "token",
    "password",
    "authorization",
    "cookie",
    "set-cookie",
    "base64",
    "data_url",
    "image",
}


def redact(value: Any, key: str = "") -> Any:
    if key.casefold() in _SECRETS:
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, bytes):
        return "[binary omitted]"
    return value


def save_result(root: Path, value: Any) -> dict[str, str]:
    payload = json.dumps(redact(value), ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(payload).hexdigest()
    path = root / "evidence" / "tool-results" / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("xb") as output:
            output.write(payload)
    return {"path": path.relative_to(root).as_posix(), "sha256": digest}


def record_event(root: Path, kind: str, step: int, **values: Any) -> None:
    path = root / "trajectory" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"type": kind, "step": step, "timestamp": datetime.now(UTC).isoformat(), **values}
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(redact(event), ensure_ascii=False) + "\n")
        output.flush()
