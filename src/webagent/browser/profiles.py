"""Owned temporary profiles and persistent Chromium clean-exit metadata."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_TEMPORARY_PROFILE_PREFIX = "webagent-profile-"
_TEMPORARY_PROFILE_MARKER = ".webagent-owner.json"


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def mark_profile_clean(user_data_dir: str | Path) -> None:
    """Repair Chromium's persisted clean-exit flags on a stopped profile.

    Chromium writes crash markers as soon as a profile starts and normally
    clears them during shutdown. A killed process or a partial Playwright start
    can leave those markers behind, causing the next headed launch to show the
    "didn't shut down correctly" restore banner. This best-effort repair runs
    before launch and after all browser processes have been asked to stop.
    """
    profile_root = Path(user_data_dir)
    updates: tuple[tuple[Path, tuple[str, ...], Any], ...] = (
        (profile_root / "Default" / "Preferences", ("profile", "exit_type"), "Normal"),
        (
            profile_root / "Local State",
            ("user_experience_metrics", "stability", "exited_cleanly"),
            True,
        ),
    )
    for path, keys, value in updates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            node = payload
            for key in keys[:-1]:
                child = node.get(key)
                if not isinstance(child, dict):
                    child = {}
                    node[key] = child
                node = child
            if node.get(keys[-1]) == value:
                continue
            node[keys[-1]] = value
            temporary = path.with_name(f".{path.name}.webagent.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
        except (OSError, TypeError, ValueError):
            logger.warning("Could not repair Chromium clean-exit state in %s", path, exc_info=True)


def create_temporary_profile(root: Path | None, *, stale_max_age_seconds: float) -> Path:
    root = root or Path(tempfile.gettempdir()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cleanup_stale_temporary_profiles(root, stale_max_age_seconds=stale_max_age_seconds)
    profile = Path(
        tempfile.mkdtemp(
            prefix=_TEMPORARY_PROFILE_PREFIX,
            dir=root,
        )
    )
    marker = {
        "pid": os.getpid(),
        "created_at": time.time(),
        "kind": "webagent-temporary-profile",
    }
    try:
        (profile / _TEMPORARY_PROFILE_MARKER).write_text(
            json.dumps(marker, separators=(",", ":")), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Could not mark temporary browser profile %s: %s", profile, exc)
    return profile


def cleanup_stale_temporary_profiles(root: Path, *, stale_max_age_seconds: float) -> None:
    """Remove only marked, old profiles whose creating process is no longer alive."""
    now = time.time()
    try:
        candidates = tuple(root.glob(f"{_TEMPORARY_PROFILE_PREFIX}*"))
    except OSError as exc:
        logger.warning("Could not scan temporary browser profiles in %s: %s", root, exc)
        return
    for candidate in candidates:
        marker_path = candidate / _TEMPORARY_PROFILE_MARKER
        if not candidate.is_dir() or not marker_path.is_file():
            continue
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            pid = int(marker["pid"])
            created_at = float(marker["created_at"])
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if now - created_at < stale_max_age_seconds or _pid_is_running(pid):
            continue
        try:
            shutil.rmtree(candidate)
            logger.info("Removed stale temporary browser profile %s", candidate)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Failed to remove stale temporary profile %s: %s", candidate, exc)
