"""Replay candidate dates and verify persisted evidence independently of success flags."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from webagent.tools.policies.candidates import CandidateLedger
from webagent.tools.policies.evidence import _distinctive_task_keywords


def has_completed_figure(steps: list[dict[str, Any]]) -> bool:
    """A found flag, caption lookup, or truncated response is not completed analysis."""
    for step in steps:
        if step.get("tool") != "pdf_analyze_figure" or step.get("success") is not True:
            continue
        try:
            data = json.loads(step.get("planner_visible_result", "{}"))
        except (ValueError, TypeError):
            continue
        if (
            isinstance(data, dict)
            and data.get("found") is True
            and isinstance(data.get("vision_analysis"), str)
            and data["vision_analysis"].strip()
            and data.get("vision_metadata", {}).get("finish_reason") != "length"
        ):
            return True
    return False


def candidate_failures(
    task: str, steps: list[dict[str, Any]], root: Path | None = None
) -> list[str]:
    """Recompute date/version comparisons from planner-visible tool evidence."""
    if "pdf" not in task.casefold():
        return []
    ledger = CandidateLedger()
    keywords = _distinctive_task_keywords(task)
    target = None
    for step in steps:
        target = step.get("policy", {}).get("selected_candidate_url") or target
        if step.get("tool") == "search":
            ledger.query_attempt(str(step.get("parameters", {}).get("query", "")))
        if step.get("success") is not True:
            continue
        try:
            data = json.loads(step.get("planner_visible_result", "{}"))
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        _replay_candidate(ledger, step, data, keywords)
    assessment = ledger.assessment(target, keywords)
    if root is not None:
        _replay_observations(ledger, steps, root, keywords)
        assessment = ledger.assessment(target, keywords)
    return [f"latest candidate evidence: {item}" for item in assessment["missing"]]


def _replay_observations(
    ledger: CandidateLedger, steps: list[dict[str, Any]], root: Path, keywords: set[str]
) -> None:
    checked_urls = {
        lead.get("checked_url")
        for step in steps
        for lead in step.get("policy", {}).get("candidate_ledger", {}).get("leads", [])
        if lead.get("status") == "official_checked"
    }
    paths = {value for step in steps for value in step.get("observations", {}).values()}
    for value in sorted(paths):
        try:
            _check_observation(root, value)
            observation = json.loads(_evidence_path(root, value).read_text())
        except (OSError, ValueError, TypeError, AttributeError):
            continue  # Artifact integrity separately reports this failure.
        if observation.get("metadata", {}).get("status") not in {"complete", "reused"}:
            continue
        ledger.page(
            observation["url"],
            observation.get("viewport_context") or "",
            official=observation["url"] in checked_urls,
            keywords=keywords,
        )


def _replay_candidate(
    ledger: CandidateLedger, step: dict[str, Any], data: dict[str, Any], keywords: set[str]
) -> None:
    tool = step.get("tool")
    if tool == "search":
        ledger.search(str(step.get("parameters", {}).get("query", "")), data, keywords)
    if tool in {"goto", "open_tab"}:
        ledger.page(
            str(data.get("url", "")), str(data.get("title", "")), official=True, keywords=keywords
        )
    if tool == "inspect_download_links":
        # This check proves date binding/comparison, not the publisher's identity.
        # Identity and URL provenance are checked by the trajectory contract.
        ledger.inspect(
            data,
            official=step.get("policy", {}).get("selected_candidate_identity_endorsed") is True,
        )


def _evidence_path(root: Path, value: Any) -> Path:
    if not isinstance(value, str):
        raise ValueError("missing evidence path")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("evidence path missing or outside run directory")
    return path


def _check_reference(root: Path, reference: dict[str, Any]) -> None:
    path = _evidence_path(root, reference.get("path"))
    if hashlib.sha256(path.read_bytes()).hexdigest() != reference.get("sha256"):
        raise ValueError("evidence SHA-256 mismatch")


def _check_observation(root: Path, value: str) -> None:
    observation = json.loads(_evidence_path(root, value).read_text())
    expected = observation.get("dom_sha256")
    actual = hashlib.sha256(observation.get("dom_summary", "").encode()).hexdigest()
    if expected != actual:
        raise ValueError("observation DOM SHA-256 mismatch")
    for key in ("screenshot", "full_page_screenshot"):
        if reference := observation.get(key):
            _check_reference(root, reference)


def artifact_failures(root: Path, trace: dict[str, Any]) -> tuple[list[str], int]:
    failures: list[str] = []
    checked = 0
    for step in trace.get("steps", []):
        try:
            if reference := step.get("result_ref"):
                _check_reference(root, reference)
                checked += 1
            for value in step.get("observations", {}).values():
                _check_observation(root, value)
                checked += 1
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            failures.append(f"step {step.get('step_number')} artifact integrity: {exc}")
    return failures, checked
