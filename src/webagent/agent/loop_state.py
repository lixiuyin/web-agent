"""Per-turn state shared by orchestration, planning and checkpoint persistence."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from webagent.agent.checkpoint import (
    BrowserResumeState,
    PendingAction,
)
from webagent.agent.state import PlanningState
from webagent.agent.strategy import (
    StrategyManager,
)


class LoopState:
    """Mutable per-turn bookkeeping shared across one task's logical steps."""

    def __init__(
        self,
        start_time: float,
        *,
        run_id: str | None = None,
        resume_count: int = 0,
        resumed: bool = False,
        next_step: int = 1,
        planning_state: PlanningState | None = None,
        strategy_manager: StrategyManager | None = None,
    ) -> None:
        self.start_time = start_time
        self.run_id = run_id or str(uuid4())
        self.resume_count = resume_count
        self.resumed = resumed
        self.next_step = next_step
        self.consecutive_failures = 0
        self.last_figure_path: str | None = None
        self.final_result: dict[str, Any] = {}
        self.planning_state = planning_state
        self.strategy_manager = strategy_manager
        self.pending_action: PendingAction | None = None
        self.browser_state = BrowserResumeState()
        self.previous_checkpoint_sha256: str | None = None
