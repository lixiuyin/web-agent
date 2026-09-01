"""Orchestration and aggregate metrics for deterministic web benchmarks."""

from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from webagent.core.models import AgentResult
from webagent.evaluation.artifacts import StudyExecutionLayout
from webagent.evaluation.calibration import analyze_calibration
from webagent.evaluation.evaluator import TerminalStateEvaluator
from webagent.evaluation.failures import analyze_failures
from webagent.evaluation.generality import analyze_generality
from webagent.evaluation.long_horizon import analyze_long_horizon
from webagent.evaluation.models import (
    BenchmarkReport,
    BenchmarkSummary,
    BenchmarkTask,
    ResearchAnalyses,
    TaskEvaluation,
)
from webagent.evaluation.studies import (
    StudyRunContext,
    publish_study_run_records,
    validate_study_task_set,
)
from webagent.evaluation.transfer import analyze_transfer

TaskExecutor = Callable[[BenchmarkTask], Awaitable[AgentResult]]
TaskReset = Callable[[BenchmarkTask], Awaitable[None]]


def _nearest_rank_percentile(values: Sequence[float | int], percentile: float) -> float:
    """Return a deterministic nearest-rank percentile for small benchmark samples."""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def aggregate_evaluations(evaluations: Sequence[TaskEvaluation]) -> BenchmarkSummary:
    """Aggregate task-level results without treating model-declared completion as truth."""
    count = len(evaluations)
    if count == 0:
        return BenchmarkSummary(
            task_count=0,
            passed_tasks=0,
            success_rate=0.0,
            mean_score=0.0,
            agent_completion_rate=0.0,
            false_completion_rate=0.0,
            action_validity_rate=0.0,
            mean_steps=0.0,
            mean_duration_seconds=0.0,
            total_planner_tokens=0,
            mean_planner_tokens=0.0,
            answer_grounding_rate=0.0,
            timeout_rate=0.0,
            captcha_rate=0.0,
            blocked_rate=0.0,
            max_steps_rate=0.0,
            p95_duration_seconds=0.0,
            p95_steps=0.0,
            p95_planner_tokens=0.0,
            termination_reason_counts={},
            category_success_rate={},
        )

    passed = sum(item.passed for item in evaluations)
    completions = sum(item.agent_reported_success for item in evaluations)
    false_completions = sum(item.agent_reported_success and not item.passed for item in evaluations)
    actions = sum(item.action_count for item in evaluations)
    failed_actions = sum(item.failed_action_count for item in evaluations)
    planner_tokens = sum(item.planner_tokens for item in evaluations)
    answer_assertions = sum(item.answer_assertion_count for item in evaluations)
    passed_answer_assertions = sum(item.answer_assertion_passed for item in evaluations)
    category_totals: dict[str, int] = defaultdict(int)
    category_passed: dict[str, int] = defaultdict(int)
    for item in evaluations:
        category_totals[item.category] += 1
        category_passed[item.category] += int(item.passed)

    return BenchmarkSummary(
        task_count=count,
        passed_tasks=passed,
        success_rate=passed / count,
        mean_score=sum(item.score for item in evaluations) / count,
        agent_completion_rate=completions / count,
        false_completion_rate=false_completions / completions if completions else 0.0,
        action_validity_rate=(actions - failed_actions) / actions if actions else 0.0,
        mean_steps=sum(item.steps for item in evaluations) / count,
        mean_duration_seconds=sum(item.duration_seconds for item in evaluations) / count,
        total_planner_tokens=planner_tokens,
        mean_planner_tokens=planner_tokens / count,
        answer_grounding_rate=(
            passed_answer_assertions / answer_assertions if answer_assertions else 0.0
        ),
        timeout_rate=sum(item.timed_out for item in evaluations) / count,
        captcha_rate=sum(item.captcha_encountered for item in evaluations) / count,
        blocked_rate=sum(item.blocked for item in evaluations) / count,
        max_steps_rate=sum(item.max_steps_reached for item in evaluations) / count,
        p95_duration_seconds=_nearest_rank_percentile(
            [item.duration_seconds for item in evaluations], 0.95
        ),
        p95_steps=_nearest_rank_percentile([item.steps for item in evaluations], 0.95),
        p95_planner_tokens=_nearest_rank_percentile(
            [item.planner_tokens for item in evaluations], 0.95
        ),
        termination_reason_counts=dict(
            sorted(Counter(item.termination_reason for item in evaluations).items())
        ),
        category_success_rate={
            category: category_passed[category] / total
            for category, total in sorted(category_totals.items())
        },
    )


class BenchmarkRunner:
    """Run tasks in one browser and persist externally judged results."""

    def __init__(
        self,
        evaluator: TerminalStateEvaluator,
        execute_task: TaskExecutor,
        *,
        output_dir: Path,
        reset_task: TaskReset | None = None,
        execution_prepared: bool = False,
        study_context: StudyRunContext | None = None,
    ) -> None:
        self._evaluator = evaluator
        self._execute_task = execute_task
        self._layout = StudyExecutionLayout.from_root(output_dir)
        self._reset_task = reset_task
        self._execution_prepared = execution_prepared
        self._study_context = study_context

    async def run(
        self,
        suite: str,
        tasks: Sequence[BenchmarkTask],
        *,
        metadata: dict[str, object] | None = None,
    ) -> BenchmarkReport:
        _validate_split_isolation(tasks)
        if self._execution_prepared:
            self._layout.require_prepared()
        else:
            self._layout.prepare(
                study_id=(self._study_context.study_id if self._study_context else None),
                task_manifest_sha256=(
                    self._study_context.task_manifest_sha256 if self._study_context else None
                ),
                task_set_sha256=(
                    self._study_context.task_set_sha256 if self._study_context else None
                ),
            )
        if self._study_context is not None:
            validate_study_task_set(
                self._study_context,
                execution=self._layout,
                suite=suite,
                tasks=tasks,
            )
        elif self._layout.study_binding() is not None:
            raise ValueError("a study-bound execution requires an explicit StudyRunContext")
        evaluations: list[TaskEvaluation] = []
        for task in tasks:
            if self._reset_task is not None:
                await self._reset_task(task)
            started_at = time.monotonic()
            try:
                result = await self._execute_task(task)
            except Exception as exc:
                result = AgentResult(
                    success=False,
                    status="runner_error",
                    steps_taken=0,
                    total_duration=time.monotonic() - started_at,
                    final_result={"error": f"{type(exc).__name__}: {exc}"},
                )
            evaluations.append(await self._evaluator.evaluate(task, result))

        report_metadata = dict(metadata or {})
        if self._study_context is not None:
            report_metadata.update(
                {
                    "study_id": self._study_context.study_id,
                    "provider": self._study_context.provider,
                    "model": self._study_context.model,
                    "condition_id": self._study_context.condition_id,
                    "repetition": self._study_context.repetition,
                }
            )
        report = BenchmarkReport(
            suite=suite,
            created_at=datetime.now(UTC).isoformat(),
            metadata=report_metadata,
            summary=aggregate_evaluations(evaluations),
            tasks=evaluations,
            research=ResearchAnalyses(
                failures=analyze_failures(evaluations),
                calibration=analyze_calibration(evaluations),
                transfer=analyze_transfer(evaluations),
                generality=analyze_generality(evaluations),
                long_horizon=analyze_long_horizon(evaluations),
            ),
        )
        self._write_report(report)
        if self._study_context is not None:
            publish_study_run_records(
                self._study_context,
                execution=self._layout,
                suite=suite,
                created_at=report.created_at,
                evaluations=report.tasks,
            )
        return report

    def _write_report(self, report: BenchmarkReport) -> None:
        self._write_json(self._layout.report_path, report.model_dump(mode="json"))
        for task in report.tasks:
            target = self._layout.task_run(task.task_id).evaluation_dir / "task.json"
            self._write_json(target, task.model_dump(mode="json"))
        if report.research is None:
            return
        analysis_dir = self._layout.analysis_dir
        self._write_json(
            analysis_dir / "failures.json", report.research.failures.model_dump(mode="json")
        )
        self._write_json(
            analysis_dir / "calibration.json",
            report.research.calibration.model_dump(mode="json"),
        )
        self._write_json(
            analysis_dir / "transfer.json", report.research.transfer.model_dump(mode="json")
        )
        self._write_json(
            analysis_dir / "generality.json", report.research.generality.model_dump(mode="json")
        )
        self._write_json(
            analysis_dir / "long-horizon.json",
            report.research.long_horizon.model_dump(mode="json"),
        )
        queue = [
            {
                "task_id": task.task_id,
                "assertion": outcome.assertion.model_dump(mode="json"),
                "observed": outcome.observed,
                "reason": outcome.adjudication_reason,
            }
            for task in report.tasks
            for outcome in task.assertions
            if outcome.adjudication_candidate
        ]
        self._write_json(
            analysis_dir / "adjudication-queue.json",
            {
                "schema_version": 1,
                "candidate_count": len(queue),
                "boundary": "Candidates are not judge overrides or causal labels.",
                "candidates": queue,
            },
        )

    @staticmethod
    def _write_json(target: Path, payload: object) -> None:
        """Atomically persist one canonical research report."""
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)


def _validate_split_isolation(tasks: Sequence[BenchmarkTask]) -> None:
    """Fail before execution when a leakage group crosses dev and held-out data."""
    groups: defaultdict[str, set[str]] = defaultdict(set)
    for task in tasks:
        groups[str(task.leakage_group)].add(task.split)
    leaked = sorted(
        group
        for group, splits in groups.items()
        if "development" in splits
        and bool({"held_out_task", "held_out_setting"}.intersection(splits))
    )
    if leaked:
        raise ValueError(
            "leakage_group crosses development and held-out tasks: " + ", ".join(leaked)
        )
