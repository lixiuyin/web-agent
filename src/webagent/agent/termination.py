"""Evidence-based failure handoffs, without inventing an answer or a success."""

from typing import Any


def unfinished_result(
    status: str, policy: dict[str, Any], events: list[dict[str, Any]], error: str | None = None
) -> dict[str, Any]:
    challenge = next(
        (event for event in reversed(events) if event.get("type") == "captcha_detected"), None
    )
    reason = str(challenge.get("reason")) if challenge else (error or status)
    candidate = policy.get("selected_candidate_url")
    gaps = policy.get("latest_missing_prerequisites", [])
    lines = [f"Task not completed ({status}).", f"Stop reason: {reason}."]
    if candidate:
        lines.append(f"Observed report candidate (not a verified latest report): {candidate}")
    downloaded = policy.get("downloaded_artifact_count", 0)
    analyzed = policy.get("figure_analysis_completed", False)
    lines.append(f"Recorded PDF downloads: {downloaded}; figure analysis completed: {analyzed}.")
    if gaps:
        lines.append("Remaining evidence: " + "; ".join(gaps))
    return {
        "summary": "\n".join(lines),
        "completion_status": "incomplete",
        "stop_reason": reason,
        "observed_candidate": candidate,
        "evidence_gaps": gaps,
        "challenge": challenge,
    }
