"""Terminal-state judge independent from planner prose and the ``done`` tool."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from playwright.async_api import Page

from webagent.core.models import AgentResult
from webagent.evaluation.artifacts import RunLayout
from webagent.evaluation.long_horizon import trajectory_diagnostics
from webagent.evaluation.models import (
    AssertionOutcome,
    BenchmarkAssertion,
    BenchmarkTask,
    TaskEvaluation,
)


def _json_path(payload: Any, path: str) -> Any:
    """Resolve a small dot/index JSON path such as ``cart.items.0.quantity``."""
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(part)
    return current


def _answer_contains_date(expected: str, observed: str) -> bool:
    """Match one calendar date across common numeric and English renderings."""
    target = date.fromisoformat(expected)
    year = str(target.year)
    month = target.strftime("%B")
    month_abbr = target.strftime("%b")
    day = str(target.day)
    numeric = rf"(?<!\d){year}[-/.]0?{target.month}[-/.]0?{target.day}(?!\d)"
    month_first = rf"\b(?:{month}|{month_abbr})\s+0?{day}(?:st|nd|rd|th)?[,]?\s+{year}\b"
    day_first = rf"\b0?{day}(?:st|nd|rd|th)?\s+(?:{month}|{month_abbr})[,]?\s+{year}\b"
    return any(
        re.search(pattern, observed, flags=re.IGNORECASE) is not None
        for pattern in (numeric, month_first, day_first)
    )


def _answer_starts_with_date(expected: str, observed: str) -> bool:
    """Require the first field value to be the expected calendar date."""
    target = date.fromisoformat(expected)
    year = str(target.year)
    month = target.strftime("%B")
    month_abbr = target.strftime("%b")
    day = str(target.day)
    patterns = (
        rf"{year}[-/.]0?{target.month}[-/.]0?{target.day}(?!\d)",
        rf"(?:{month}|{month_abbr})\s+0?{day}(?:st|nd|rd|th)?[,]?\s+{year}\b",
        rf"0?{day}(?:st|nd|rd|th)?\s+(?:{month}|{month_abbr})[,]?\s+{year}\b",
    )
    value = observed.lstrip()
    return any(re.match(pattern, value, flags=re.IGNORECASE) is not None for pattern in patterns)


def _matches_answer_assertion(assertion: BenchmarkAssertion, observed: Any) -> bool:
    kind = assertion.kind
    if kind == "answer_contains":
        return str(assertion.expected).casefold() in str(observed).casefold()
    if kind == "answer_date":
        return _answer_contains_date(str(assertion.expected), str(observed))
    if kind == "answer_labeled_date":
        expected = assertion.expected
        if not isinstance(expected, dict):
            return False
        label = re.escape(str(expected.get("label", "")))
        match = re.search(rf"(?im)^\s*{label}\s*:\s*", str(observed))
        if match is None:
            return False
        field_value = str(observed)[match.end() :].splitlines()[0]
        return _answer_starts_with_date(str(expected.get("date", "")), field_value)
    if kind == "answer_not_contains":
        return str(assertion.expected).casefold() not in str(observed).casefold()
    if kind == "answer_regex":
        return re.search(str(assertion.expected), str(observed), flags=re.IGNORECASE) is not None
    if kind == "answer_in_order":
        if not isinstance(assertion.expected, list):
            return False
        text = str(observed).casefold()
        cursor = 0
        for expected in assertion.expected:
            position = text.find(str(expected).casefold(), cursor)
            if position < 0:
                return False
            cursor = position + len(str(expected))
        return True
    return False


class TerminalStateEvaluator:
    """Evaluate task assertions directly against the page and optional JSON state."""

    def __init__(
        self,
        page: Page,
        *,
        http_timeout: float = 5.0,
        output_dir: Path | None = None,
    ) -> None:
        self._page = page
        self._http_timeout = http_timeout
        self._output_dir = output_dir

    async def evaluate(self, task: BenchmarkTask, result: AgentResult) -> TaskEvaluation:
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            outcomes = [
                await self._evaluate_assertion(task, assertion, client, result)
                for assertion in task.assertions
            ]

        required_passed = all(item.passed for item in outcomes if item.assertion.required)
        total_weight = sum(item.assertion.weight for item in outcomes)
        passed_weight = sum(item.assertion.weight for item in outcomes if item.passed)
        action_steps = [step for step in result.history if step.tool_call.tool_name != "done"]
        answer_outcomes = [
            item
            for item in outcomes
            if item.assertion.kind.startswith("answer_")
            or item.assertion.kind in {"history_url_observed", "history_origin_observed"}
        ]
        status = result.status.casefold()
        success_probability = _success_probability(result.final_result)
        confidence_step = next(
            (
                step.step_number
                for step in reversed(result.history)
                if step.tool_call.tool_name == "done" and step.tool_result.success
            ),
            None,
        )
        return TaskEvaluation(
            task_id=task.id,
            category=task.category,
            goal=task.goal,
            passed=required_passed,
            score=passed_weight / total_weight,
            agent_reported_success=result.success,
            agent_status=result.status,
            error=(
                str(result.final_result["error"])
                if isinstance(result.final_result.get("error"), str)
                else None
            ),
            duration_seconds=result.total_duration,
            steps=result.steps_taken,
            action_count=len(action_steps),
            failed_action_count=sum(not step.tool_result.success for step in action_steps),
            planner_attempt_count=len(result.planner_attempts),
            planner_failure_count=sum(not attempt.success for attempt in result.planner_attempts),
            planner_tokens=sum(attempt.total_tokens or 0 for attempt in result.planner_attempts),
            answer_assertion_count=len(answer_outcomes),
            answer_assertion_passed=sum(item.passed for item in answer_outcomes),
            termination_reason=result.status,
            timed_out=status == "timeout",
            captcha_encountered=any(
                str(event.get("type", "")).casefold() == "captcha_detected"
                for event in result.events
            ),
            blocked=status == "blocked",
            max_steps_reached=status == "max_steps_reached",
            split=task.split,
            task_family=task.task_family,
            setting_id=task.setting_id,
            leakage_group=task.leakage_group,
            target_failure_modes=task.target_failure_modes,
            feedback=task.feedback,
            expected_horizon=task.expected_horizon,
            scenario=task.scenario,
            environment=task.environment,
            entry_mode=task.entry_mode,
            risk_scope=task.risk_scope,
            source_origins=sorted(
                {
                    f"{parsed.scheme}://{parsed.netloc}"
                    for source in task.source_urls
                    if (parsed := urlsplit(source)).scheme and parsed.netloc
                }
            ),
            trajectory=trajectory_diagnostics(result),
            success_probability=success_probability,
            confidence_source=("self_reported" if success_probability is not None else None),
            confidence_elicited_at_step=(
                confidence_step if success_probability is not None else None
            ),
            assertions=outcomes,
        )

    async def _evaluate_assertion(
        self,
        task: BenchmarkTask,
        assertion: BenchmarkAssertion,
        client: httpx.AsyncClient,
        result: AgentResult,
    ) -> AssertionOutcome:
        try:
            observed = await self._observe(task, assertion, client, result)
            passed = self._matches(assertion, observed)
            return AssertionOutcome(assertion=assertion, passed=passed, observed=observed)
        except Exception as exc:
            return AssertionOutcome(
                assertion=assertion,
                passed=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _observe(
        self,
        task: BenchmarkTask,
        assertion: BenchmarkAssertion,
        client: httpx.AsyncClient,
        result: AgentResult,
    ) -> Any:
        if assertion.kind in {"url_equals", "url_contains"}:
            return self._page.url
        if assertion.kind == "text_contains":
            return await self._page.locator("body").inner_text()
        if assertion.kind == "json_equals":
            endpoint = urljoin(task.start_url, assertion.endpoint or "")
            response = await client.get(endpoint)
            response.raise_for_status()
            return _json_path(response.json(), assertion.json_path or "")
        if assertion.kind.startswith("answer_"):
            return str(result.final_result.get("summary", ""))
        if assertion.kind == "history_url_observed":
            expected = str(assertion.expected)
            observed_urls = [step.browser_state.url for step in result.history]
            return any(url.startswith(expected) for url in observed_urls)
        if assertion.kind == "history_origin_observed":
            expected = str(assertion.expected).rstrip("/")
            observed_origins = {
                f"{parsed.scheme}://{parsed.netloc}"
                for step in result.history
                if (parsed := urlsplit(step.browser_state.url)).scheme and parsed.netloc
            }
            return expected in observed_origins
        if assertion.kind == "history_tool_succeeded":
            expected = str(assertion.expected)
            return any(
                step.tool_call.tool_name == expected and step.tool_result.success
                for step in result.history
            )
        if assertion.kind == "history_tool_sequence":
            expected_tools = list(assertion.expected)
            successful_tools = [
                step.tool_call.tool_name for step in result.history if step.tool_result.success
            ]
            cursor = iter(successful_tools)
            return all(
                any(observed == expected for observed in cursor) for expected in expected_tools
            )
        if assertion.kind in {"artifact_exists", "artifact_sha256"}:
            path, digest = self._artifact_expectation(task, assertion)
            if not path.is_file():
                return {"exists": False, "path": str(path), "sha256": None}
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            return {
                "exists": True,
                "path": str(path),
                "sha256": actual_digest,
                "expected_sha256": digest,
            }
        if assertion.kind == "certificate_valid":
            if self._output_dir is None:
                return False
            layout = self._task_run_layout(task)
            trace_path = layout.trace_path_for_read()
            certificate_path = layout.verification_path_for_read()
            if not trace_path.is_file() or not certificate_path.is_file():
                return False
            raw = trace_path.read_bytes()
            certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
            return bool(
                certificate.get("valid") is True
                and certificate.get("trace_sha256") == hashlib.sha256(raw).hexdigest()
            )
        return await self._observe_element(assertion)

    def _artifact_expectation(
        self, task: BenchmarkTask, assertion: BenchmarkAssertion
    ) -> tuple[Path, str | None]:
        if self._output_dir is None:
            raise ValueError("artifact assertions require an evaluator output directory")
        if assertion.kind == "artifact_sha256":
            expected = assertion.expected
            if not isinstance(expected, dict):
                raise ValueError("artifact_sha256 expectation is invalid")
            relative = str(expected["path"])
            digest = str(expected["sha256"])
        else:
            relative = str(assertion.expected)
            digest = None
        root = self._task_run_layout(task).artifacts_dir.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("artifact assertion escaped the task artifacts directory")
        return path, digest

    def _task_run_layout(self, task: BenchmarkTask) -> RunLayout:
        """Resolve the canonical study run, with one-version legacy read support."""
        if self._output_dir is None:
            raise ValueError("task run lookup requires an evaluator output directory")
        canonical = self._output_dir / "runs" / task.id
        legacy = self._output_dir / "tasks" / task.id
        root = canonical if canonical.exists() or not legacy.exists() else legacy
        return RunLayout.from_root(root)

    async def _observe_element(self, assertion: BenchmarkAssertion) -> Any:
        if assertion.kind == "element_text_equals":
            return (await self._page.locator(assertion.selector or "").first.text_content()) or ""
        if assertion.kind == "element_value_equals":
            return await self._page.locator(assertion.selector or "").first.input_value()
        if assertion.kind == "element_checked":
            return await self._page.locator(assertion.selector or "").first.is_checked()
        if assertion.kind == "element_visible":
            return await self._page.locator(assertion.selector or "").first.is_visible()
        if assertion.kind == "attribute_equals":
            return await self._page.locator(assertion.selector or "").first.get_attribute(
                assertion.attribute or ""
            )
        raise ValueError(f"Unsupported assertion kind: {assertion.kind}")

    @staticmethod
    def _matches(assertion: BenchmarkAssertion, observed: Any) -> bool:
        if assertion.kind == "url_contains":
            return str(assertion.expected) in str(observed)
        if assertion.kind == "text_contains":
            return str(assertion.expected) in str(observed)
        if assertion.kind.startswith("answer_"):
            return _matches_answer_assertion(assertion, observed)
        if assertion.kind == "history_url_observed":
            return observed is True
        if assertion.kind in {
            "history_origin_observed",
            "history_tool_succeeded",
            "history_tool_sequence",
        }:
            return observed is True
        if assertion.kind == "artifact_exists":
            return isinstance(observed, dict) and observed.get("exists") is True
        if assertion.kind == "artifact_sha256":
            return bool(
                isinstance(observed, dict)
                and observed.get("exists") is True
                and observed.get("sha256") == observed.get("expected_sha256")
            )
        if assertion.kind == "certificate_valid":
            return observed is True and assertion.expected is True
        return bool(observed == assertion.expected)


def _success_probability(final_result: dict[str, Any]) -> float | None:
    """Return a valid pre-judgment task-success probability without imputing one."""
    value = final_result.get("success_probability")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    probability = float(value)
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        return None
    return probability
