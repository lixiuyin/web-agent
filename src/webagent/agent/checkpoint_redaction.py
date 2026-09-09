"""Checkpoint serialization: redaction, sanitization, and artifact collection.

Checkpoint files are crash-recovery state written before externally consequential
actions. They must never leak page text, secrets, credentials, or absolute
local paths, so every value that enters a checkpoint passes through the
sanitizers in this module.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from webagent.agent.checkpoint import ArtifactRecord, checkpoint_fingerprint
from webagent.agent.state import PlanningState, validate_durable_note
from webagent.agent.strategy import StrategyState
from webagent.core.config import AgentConfig
from webagent.core.models import AgentStep, PlannerAttempt

_CHECKPOINT_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "base64",
        "content",
        "cookie",
        "cookies",
        "data_url",
        "id_token",
        "password",
        "secret",
        "session",
        "session_id",
        "storage_state",
        "token",
    }
)
_CHECKPOINT_PATH_KEYS = frozenset({"path", "image_path", "output_dir", "upload_path"})
_CHECKPOINT_URL_KEYS = frozenset(
    {"url", "source_url", "browser_url", "current_url", "html_url", "pdf_url", "source"}
)
_CHECKPOINT_STRATEGY_VALUES = frozenset(
    {
        "default",
        "search-discovery",
        "semantic-dom",
        "alternate-navigation",
        "visual-grounding",
        "document-local",
        "recovery",
    }
)
_CHECKPOINT_FIXED_VALUES = frozenset(
    {
        "allow",
        "deny",
        "blocked",
        "completed",
        "failed",
        "fail",
        "forbid",
        "human_wait_timeout",
        "interrupted",
        "max_steps_reached",
        "none_or_reversible",
        "prompt",
        "reconcile",
        "report",
        "resolved_by_human",
        "running",
        "safe",
        "success",
        "timeout",
        "wait_for_human",
    }
)


def _config_fingerprint(config: AgentConfig) -> str:
    """Fingerprint compatibility-affecting, non-secret runtime settings."""
    # Include all behavior-affecting settings (provider URLs, parser/cache and
    # policy choices included), while excluding credentials and run location.
    value = config.model_dump(
        mode="json",
        exclude={
            "model_api_key",
            "google_search_api_key",
            "vllm_api_key",
            "marker_api_key",
            "mineru_api_key",
            "paddleocr_api_key",
            "output_dir",
            "checkpoint_filename",
        },
    )
    return checkpoint_fingerprint(value)


def _checkpoint_step(step: AgentStep, output_dir: Path) -> dict[str, Any]:
    """Build the minimum non-sensitive history representation needed to resume."""
    value = step.model_dump(mode="python")
    browser = value.get("browser_state")
    if isinstance(browser, dict):
        page_status = _checkpoint_page_status(
            f"{browser.get('title', '')} {browser.get('dom_summary', '')}"
        )
        browser.pop("screenshot", None)
        browser.pop("elements", None)
        browser.pop("observation_metadata", None)
        for key in ("viewport_context", "document_context", "viewport_blocks", "document_blocks"):
            browser.pop(key, None)
        browser["dom_summary"] = "(omitted from checkpoint; re-observe current page)"
        browser["url"] = _checkpoint_tab_url(browser.get("url"))
        browser["title"] = page_status
    tool_call = value.get("tool_call")
    if isinstance(tool_call, dict):
        name = str(tool_call.get("tool_name", ""))
        params = tool_call.get("parameters")
        tool_call["parameters"] = _checkpoint_parameters(name, params, output_dir)
        # Model-authored rationale is not controller state and may echo page input.
        tool_call["reasoning"] = ""
    tool_result = value.get("tool_result")
    if isinstance(tool_result, dict):
        tool_result["data"] = _checkpoint_value(tool_result.get("data", {}), output_dir)
        tool_result["audit"] = _checkpoint_value(tool_result.get("audit", {}), output_dir)
        if tool_result.get("error") is not None:
            tool_result["error"] = "tool action failed; details omitted"
    return value


def _checkpoint_page_status(value: str) -> str:
    """Retain only a coarse non-sensitive page-state class across resume."""
    normalized = value.casefold()
    if any(marker in normalized for marker in ("transient", "temporarily unavailable", "503")):
        return "transient_error"
    if any(marker in normalized for marker in ("captcha", "verify you are human")):
        return "captcha_challenge"
    if any(marker in normalized for marker in ("access denied", "forbidden", "403")):
        return "access_denied"
    if any(marker in normalized for marker in ("not found", "404")):
        return "not_found"
    return "normal"


def _checkpoint_parameters(tool_name: str, value: Any, output_dir: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sanitized = _checkpoint_value(value, output_dir)
    if not isinstance(sanitized, dict):
        return {}
    if tool_name.casefold() in {"type", "frame_interact", "shadow_dom"} and "text" in sanitized:
        sanitized["text"] = "[redacted]"
    return sanitized


def _checkpoint_value(value: Any, output_dir: Path, key: str = "") -> Any:
    normalized = key.casefold()
    if normalized in _CHECKPOINT_SECRET_KEYS:
        return "[redacted]"
    if normalized in _CHECKPOINT_PATH_KEYS and isinstance(value, str):
        return _checkpoint_local_path(value, output_dir)
    if normalized in _CHECKPOINT_URL_KEYS and isinstance(value, str):
        return (
            _checkpoint_tab_url(value) if value.startswith(("http://", "https://")) else "[omitted]"
        )
    if normalized == "attachments" and isinstance(value, list):
        return [
            _checkpoint_tab_url(item)
            if isinstance(item, str) and item.startswith(("http://", "https://"))
            else _checkpoint_local_path(item, output_dir)
            if isinstance(item, str)
            else "[redacted]"
            for item in value
        ]
    if isinstance(value, dict):
        return {
            _checkpoint_dict_key(str(item_key)): _checkpoint_value(item, output_dir, str(item_key))
            for item_key, item in value.items()
            if str(item_key).casefold() != "task"
        }
    if isinstance(value, (list, tuple)):
        return [_checkpoint_value(item, output_dir, key) for item in value]
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return _checkpoint_tab_url(value)
        if _checkpoint_safe_string(normalized, value):
            return value
        return "[omitted]"
    return value


def _checkpoint_dict_key(value: str) -> str:
    """Keep schema-like keys while hashing keys that can themselves carry secrets."""
    normalized = value.casefold()
    if (
        any(secret in normalized for secret in _CHECKPOINT_SECRET_KEYS)
        or "/" in value
        or "\\" in value
        or "://" in value
    ):
        return f"field_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"
    return value


def _checkpoint_safe_string(key: str, value: str) -> bool:
    """Allow only controller-generated scalar strings, never arbitrary page text."""
    if value in _CHECKPOINT_FIXED_VALUES or value in _CHECKPOINT_STRATEGY_VALUES:
        return True
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return True
    if key in {"timestamp", "date", "datetime"}:
        return re.fullmatch(r"[0-9TtZz:+.\- ]{4,40}", value) is not None
    if key in {
        "engine",
        "finish_reason",
        "loop_type",
        "policy",
        "requested_output_mode",
        "effective_output_mode",
        "source_tool",
        "tool",
        "tool_name",
        "type",
    }:
        return re.fullmatch(r"[A-Za-z0-9_.:\-]{1,100}", value) is not None
    if key in {"figure_number", "table_number"}:
        return re.fullmatch(r"[A-Za-z0-9_.:\-]{1,32}", value) is not None
    if key == "version_frontier":
        return re.fullmatch(r"[A-Za-z0-9_.+\-]{1,100}", value) is not None
    return False


def _checkpoint_local_path(value: str, output_dir: Path) -> str:
    raw = Path(value).expanduser()
    output_root = output_dir.resolve()
    possible = [raw] if raw.is_absolute() else [output_root / "artifacts" / raw, output_root / raw]
    path = next((item.resolve() for item in possible if item.exists()), None)
    if path is None or (path != output_root and not path.is_relative_to(output_root)):
        return "[redacted]"
    return path.relative_to(output_root).as_posix()


def _checkpoint_planner_attempt(attempt: PlannerAttempt) -> dict[str, Any]:
    value = attempt.model_dump(mode="json")
    if value.get("error") is not None:
        value["error"] = "planner attempt failed; details omitted"
    return value


def _checkpoint_planning_state(
    state: PlanningState | None, output_dir: Path
) -> PlanningState | None:
    if state is None:
        return None
    milestone_ids = {item.id: f"m{index}" for index, item in enumerate(state.milestones, 1)}
    return PlanningState.model_validate(
        {
            "objective": "task bound by checkpoint task_sha256",
            "milestones": [
                {
                    "id": milestone_ids[item.id],
                    "description": f"checkpoint milestone {index}",
                    "status": item.status,
                    "completed_at_step": item.completed_at_step,
                }
                for index, item in enumerate(state.milestones, 1)
            ],
            "active_milestone_id": (
                milestone_ids.get(state.active_milestone_id)
                if state.active_milestone_id is not None
                else None
            ),
            "evidence": [
                {
                    "id": f"e{index}",
                    "step_number": item.step_number,
                    "kind": item.kind if item.kind == "durable_note" else "checkpoint",
                    "summary": (
                        validate_durable_note(item.summary)
                        if item.kind == "durable_note"
                        else f"evidence retained from step {item.step_number}"
                    ),
                    "source": (
                        _checkpoint_tab_url(item.source)
                        if item.source and item.source.startswith(("http://", "https://"))
                        else _checkpoint_local_path(item.source, output_dir)
                        if item.source
                        else None
                    ),
                }
                for index, item in enumerate(state.evidence, 1)
            ],
            "revisions": [
                {
                    "revision": index,
                    "step_number": item.step_number,
                    "reason": "runtime strategy replan",
                    "strategy": (
                        item.strategy
                        if item.strategy in _CHECKPOINT_STRATEGY_VALUES
                        else "recovery"
                    ),
                    "added_milestone_ids": tuple(
                        milestone_ids[milestone_id]
                        for milestone_id in item.added_milestone_ids
                        if milestone_id in milestone_ids
                    ),
                }
                for index, item in enumerate(state.revisions, 1)
            ],
        }
    )


def _checkpoint_strategy_state(state: StrategyState) -> StrategyState:
    switches = tuple(
        item.model_copy(update={"reason": "runtime strategy switch"}) for item in state.switches
    )
    return state.model_copy(update={"switches": switches})


def _checkpoint_loop_state(state: dict[str, Any]) -> dict[str, Any]:
    safe = dict(state)
    for key in ("recent_actions", "recent_pages"):
        values = safe.get(key)
        safe[key] = (
            [hashlib.sha256(item.encode("utf-8")).hexdigest() for item in values]
            if isinstance(values, list) and all(isinstance(item, str) for item in values)
            else []
        )
    urls = safe.get("url_history")
    safe["url_history"] = (
        [_checkpoint_tab_url(item) for item in urls]
        if isinstance(urls, list) and all(isinstance(item, str) for item in urls)
        else []
    )
    return safe


def _checkpoint_artifacts(steps: list[AgentStep], output_dir: Path) -> tuple[ArtifactRecord, ...]:
    """Record only explicit tool-returned files; never crawl the output tree."""
    output_root = output_dir.resolve()
    artifacts_root = output_root / "artifacts"
    candidates: list[str] = []
    for step in steps:
        data = step.tool_result.data
        for key in ("path", "image_path"):
            value = data.get(key)
            if isinstance(value, str):
                candidates.append(value)
        attachments = data.get("attachments")
        if isinstance(attachments, list):
            candidates.extend(item for item in attachments if isinstance(item, str))

    records: list[ArtifactRecord] = []
    seen: set[Path] = set()
    for value in candidates:
        raw = Path(value).expanduser()
        possible = [raw] if raw.is_absolute() else [artifacts_root / raw, output_root / raw]
        path = next((item.resolve() for item in possible if item.is_file()), None)
        if path is None or path in seen:
            continue
        if path != output_root and not path.is_relative_to(output_root):
            continue
        relative = path.relative_to(output_root)
        if (
            relative.parts[0] == "screenshots"
            or relative.parts[:2]
            == (
                "observations",
                "screenshots",
            )
            or relative.as_posix()
            in {
                "artifacts/checkpoint.json",
                "artifacts/run.json",
                "control/checkpoints/latest.json",
                "trajectory/trace.json",
            }
        ):
            continue
        records.append(ArtifactRecord.from_path(path, root=output_root))
        seen.add(path)
    return tuple(records)


def _checkpoint_tab_url(value: Any) -> str:
    """Keep web coordinates while dropping local paths and common URL credentials."""
    if not isinstance(value, str):
        return "about:blank"
    if value == "about:blank":
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "about:blank"
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{parsed.hostname}{port}"
    # Query strings are free-form and routinely carry search text, reset
    # tokens, OAuth state, and signed URLs under provider-specific key names.
    # A non-secret checkpoint therefore retains only origin + path.
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
