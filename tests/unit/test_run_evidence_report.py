"""Immutable evidence, explicit missing states and an escaped offline reader."""

import asyncio
import hashlib
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from webagent.agent.context import planner_result_preview
from webagent.agent.evidence_store import record_event, save_result
from webagent.agent.observations import save_observation
from webagent.agent.report import _merged_lifecycle, report_verification, write_report
from webagent.browser.capture_failure import InconsistentCapture, failed_observation
from webagent.core.models import BrowserState
from webagent.evaluation.artifacts import RunLayout
from webagent.evaluation.evidence_integrity import artifact_failures
from webagent.planner.api import _planning_screenshot_needed
from webagent.tools.builtin.interaction_tools import GetAllLinksTool
from webagent.tools.builtin.search_tools import SearchTool


def _state():
    return BrowserState(
        screenshot=Image.new("RGB", (12, 12), "blue"),
        url="https://test/",
        title="Report",
        dom_summary="Qwen3.8",
        timestamp="now",
    )


def test_report_separates_strict_applicability_from_task_assertions(tmp_path):
    trace = {
        "task": "Features",
        "status": "completed",
        "steps": [],
        "evaluation": {"search_engine_only": False, "mode": "browser_grounded"},
    }
    assert report_verification(tmp_path, trace)["valid"] is None
    (tmp_path / "evaluation").mkdir()
    (tmp_path / "evaluation/task.json").write_text(json.dumps({"passed": False, "score": 0.5}))
    content = write_report(tmp_path, trace).read_text()
    assert "not applicable" in content
    assert "Independent task assertions" in content
    assert "0.5" in content
    assert "evaluation/task.json" in content
    trace["evaluation"]["search_engine_only"] = True
    assert report_verification(tmp_path, trace)["valid"] is False


def test_report_handles_unreadable_task_assertions(tmp_path):
    (tmp_path / "evaluation").mkdir()
    (tmp_path / "evaluation/task.json").write_text("{")
    content = write_report(tmp_path, {"task": "T", "status": "failed", "steps": []}).read_text()
    assert "Unreadable task judgment" in content


def test_full_result_is_not_the_truncated_preview_and_redacts_credentials(tmp_path):
    data = {
        "links": [{"href": f"https://test/{i}", "text": "a" * 250} for i in range(100)],
        "returned": 100,
        "token": "secret",
        "bytes": b"raw",
    }
    reference = save_result(tmp_path, data)
    payload = (tmp_path / reference["path"]).read_bytes()
    full = json.loads(payload)
    assert len(full["links"]) == 100 and full["token"] == "[redacted]"
    assert hashlib.sha256(payload).hexdigest() == reference["sha256"]
    assert save_result(tmp_path, data) == reference
    visible = json.loads(planner_result_preview("get_all_links", data, success=True))
    assert len(visible["links"]) < 20
    assert visible["visible_count"] == visible["next_offset"] == len(visible["links"])
    assert visible["omitted_count"] == 100 - len(visible["links"])
    assert data["returned"] == 100


def test_oversized_scalar_preview_remains_valid_json():
    value = json.loads(planner_result_preview("unknown", {"content": "x" * 10000}, success=True))
    assert value["preview_omitted"] == {"content": 1}


def test_runtime_and_action_events_are_merged_in_timestamp_order():
    action = {"type": "action_finished", "timestamp": "2026-09-08T01:00:00Z"}
    challenge = {"type": "captcha_detected", "timestamp": "2026-09-08T01:01:00Z"}
    assert _merged_lifecycle(
        json.dumps(action) + '\n{"partial":', {"events": [challenge, action]}
    ) == [action, challenge]


def test_finished_action_without_post_is_not_mislabeled_unexecuted(tmp_path):
    record_event(tmp_path, "observed", 1, observation_path="observations/step_001/pre.json")
    record_event(tmp_path, "action_started", 1, tool="goto")
    record_event(tmp_path, "action_finished", 1, tool="goto", success=True)
    trace = {"task": "Interrupted after action", "status": "interrupted", "steps": []}
    content = write_report(tmp_path, trace).read_text()
    assert "goto / lifecycle only" in content
    assert "planning / no executed action" not in content


async def test_repeated_searches_report_zero_information_gain():
    browser = SimpleNamespace(
        page=SimpleNamespace(url="https://bing.com/search", title=AsyncMock(return_value="Results"))
    )
    tool = SearchTool(browser=browser)
    items = [{"title": "Report", "url": "https://publisher.test/report"}]
    first = await tool._search_result_data("query one", "bing", items)
    second = await tool._search_result_data("query two", "bing", items)
    assert first["novel_result_count"] == 1 and not first["repeated_result_set"]
    assert second["novel_result_count"] == 0 and second["repeated_result_set"]
    assert "recovery_hint" in json.loads(planner_result_preview("search", second, success=True))


async def test_filtered_links_can_page_past_first_twenty():
    links = [{"href": f"https://test/{i}", "text": f"Report {i}"} for i in range(40)]
    browser = SimpleNamespace(
        page=SimpleNamespace(url="https://test/"),
        get_all_links=AsyncMock(return_value={"success": True, "links": links, "total_count": 40}),
    )
    tool = GetAllLinksTool(browser=browser)
    result = await tool.execute({"contains": "report", "offset": 20, "max_results": 20})
    assert result.data["links"][0]["href"] == "https://test/20"
    assert result.data["next_offset"] is None
    assert browser.get_all_links.call_args.kwargs["max_results"] is None
    for params in ({"offset": -1}, {"contains": 3}):
        with pytest.raises(ValueError):
            tool.validate_params(params)


async def test_partial_capture_retains_pixels_without_claiming_paired_dom():
    output = BytesIO()
    _state().screenshot.save(output, "PNG")
    page = SimpleNamespace(url="https://new/", screenshot=AsyncMock(return_value=output.getvalue()))
    state = await failed_observation(page, [], None)
    assert state.screenshot is not None and state.observation_metadata["status"] == "partial"
    partial = InconsistentCapture("changed", output.getvalue(), "https://old/")
    state = await failed_observation(page, [], partial)
    assert state.url == "https://old/" and not state.observation_metadata["pair_consistent"]
    page.screenshot.side_effect = RuntimeError("closed")
    state = await failed_observation(page, [], None)
    assert state.screenshot is None and state.observation_metadata["status"] == "failed"


async def test_fallback_screenshot_has_its_own_deadline():
    async def never_finishes(**kwargs):
        await asyncio.Event().wait()

    page = SimpleNamespace(url="https://test/", screenshot=never_finishes)
    state = await failed_observation(page, [], None, timeout_seconds=0.01)
    assert state.observation_metadata["capture_attempts"][-1]["error_type"] == "TimeoutError"


def test_structured_visual_recovery_does_not_depend_on_hint_wording():
    state = _state().model_copy(update={"requires_visual": True})
    assert _planning_screenshot_needed(state, "Use the visual layout to recover")


def test_deduplicated_observations_and_artifact_tampering(tmp_path):
    layout = RunLayout.from_root(tmp_path)
    pre = save_observation(_state(), layout, 1, "pre")
    post = save_observation(_state(), layout, 1, "post")
    reference = save_result(tmp_path, {"success": True})
    trace = {
        "steps": [
            {"step_number": 1, "result_ref": reference, "observations": {"pre": pre, "post": post}}
        ]
    }
    assert len(list((tmp_path / "blobs").glob("*.png"))) == 1
    assert artifact_failures(tmp_path, trace) == ([], 3)
    (tmp_path / reference["path"]).write_text("tampered")
    assert "SHA-256" in artifact_failures(tmp_path, trace)[0][0]


def test_report_escapes_untrusted_content_and_explains_missing_post(tmp_path):
    pre = save_observation(_state(), RunLayout.from_root(tmp_path), 1, "pre")
    record_event(tmp_path, "action_started", 1, token="secret")
    trace = {
        "task": "<script>alert(1)</script>",
        "status": "failed",
        "steps": [
            {
                "step_number": 1,
                "tool": "goto",
                "success": False,
                "observations": {"pre": pre, "post": "../../secret"},
            }
        ],
    }
    page = write_report(tmp_path, trace).read_text()
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "not independently evaluated" in page
    assert "Not recorded / action not attempted" in page
    assert '"token": "secret"' not in (tmp_path / "trajectory/events.jsonl").read_text()


def test_report_links_raw_evidence_without_repeating_large_payloads(tmp_path):
    state = _state().model_copy(
        update={
            "dom_summary": "planner context",
            "viewport_context": "planner context",
            "document_context": "supplement",
            "elements": [{"text": "raw element payload " + "x" * 2000}],
        }
    )
    pre = save_observation(state, RunLayout.from_root(tmp_path), 1, "pre")
    (tmp_path / "evaluation").mkdir()
    (tmp_path / "evaluation/task.json").write_text(
        json.dumps(
            {
                "passed": True,
                "score": 1.0,
                "assertions": [
                    {
                        "assertion": {"kind": "answer_contains", "expected": "fact"},
                        "passed": True,
                        "observed": "repeated final answer " + "y" * 2000,
                    }
                ],
            }
        )
    )
    trace = {
        "task": "Compact reader",
        "status": "completed",
        "steps": [
            {
                "step_number": 1,
                "tool": "done",
                "success": True,
                "observations": {"pre": pre, "post": pre},
            }
        ],
    }

    page = write_report(tmp_path, trace).read_text()

    assert "Open complete observation JSON" in page
    assert "planner context" in page and "supplement" in page
    assert "raw element payload" not in page
    assert "repeated final answer" not in page


def test_report_includes_observed_step_interrupted_during_first_planner_attempt(tmp_path):
    pre = save_observation(_state(), RunLayout.from_root(tmp_path), 1, "pre")
    record_event(tmp_path, "observed", 1, observation_path=pre)
    trace = {
        "task": "Interrupted task",
        "status": "interrupted",
        "steps": [],
        "planner_attempts": [],
    }
    page = write_report(tmp_path, trace).read_text()
    assert 'id="step-1"' in page and "no executed action" in page
