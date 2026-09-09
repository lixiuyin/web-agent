"""Final-output, figure, screenshot, and run-trace persistence.

Everything the agent durably records beyond its checkpoint lives here: per-step
screenshots, figure selection/attachment backfill, result and per-turn snapshots,
and the auditable ``trajectory/trace.json`` with its redaction rules.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from contextlib import suppress
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from webagent.agent.checkpoint import CHECKPOINT_SCHEMA_VERSION
from webagent.agent.context import planner_context, planner_result_preview
from webagent.agent.evidence_store import save_result
from webagent.agent.observations import observation_references
from webagent.core.config import AgentConfig
from webagent.core.models import AgentResult, BrowserState, ToolCall
from webagent.evaluation.artifacts import RunLayout
from webagent.evaluation.trace_schema import build_run_trace_v8
from webagent.tools.risk import assess_tool_call
from webagent.utils.runtime import package_source_fingerprint

logger = logging.getLogger("webagent")


class TracePersistenceError(RuntimeError):
    """Raised when a strict run cannot persist its auditable trace and certificate."""


def _refresh_catalogs(root: Path) -> None:
    from webagent.agent.catalog import refresh_parent_catalogs

    try:
        refresh_parent_catalogs(root)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Run evidence saved, but catalog refresh failed: %s", exc)


def _save_step_screenshot(
    browser_state: BrowserState,
    screenshot_path: Path,
) -> None:
    if browser_state.screenshot is None:
        return
    encoded = BytesIO()
    browser_state.screenshot.save(encoded, format="JPEG")
    payload = encoded.getvalue()
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    match = re.fullmatch(r"step_(\d+)\.jpg", screenshot_path.name)
    previous = (
        screenshot_path.with_name(f"step_{int(match.group(1)) - 1:03d}.jpg")
        if match is not None and int(match.group(1)) > 1
        else None
    )
    if previous is not None and previous.is_file():
        try:
            if previous.read_bytes() == payload:
                os.link(previous, screenshot_path)
                return
        except OSError:
            pass
    # Replacing a legacy preview during recovery must not alter another step
    # that shares its inode through frame deduplication.
    temporary = screenshot_path.with_suffix(".jpg.tmp")
    temporary.write_bytes(payload)
    temporary.replace(screenshot_path)


# Tools whose successful result identifies an image the agent focused on; the
# most recent one is the "found figure" persisted when the task completes.
# pdf_analyze_figure resolves a numbered figure and returns it under image_path.
_FIGURE_TOOLS = ("pdf_analyze_figure", "analyze_image", "read_image", "save_image")
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def _as_image_path(candidate: Any, artifacts_dir: Path) -> Path | None:
    """Resolve ``candidate`` to an existing image file contained in the output root.

    Attachments are partly LLM-controlled, so the resolved path is confined to
    the output root to avoid copying an arbitrary file out of the workspace.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    path = Path(candidate.strip())
    if not path.is_absolute():
        path = artifacts_dir / path
    path = path.resolve()
    output_root = artifacts_dir.resolve().parent
    if path != output_root and not path.is_relative_to(output_root):
        return None
    if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
        return path
    return None


def _select_figure(
    attachments: Any, last_figure_path: str | None, artifacts_dir: Path
) -> Path | None:
    """Pick the figure to persist: first image attachment, else the last one seen."""
    if isinstance(attachments, list):
        for attachment in attachments:
            found = _as_image_path(attachment, artifacts_dir)
            if found:
                return found
    return _as_image_path(last_figure_path, artifacts_dir)


def _attach_figure(
    final_result: dict[str, Any],
    figure: Path | None,
    *,
    source_figure: Path | None = None,
    artifacts_dir: Path | None = None,
) -> None:
    """Ensure the found figure is listed in the result's ``attachments``.

    The ``done`` tool's attachments are model-controlled and are often omitted
    even when a figure was analyzed. Backfilling keeps the reported result
    complete without depending on the model remembering to attach the image.
    """
    if figure is None:
        return
    attachments = final_result.get("attachments")
    if not isinstance(attachments, list):
        attachments = []
    if source_figure is not None and artifacts_dir is not None:
        source = source_figure.resolve()
        attachments = [
            attachment
            for attachment in attachments
            if _as_image_path(attachment, artifacts_dir) != source
        ]
    figure_str = str(figure)
    if figure_str not in attachments:
        attachments.append(figure_str)
    final_result["attachments"] = attachments


def _persist_final_outputs(
    output_dir: Path,
    summary: str,
    figure: Path | None,
    *,
    turn_index: int | None = None,
) -> Path | None:
    """Write the final answer and copy the selected figure attachment.

    Best-effort: failures are logged, never raised, so persistence cannot crash a
    task that has otherwise completed successfully.
    """
    layout = RunLayout.from_root(output_dir)
    figure_bytes: bytes | None = None
    figure_name: str | None = None
    if figure is not None:
        try:
            figure_bytes = figure.read_bytes()
            figure_name = f"figure{figure.suffix.lower()}"
        except OSError as exc:
            logger.warning("Failed to read figure %s: %s", figure, exc)
    try:
        layout.result_dir.mkdir(parents=True, exist_ok=True)
        layout.summary_path.write_text(summary or "", encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write result/summary.txt: %s", exc)

    dest: Path | None = None
    try:
        if layout.attachments_dir.exists():
            shutil.rmtree(layout.attachments_dir)
        layout.attachments_dir.mkdir(parents=True, exist_ok=True)
        if figure_bytes is not None and figure_name is not None:
            dest = layout.attachments_dir / figure_name
            _link_or_write_attachment(figure, figure_bytes, dest)
            logger.info("Saved found figure to %s", dest)
    except OSError as exc:
        logger.warning("Failed to refresh result attachments: %s", exc)
        dest = None

    if turn_index is not None:
        _persist_turn_result_snapshot(
            layout,
            turn_index=turn_index,
            summary=summary,
            figure_name=figure_name,
            figure_bytes=figure_bytes,
            figure_source=dest,
        )
    return dest


def _persist_turn_result_snapshot(
    layout: RunLayout,
    *,
    turn_index: int,
    summary: str,
    figure_name: str | None,
    figure_bytes: bytes | None,
    figure_source: Path | None = None,
) -> None:
    """Publish one result turn atomically and never replace prior evidence."""
    target = layout.turn_result_dir(turn_index)
    if target.exists():
        logger.warning("Refusing to overwrite existing result turn snapshot %s", target)
        return
    layout.result_turns_dir.mkdir(parents=True, exist_ok=True)
    staging = layout.result_turns_dir / f".{target.name}-{uuid4().hex}.tmp"
    try:
        (staging / "attachments").mkdir(parents=True)
        (staging / "summary.txt").write_text(summary or "", encoding="utf-8")
        if figure_name is not None and figure_bytes is not None:
            _link_or_write_attachment(
                figure_source,
                figure_bytes,
                staging / "attachments" / figure_name,
            )
        staging.replace(target)
    except OSError as exc:
        logger.warning("Failed to persist result turn snapshot %s: %s", target, exc)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _ensure_turn_result_snapshot(
    layout: RunLayout,
    *,
    turn_index: int,
    final_result: dict[str, Any],
    last_figure_path: str | None,
) -> None:
    """Ensure interrupted/failed turns also retain a non-overwriting result record."""
    if layout.turn_result_dir(turn_index).exists():
        return
    figure = _select_figure(
        final_result.get("attachments"),
        last_figure_path,
        layout.artifacts_dir,
    )
    figure_bytes: bytes | None = None
    figure_name: str | None = None
    if figure is not None:
        try:
            figure_bytes = figure.read_bytes()
            figure_name = f"figure{figure.suffix.lower()}"
        except OSError as exc:
            logger.warning("Failed to read turn figure %s: %s", figure, exc)
    summary = final_result.get("summary", "")
    _persist_turn_result_snapshot(
        layout,
        turn_index=turn_index,
        summary=summary if isinstance(summary, str) else str(summary),
        figure_name=figure_name,
        figure_bytes=figure_bytes,
        figure_source=figure,
    )


def _link_or_write_attachment(source: Path | None, payload: bytes, target: Path) -> None:
    """Preserve a self-contained result path without duplicating immutable bytes."""
    if source is not None and source.is_file():
        try:
            os.link(source, target)
            return
        except OSError:
            pass
    target.write_bytes(payload)


def _persist_run_trace(
    output_dir: Path,
    task: str,
    result: AgentResult,
    config: AgentConfig,
    *,
    run_id: str | None = None,
    resume_count: int = 0,
    resumed: bool = False,
    turn_index: int | None = None,
    turn_start_step: int = 1,
    planner_attempt_start: int = 0,
    event_start: int = 0,
) -> None:
    """Persist an auditable, screenshot-free execution trace."""
    run_id = run_id or str(uuid4())
    layout = RunLayout.from_root(output_dir)
    trace_payload = _build_trace_payload(
        layout,
        task,
        result,
        config,
        run_id=run_id,
        resume_count=resume_count,
        resumed=resumed,
        turn_start_step=turn_start_step,
        planner_attempt_start=planner_attempt_start,
        event_start=event_start,
    )
    _write_trace(
        layout,
        trace_payload,
        config,
        turn_index=turn_index,
    )


def _build_trace_payload(
    layout: RunLayout,
    task: str,
    result: AgentResult,
    config: AgentConfig,
    *,
    run_id: str,
    resume_count: int,
    resumed: bool,
    turn_start_step: int,
    planner_attempt_start: int,
    event_start: int,
) -> dict[str, Any]:
    anti_shortcut_configured = bool(
        config.strict_eval_mode
        and config.search_engine_only
        and config.browser_profile_mode == "temporary"
        and config.browser_channel == "bundled"
        and not config.persistent_pdf_cache
    )
    return cast(
        dict[str, Any],
        _portable_run_paths(
            {
                "run_id": run_id,
                "run_kind": "agent_e2e",
                "resume_count": resume_count,
                "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION if resumed else None,
                "resumed_from_checkpoint": resumed,
                "task": task,
                "status": result.status,
                "success": result.success,
                "steps_taken": sum(step.step_number >= turn_start_step for step in result.history),
                "total_duration": result.total_duration,
                "final_result": _portable_trace_final_result(result.final_result, layout),
                "evaluation": {
                    "agent_source_sha256": package_source_fingerprint(),
                    "mode": (
                        "search_engine_only"
                        if config.search_engine_only
                        else (
                            "hybrid_api_augmented"
                            if config.discovery_mode == "hybrid"
                            else "browser_grounded"
                        )
                    ),
                    "discovery_mode": config.discovery_mode,
                    "direct_source_tools_enabled": config.discovery_mode == "hybrid",
                    "high_risk_action_policy": config.high_risk_action_policy,
                    "stealth_mode": config.stealth_mode,
                    "anti_shortcut_contract": (
                        "search_engine_only_v8" if anti_shortcut_configured else None
                    ),
                    "certificate_required": config.strict_eval_mode,
                    "strict_eval_mode": config.strict_eval_mode,
                    "search_engine_only": config.search_engine_only,
                    "browser_profile_mode": config.browser_profile_mode,
                    "browser_channel": config.browser_channel,
                    "persistent_pdf_cache": config.persistent_pdf_cache,
                },
                "planner_attempts": [
                    attempt.model_dump(mode="json")
                    for attempt in result.planner_attempts[planner_attempt_start:]
                ],
                "events": _trace_value(result.events[event_start:]),
                "steps": [
                    {
                        "step_number": step.step_number,
                        "run_id": run_id,
                        "timestamp": step.timestamp,
                        "tool": step.tool_call.tool_name,
                        "parameters": _trace_parameters(step.tool_call),
                        "reasoning": step.tool_call.reasoning,
                        "success": step.tool_result.success,
                        "error": step.tool_result.error,
                        "result": _trace_value(
                            planner_context(step.tool_call.tool_name, step.tool_result.data)
                        ),
                        "result_ref": save_result(
                            layout.root, _portable_full_result(step.tool_result.data, layout)
                        ),
                        "planner_visible_result": planner_result_preview(
                            step.tool_call.tool_name,
                            _portable_run_paths(step.tool_result.data, layout),
                            success=step.tool_result.success,
                        ),
                        "policy": _trace_value(step.tool_result.audit),
                        "duration_seconds": step.duration_seconds,
                        "tool_duration_seconds": step.tool_duration_seconds,
                        "observations": observation_references(
                            layout, step.step_number, step.browser_state.observation_path
                        ),
                    }
                    for step in result.history
                    if step.step_number >= turn_start_step
                ],
            },
            layout,
        ),
    )


def _write_trace(
    layout: RunLayout,
    trace_payload: dict[str, Any],
    config: AgentConfig,
    *,
    turn_index: int | None,
) -> None:
    path = layout.trace_path
    temporary = path.with_suffix(".json.tmp")
    turn_temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        trace = build_run_trace_v8(trace_payload)
        encoded_trace = json.dumps(trace, ensure_ascii=False, indent=2).encode("utf-8")
        temporary.write_bytes(encoded_trace)
        temporary.replace(path)
        from webagent.agent.report import write_report

        if config.strict_eval_mode:
            from webagent.evaluation.trace_verifier import write_verification_certificate

            write_verification_certificate(path)
        write_report(layout.root, trace)
        _refresh_catalogs(layout.root)
        if turn_index is not None:
            turn_path = layout.turn_trace_path(turn_index)
            if turn_path.exists():
                raise FileExistsError(f"turn trace snapshot already exists: {turn_path}")
            turn_path.parent.mkdir(parents=True, exist_ok=True)
            turn_trace = _trace_for_turn_snapshot(trace, layout, turn_index)
            encoded_turn = json.dumps(turn_trace, ensure_ascii=False, indent=2).encode("utf-8")
            if encoded_turn == encoded_trace:
                os.link(path, turn_path)
            else:
                turn_temporary = turn_path.with_name(f".{turn_path.name}-{uuid4().hex}.tmp")
                turn_temporary.write_bytes(encoded_turn)
                turn_temporary.replace(turn_path)
    except (OSError, TypeError, ValueError) as exc:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        if turn_temporary is not None:
            with suppress(OSError):
                turn_temporary.unlink(missing_ok=True)
        logger.warning("Failed to write trajectory/trace.json: %s", exc)
        if config.strict_eval_mode:
            raise TracePersistenceError(
                "strict evaluation failed to persist trace.json and verification.json"
            ) from exc


def _trace_for_turn_snapshot(
    trace: dict[str, Any], layout: RunLayout, turn_index: int
) -> dict[str, Any]:
    """Bind historical attachments to their immutable result-turn copies."""
    snapshot = deepcopy(trace)
    final_result = snapshot.get("final_result")
    if not isinstance(final_result, dict):
        return snapshot
    attachments = final_result.get("attachments")
    if not isinstance(attachments, list):
        return snapshot
    canonical_root = layout.attachments_dir.resolve()
    immutable_root = layout.turn_attachments_dir(turn_index)
    rewritten: list[Any] = []
    for attachment in attachments:
        if not isinstance(attachment, str):
            rewritten.append(attachment)
            continue
        if attachment.startswith(("http://", "https://")):
            rewritten.append(attachment)
            continue
        candidate = Path(attachment).expanduser()
        path = (candidate if candidate.is_absolute() else layout.root / candidate).resolve()
        if path.parent == canonical_root:
            immutable = immutable_root / path.name
            if immutable.is_file():
                rewritten.append(immutable.relative_to(layout.root).as_posix())
                continue
        rewritten.append(attachment)
    final_result["attachments"] = rewritten
    return snapshot


def _portable_trace_final_result(value: Any, layout: RunLayout) -> Any:
    """Redact the final result and store run-contained attachments as relative paths."""
    portable = _trace_value(value)
    if not isinstance(portable, dict):
        return portable
    attachments = portable.get("attachments")
    if not isinstance(attachments, list):
        return portable
    rewritten: list[Any] = []
    for attachment in attachments:
        if not isinstance(attachment, str) or attachment.startswith(("http://", "https://")):
            rewritten.append(attachment)
            continue
        candidate = Path(attachment).expanduser()
        resolved = (candidate if candidate.is_absolute() else layout.root / candidate).resolve()
        if resolved == layout.root or resolved.is_relative_to(layout.root):
            rewritten.append(resolved.relative_to(layout.root).as_posix())
        else:
            rewritten.append(attachment)
    portable["attachments"] = rewritten
    return portable


def _portable_run_paths(value: Any, layout: RunLayout) -> Any:
    """Rewrite absolute paths contained by a run so frozen evidence can be moved."""
    if isinstance(value, dict):
        return {str(key): _portable_run_paths(item, layout) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable_run_paths(item, layout) for item in value]
    if not isinstance(value, str) or not Path(value).is_absolute():
        return value
    try:
        resolved = Path(value).expanduser().resolve()
        if resolved == layout.root or resolved.is_relative_to(layout.root):
            return resolved.relative_to(layout.root).as_posix()
    except OSError:
        return value
    return value


def _trace_value(value: Any, key: str = "") -> Any:
    """Bound trace size and redact common secret/binary fields."""
    if key.casefold() in {"api_key", "token", "password", "base64", "data_url", "image"}:
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _trace_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_trace_value(item) for item in value[:50]]
    if isinstance(value, str) and len(value) > 5000:
        return value[:5000] + "...[truncated]"
    return value


def _portable_full_result(value: Any, layout: RunLayout) -> Any:
    from webagent.agent.evidence_store import redact

    # Full evidence is not passed through the bounded trace preview serializer.
    return redact(value)


def _trace_parameters(tool_call: ToolCall) -> dict[str, Any]:
    """Redact semantically sensitive values even when their key is merely ``text``."""
    parameters = _trace_value(tool_call.parameters)
    if not isinstance(parameters, dict):
        return {}
    assessment = assess_tool_call(tool_call)
    if assessment.external_effect == "sensitive_input" and "text" in parameters:
        parameters["text"] = "[redacted]"
    if assessment.external_effect == "local_file_disclosure" and "path" in parameters:
        parameters["path"] = "[redacted]"
    return parameters
