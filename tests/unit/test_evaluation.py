"""Tests for environment-grounded web benchmark evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from webagent.core.models import (
    AgentResult,
    AgentStep,
    BrowserState,
    ToolCall,
    ToolResult,
)
from webagent.evaluation.artifacts import RunLayout
from webagent.evaluation.evaluator import TerminalStateEvaluator, _json_path
from webagent.evaluation.models import (
    AssertionOutcome,
    BenchmarkAssertion,
    BenchmarkTask,
    TaskEvaluation,
)
from webagent.evaluation.runner import BenchmarkRunner, aggregate_evaluations


class _Locator:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    @property
    def first(self) -> _Locator:
        return self

    async def inner_text(self) -> str:
        return str(self._values.get("text", ""))

    async def text_content(self) -> str | None:
        return self._values.get("text")

    async def input_value(self) -> str:
        return str(self._values.get("value", ""))

    async def is_checked(self) -> bool:
        return bool(self._values.get("checked", False))

    async def is_visible(self) -> bool:
        if self._values.get("raise"):
            raise RuntimeError("locator failed")
        return bool(self._values.get("visible", False))

    async def get_attribute(self, name: str) -> str | None:
        return self._values.get("attributes", {}).get(name)


class _Page:
    url = "http://example.test/final?ok=1"

    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {
            "body": {"text": "Everything is ready"},
            "#text": {"text": "Exact text"},
            "#input": {"value": "Ada"},
            "#check": {"checked": True},
            "#visible": {"visible": True},
            "#attr": {"attributes": {"data-state": "saved"}},
            "#broken": {"raise": True},
        }

    def locator(self, selector: str) -> _Locator:
        return _Locator(self.values.get(selector, {}))


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"profile": {"name": "Ada"}, "items": [{"count": 2}]}


class _Client:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str) -> _Response:
        return _Response()


def _task(*assertions: BenchmarkAssertion) -> BenchmarkTask:
    return BenchmarkTask(
        id="test_task",
        category="test",
        goal="Reach the independently verified terminal state",
        start_url="http://example.test/start",
        assertions=list(assertions),
    )


def _agent_result(
    *,
    success: bool = True,
    action_success: bool = True,
    summary: str = "",
    success_probability: float | None = None,
) -> AgentResult:
    state = BrowserState(dom_summary="page", url=_Page.url, title="Test", timestamp="now")
    history = [
        AgentStep(
            step_number=1,
            timestamp="now",
            browser_state=state,
            tool_call=ToolCall(tool_name="click"),
            tool_result=ToolResult(success=action_success, tool_name="click"),
            duration_seconds=0.1,
        ),
        AgentStep(
            step_number=2,
            timestamp="now",
            browser_state=state,
            tool_call=ToolCall(tool_name="done"),
            tool_result=ToolResult(success=True, tool_name="done"),
            duration_seconds=0.1,
        ),
    ]
    final_result: dict[str, Any] = {"summary": summary}
    if success_probability is not None:
        final_result["success_probability"] = success_probability
    return AgentResult(
        success=success,
        status="completed" if success else "failed",
        steps_taken=2,
        total_duration=0.2,
        final_result=final_result,
        history=history,
    )


def test_assertion_schema_rejects_missing_kind_specific_fields() -> None:
    with pytest.raises(ValidationError, match="requires selector"):
        BenchmarkAssertion(kind="element_visible", expected=True)
    with pytest.raises(ValidationError, match="selector and attribute"):
        BenchmarkAssertion(kind="attribute_equals", selector="#x", expected="y")
    with pytest.raises(ValidationError, match="endpoint and json_path"):
        BenchmarkAssertion(kind="json_equals", endpoint="/state", expected=1)
    with pytest.raises(ValidationError, match="non-empty tool-name list"):
        BenchmarkAssertion(kind="history_tool_sequence", expected=[])
    with pytest.raises(ValidationError, match="non-empty string list"):
        BenchmarkAssertion(kind="history_url_observed_any", expected=[])
    with pytest.raises(ValidationError, match="below the task artifacts"):
        BenchmarkAssertion(kind="artifact_exists", expected="../secret")


def test_json_path_supports_mapping_and_list_indices() -> None:
    assert _json_path({"items": [{"count": 2}]}, "items.0.count") == 2
    with pytest.raises(KeyError):
        _json_path({"value": 1}, "value.missing")


async def test_evaluator_checks_all_browser_and_server_assertions(monkeypatch) -> None:
    monkeypatch.setattr("webagent.evaluation.evaluator.httpx.AsyncClient", _Client)
    task = _task(
        BenchmarkAssertion(kind="url_equals", expected=_Page.url),
        BenchmarkAssertion(kind="url_contains", expected="/final"),
        BenchmarkAssertion(kind="text_contains", expected="ready"),
        BenchmarkAssertion(kind="element_text_equals", selector="#text", expected="Exact text"),
        BenchmarkAssertion(kind="element_value_equals", selector="#input", expected="Ada"),
        BenchmarkAssertion(kind="element_checked", selector="#check", expected=True),
        BenchmarkAssertion(kind="element_visible", selector="#visible", expected=True),
        BenchmarkAssertion(
            kind="attribute_equals",
            selector="#attr",
            attribute="data-state",
            expected="saved",
        ),
        BenchmarkAssertion(
            kind="json_equals",
            endpoint="/api/state",
            json_path="profile.name",
            expected="Ada",
        ),
    )

    evaluation = await TerminalStateEvaluator(_Page()).evaluate(task, _agent_result())  # type: ignore[arg-type]

    assert evaluation.passed is True
    assert evaluation.score == 1.0
    assert evaluation.action_count == 1
    assert evaluation.failed_action_count == 0
    assert all(item.passed for item in evaluation.assertions)


async def test_evaluator_distinguishes_optional_failure_and_judge_error(monkeypatch) -> None:
    monkeypatch.setattr("webagent.evaluation.evaluator.httpx.AsyncClient", _Client)
    task = _task(
        BenchmarkAssertion(kind="url_contains", expected="/final"),
        BenchmarkAssertion(
            kind="element_visible",
            selector="#missing",
            expected=True,
            required=False,
            weight=2,
        ),
        BenchmarkAssertion(kind="element_visible", selector="#broken", expected=True),
    )

    evaluation = await TerminalStateEvaluator(_Page()).evaluate(  # type: ignore[arg-type]
        task, _agent_result(action_success=False)
    )

    assert evaluation.passed is False
    assert evaluation.score == pytest.approx(0.25)
    assert evaluation.failed_action_count == 1
    assert evaluation.assertions[-1].error == "RuntimeError: locator failed"


async def test_evaluator_records_termination_and_captcha_metrics(monkeypatch) -> None:
    monkeypatch.setattr("webagent.evaluation.evaluator.httpx.AsyncClient", _Client)
    result = _agent_result(success=False).model_copy(
        update={
            "status": "blocked",
            "events": [{"type": "captcha_detected", "outcome": "blocked"}],
        }
    )

    evaluation = await TerminalStateEvaluator(_Page()).evaluate(  # type: ignore[arg-type]
        _task(BenchmarkAssertion(kind="url_contains", expected="/final")), result
    )

    assert evaluation.termination_reason == "blocked"
    assert evaluation.captcha_encountered is True
    assert evaluation.blocked is True


async def test_evaluator_scores_final_answer_and_observed_source(monkeypatch) -> None:
    monkeypatch.setattr("webagent.evaluation.evaluator.httpx.AsyncClient", _Client)
    task = _task(
        BenchmarkAssertion(kind="answer_contains", expected="Mira Chen"),
        BenchmarkAssertion(kind="answer_contains_any", expected=["missing", "Mira Chen"]),
        BenchmarkAssertion(kind="answer_regex", expected=r"mira\.chen@.*\.test"),
        BenchmarkAssertion(
            kind="answer_in_order",
            expected=["CEDAR", "ORBIT", "LANTERN", "DELTA"],
        ),
        BenchmarkAssertion(kind="answer_not_contains", expected="Noah is the lead"),
        BenchmarkAssertion(kind="history_url_observed", expected="http://example.test/final"),
        BenchmarkAssertion(
            kind="history_url_observed_any",
            expected=["http://missing.test", "http://example.test/final"],
        ),
    )

    evaluation = await TerminalStateEvaluator(_Page()).evaluate(  # type: ignore[arg-type]
        task,
        _agent_result(
            summary=(
                "Mira Chen — mira.chen@example.test. CEDAR (stage 4), then ORBIT; "
                "LANTERN, and DELTA."
            )
        ),
    )

    assert evaluation.passed is True
    assert evaluation.answer_assertion_count == 7
    assert evaluation.answer_assertion_passed == 7

    typography = await TerminalStateEvaluator(_Page()).evaluate(  # type: ignore[arg-type]
        _task(BenchmarkAssertion(kind="answer_contains", expected="comma-separated")),
        _agent_result(summary="comma separated values"),
    )
    assert typography.passed is True

    reversed_order = await TerminalStateEvaluator(_Page()).evaluate(  # type: ignore[arg-type]
        _task(
            BenchmarkAssertion(
                kind="answer_in_order",
                expected=["CEDAR", "ORBIT", "LANTERN", "DELTA"],
            )
        ),
        _agent_result(summary="ORBIT, CEDAR, LANTERN, DELTA"),
    )

    assert reversed_order.passed is False


async def test_evaluator_records_pre_judgment_success_probability(monkeypatch) -> None:
    monkeypatch.setattr("webagent.evaluation.evaluator.httpx.AsyncClient", _Client)
    task = _task(BenchmarkAssertion(kind="url_contains", expected="/final"))

    evaluation = await TerminalStateEvaluator(_Page()).evaluate(  # type: ignore[arg-type]
        task,
        _agent_result(success_probability=0.82),
    )

    assert evaluation.success_probability == 0.82
    assert evaluation.confidence_elicited_at_step == 2
    assert evaluation.confidence_source == "self_reported"
    assert evaluation.split == "development"
    assert evaluation.task_family == "test"


async def test_evaluator_checks_tool_sequence_origin_and_artifact_hash(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("webagent.evaluation.evaluator.httpx.AsyncClient", _Client)
    artifacts = tmp_path / "tasks" / "test_task" / "artifacts" / "downloads"
    artifacts.mkdir(parents=True)
    payload = b"downloaded bytes"
    (artifacts / "payload.txt").write_bytes(payload)
    task = _task(
        BenchmarkAssertion(kind="history_origin_observed", expected="http://example.test"),
        BenchmarkAssertion(kind="history_tool_succeeded", expected="click"),
        BenchmarkAssertion(kind="history_tool_sequence", expected=["click", "done"]),
        BenchmarkAssertion(kind="artifact_exists", expected="downloads/payload.txt"),
        BenchmarkAssertion(
            kind="artifact_sha256",
            expected={
                "path": "downloads/payload.txt",
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        ),
    )

    evaluation = await TerminalStateEvaluator(  # type: ignore[arg-type]
        _Page(), output_dir=tmp_path
    ).evaluate(task, _agent_result())

    assert evaluation.passed is True


@pytest.mark.parametrize(
    "rendered",
    (
        "2026-08-26",
        "2026-8-26",
        "2026/08/26",
        "August 26, 2026",
        "Aug 26 2026",
        "26 August 2026",
    ),
)
def test_answer_date_accepts_equivalent_date_renderings(rendered: str) -> None:
    assertion = BenchmarkAssertion(kind="answer_date", expected="2026-08-26")

    assert TerminalStateEvaluator._matches(assertion, f"Published {rendered}.") is True


def test_answer_date_rejects_different_or_invalid_dates() -> None:
    assertion = BenchmarkAssertion(kind="answer_date", expected="2026-08-26")

    assert TerminalStateEvaluator._matches(assertion, "Published August 27, 2026.") is False
    with pytest.raises(ValidationError, match="ISO date"):
        BenchmarkAssertion(kind="answer_date", expected="August 26, 2026")


def test_answer_labeled_date_checks_the_first_date_after_the_exact_label() -> None:
    assertion = BenchmarkAssertion(
        kind="answer_labeled_date",
        expected={"label": "Selected report date", "date": "2026-08-26"},
    )

    assert TerminalStateEvaluator._matches(
        assertion, "Selected report date: August 26, 2026 (official file history)"
    )
    assert not TerminalStateEvaluator._matches(
        assertion,
        "Selected report date: August 27, 2026; coverage appeared August 26, 2026",
    )
    assert not TerminalStateEvaluator._matches(assertion, "Published August 26, 2026")


async def test_evaluator_requires_hash_bound_valid_certificate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("webagent.evaluation.evaluator.httpx.AsyncClient", _Client)
    layout = RunLayout.from_root(tmp_path / "runs" / "test_task")
    layout.trajectory_dir.mkdir(parents=True)
    raw = b'{"schema_version": 4}'
    layout.trace_path.write_bytes(raw)
    layout.verification_path.write_text(
        json.dumps({"valid": True, "trace_sha256": hashlib.sha256(raw).hexdigest()}),
        encoding="utf-8",
    )
    task = _task(BenchmarkAssertion(kind="certificate_valid", expected=True))

    evaluation = await TerminalStateEvaluator(  # type: ignore[arg-type]
        _Page(), output_dir=tmp_path
    ).evaluate(task, _agent_result())

    assert evaluation.passed is True


async def test_evaluator_reads_canonical_run_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("webagent.evaluation.evaluator.httpx.AsyncClient", _Client)
    layout = RunLayout.from_root(tmp_path / "runs" / "test_task")
    payload = b"canonical evidence"
    artifact = layout.downloads_dir / "payload.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(payload)
    task = _task(
        BenchmarkAssertion(kind="artifact_exists", expected="downloads/payload.txt"),
        BenchmarkAssertion(
            kind="artifact_sha256",
            expected={
                "path": "downloads/payload.txt",
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        ),
    )

    evaluation = await TerminalStateEvaluator(  # type: ignore[arg-type]
        _Page(), output_dir=tmp_path
    ).evaluate(task, _agent_result())

    assert evaluation.passed is True


def _evaluation(
    task_id: str,
    category: str,
    *,
    passed: bool,
    reported: bool,
    actions: int,
    failed_actions: int,
) -> TaskEvaluation:
    assertion = BenchmarkAssertion(kind="url_contains", expected="/done")
    return TaskEvaluation(
        task_id=task_id,
        category=category,
        goal="goal",
        passed=passed,
        score=1.0 if passed else 0.0,
        agent_reported_success=reported,
        agent_status="completed" if reported else "failed",
        duration_seconds=2,
        steps=3,
        action_count=actions,
        failed_action_count=failed_actions,
        planner_attempt_count=0,
        planner_failure_count=0,
        planner_tokens=0,
        assertions=[AssertionOutcome(assertion=assertion, passed=passed)],
    )


def test_aggregate_metrics_expose_false_completion_and_action_validity() -> None:
    summary = aggregate_evaluations(
        [
            _evaluation("a", "nav", passed=True, reported=True, actions=2, failed_actions=0),
            _evaluation("b", "nav", passed=False, reported=True, actions=2, failed_actions=1),
            _evaluation("c", "form", passed=False, reported=False, actions=0, failed_actions=0),
        ]
    )
    assert summary.task_count == 3
    assert summary.success_rate == pytest.approx(1 / 3)
    assert summary.agent_completion_rate == pytest.approx(2 / 3)
    assert summary.false_completion_rate == 0.5
    assert summary.action_validity_rate == 0.75
    assert summary.total_planner_tokens == 0
    assert summary.category_success_rate == {"form": 0.0, "nav": 0.5}
    assert summary.p95_duration_seconds == 2
    assert summary.p95_steps == 3
    assert summary.termination_reason_counts == {"unknown": 3}


def test_aggregate_empty_suite_is_well_defined() -> None:
    summary = aggregate_evaluations([])
    assert summary.task_count == 0
    assert summary.success_rate == 0.0
    assert summary.category_success_rate == {}


def test_network_task_requires_dated_source_metadata() -> None:
    with pytest.raises(ValidationError, match="source_urls"):
        BenchmarkTask(
            id="live",
            category="open_web",
            goal="read",
            start_url="https://example.com",
            assertions=[BenchmarkAssertion(kind="answer_contains", expected="x")],
            network_required=True,
        )
    source_assertions = [
        BenchmarkAssertion(kind="history_url_observed", expected="https://example.com"),
        BenchmarkAssertion(kind="answer_contains", expected="https://example.com"),
    ]
    with pytest.raises(ValidationError, match="valid_from"):
        BenchmarkTask(
            id="bad_window",
            category="open_web",
            goal="read",
            start_url="https://example.com",
            assertions=source_assertions,
            source_urls=["https://example.com"],
            snapshot_id="bad-window",
            network_required=True,
            valid_from="2027-01-01",
            valid_until="2026-01-01",
        )
    with pytest.raises(ValidationError, match="source URL was observed"):
        BenchmarkTask(
            id="missing_observation",
            category="open_web",
            goal="read",
            start_url="https://example.com",
            assertions=[BenchmarkAssertion(kind="answer_contains", expected="https://example.com")],
            source_urls=["https://example.com"],
            snapshot_id="missing-observation",
            network_required=True,
            valid_from="2026-01-01",
            valid_until="2026-12-31",
        )
    with pytest.raises(ValidationError, match="source URL in the answer"):
        BenchmarkTask(
            id="missing_citation",
            category="open_web",
            goal="read",
            start_url="https://example.com",
            assertions=[
                BenchmarkAssertion(kind="history_url_observed", expected="https://example.com")
            ],
            source_urls=["https://example.com"],
            snapshot_id="missing-citation",
            network_required=True,
            valid_from="2026-01-01",
            valid_until="2026-12-31",
        )


def test_discovery_task_requires_blank_start_and_certificate() -> None:
    assertions = [
        BenchmarkAssertion(kind="history_url_observed", expected="https://example.com"),
        BenchmarkAssertion(kind="answer_contains", expected="https://example.com"),
    ]
    fields = {
        "id": "discover",
        "category": "open_web",
        "goal": "find source",
        "source_urls": ["https://example.com"],
        "snapshot_id": "discover-2026",
        "network_required": True,
        "discovery_required": True,
        "valid_from": "2026-01-01",
        "valid_until": "2026-12-31",
    }
    with pytest.raises(ValidationError, match="about:blank"):
        BenchmarkTask(start_url="https://example.com", assertions=assertions, **fields)
    with pytest.raises(ValidationError, match="certificate_valid"):
        BenchmarkTask(start_url="about:blank", assertions=assertions, **fields)


def test_typed_scenario_and_risk_scope_enforce_sandbox_boundary() -> None:
    assertion = BenchmarkAssertion(kind="text_contains", expected="done")
    with pytest.raises(ValidationError, match="sandbox environment"):
        BenchmarkTask(
            id="unsafe",
            category="transaction",
            goal="mutate public state",
            start_url="https://example.com",
            assertions=[assertion],
            scenario="sandbox_transaction",
            risk_scope="sandbox_mutation",
        )
    with pytest.raises(ValidationError, match="sandbox_mutation risk scope"):
        BenchmarkTask(
            id="unscoped",
            category="transaction",
            goal="mutate sandbox state",
            start_url="http://127.0.0.1:8000",
            assertions=[assertion],
            scenario="sandbox_transaction",
            environment="sandbox",
        )


async def test_runner_persists_report_and_isolates_executor_errors(tmp_path: Path) -> None:
    task = _task(BenchmarkAssertion(kind="url_contains", expected="/final"))
    resets: list[str] = []

    async def reset(item: BenchmarkTask) -> None:
        resets.append(item.id)

    async def explode(_item: BenchmarkTask) -> AgentResult:
        raise RuntimeError("agent crashed")

    class _Evaluator:
        async def evaluate(self, item: BenchmarkTask, result: AgentResult) -> TaskEvaluation:
            assert result.status == "runner_error"
            return _evaluation(
                item.id,
                item.category,
                passed=False,
                reported=False,
                actions=0,
                failed_actions=0,
            )

    runner = BenchmarkRunner(  # type: ignore[arg-type]
        _Evaluator(), explode, output_dir=tmp_path, reset_task=reset
    )
    report = await runner.run("suite", [task], metadata={"mode": "test"})

    assert resets == ["test_task"]
    assert report.summary.passed_tasks == 0
    persisted = json.loads((tmp_path / "results.json").read_text())
    assert persisted["metadata"] == {"mode": "test"}
    assert json.loads((tmp_path / "analysis" / "failures.json").read_text())["task_count"] == 1
    assert (
        json.loads((tmp_path / "analysis" / "calibration.json").read_text())["status"]
        == "unavailable"
    )
    assert (
        json.loads((tmp_path / "analysis" / "transfer.json").read_text())["status"] == "unavailable"
    )
    assert persisted["schema_version"] == 4
    assert persisted["research"]["calibration"]["status"] == "unavailable"
    assert persisted["research"]["transfer"]["status"] == "unavailable"
    task_evaluation = json.loads(
        (tmp_path / "runs" / task.id / "evaluation" / "task.json").read_text()
    )
    assert task_evaluation["task_id"] == task.id

    with pytest.raises(FileExistsError, match="already contains run evidence"):
        await runner.run("suite", [task])
    assert persisted == json.loads((tmp_path / "results.json").read_text())


async def test_runner_rejects_split_leakage_before_execution(tmp_path: Path) -> None:
    base = _task(BenchmarkAssertion(kind="url_contains", expected="/final"))
    tasks = [
        base.model_copy(update={"id": "dev", "leakage_group": "shared"}),
        base.model_copy(
            update={
                "id": "held",
                "split": "held_out_task",
                "leakage_group": "shared",
            }
        ),
    ]
    called = False

    async def execute(_item: BenchmarkTask) -> AgentResult:
        nonlocal called
        called = True
        return _agent_result()

    runner = BenchmarkRunner(  # type: ignore[arg-type]
        TerminalStateEvaluator(_Page()), execute, output_dir=tmp_path
    )
    with pytest.raises(ValueError, match="leakage_group crosses"):
        await runner.run("leaky", tasks)
    assert called is False
