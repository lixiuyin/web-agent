"""Action loop detection for web agent.

Detects when the agent is stuck in repeating action patterns,
providing nudges to help break the loop and continue progress.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from itertools import pairwise
from typing import Any

logger = logging.getLogger("webagent")

# Tools that do not change browser page state.  These operate on local files,
# images, or other non-browser resources and should not trigger page-based
# stagnation detection.
NON_NAVIGATION_TOOLS: frozenset[str] = frozenset(
    {
        # PDF tools (operate on local files)
        "download_pdf",
        "pdf_parse",
        "pdf_find_images",
        "pdf_find_tables",
        "pdf_find_section",
        "pdf_content_summary",
        "pdf_extract_text",
        "pdf_extract_images",
        "pdf_get_figure_info",
        "pdf_extract_table_data",
        "pdf_find_mentions",
        "pdf_get_section",
        "pdf_get_hierarchy",
        "pdf_get_metadata",
        "pdf_extract_metrics",
        "pdf_extract_topics",
        "pdf_extract_citations",
        "pdf_summarize_sections",
        "pdf_compare_entities",
        "pdf_qa",
        "pdf_search",
        "pdf_list_figures",
        "pdf_list_tables",
        "pdf_list_sections",
        "pdf_analyze_figure",
        # File/image analysis tools
        "analyze_image",
        "read_image",
        "save_image",
        "write_text",
        # Observation tools (no navigation)
        "screenshot",
        "dom_summary",
        "extract_text",
        # Search
        "search",
    }
)


class LoopDetector:
    """Detects repeating action patterns during agent execution.

    Uses action + page fingerprinting to detect loops without complex
    state tracking. When a loop is detected, provides a nudge message
    to help the agent break the pattern and try a different approach.
    """

    def __init__(self, window_size: int = 5, threshold: int = 3) -> None:
        """Initialize the loop detector.

        Args:
            window_size: Number of recent actions to keep in memory
            threshold: Number of repetitions before declaring a loop
        """
        self.window_size = window_size
        self.threshold = threshold

        # History tracking
        self.recent_actions: list[str] = []  # Action signatures
        self.recent_pages: list[str] = []  # Page signatures
        self.action_counts: Counter[str] = Counter()  # Action frequency

        # For detecting page stagnation
        self.page_hash_history: list[str] = []
        self.url_history: list[str] = []

        # Loop state
        self._in_loop: bool = False
        self._loop_type: str = ""

    def add_action(
        self,
        tool_name: str,
        page_url: str,
        page_hash: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Record an action and check for loops.

        Args:
            tool_name: Name of the tool that was executed
            page_url: Current page URL
            page_hash: Hash of page content for detecting stagnation
            parameters: Optional tool parameters for finer fingerprinting
        """
        # Create action signature
        if parameters:
            # Include key parameters in signature
            param_str = ",".join(
                f"{k}={v}"
                for k, v in sorted(parameters.items())
                if isinstance(v, (str, int, float))
            )
            action_sig = f"{tool_name}:{param_str}"
        else:
            action_sig = tool_name

        # Create page signature
        if page_hash:
            page_sig = f"{page_url}:{page_hash[:16]}"
        else:
            page_sig = page_url

        # Add to history
        self.recent_actions.append(action_sig)
        self.recent_pages.append(page_sig)
        self.page_hash_history.append(page_hash or self._hash_url(page_url))
        self.url_history.append(page_url)

        # Update action counts
        self.action_counts[action_sig] += 1

        # Keep only recent window
        if len(self.recent_actions) > self.window_size:
            old_action = self.recent_actions.pop(0)
            self.recent_pages.pop(0)  # Keep in sync with actions
            self.page_hash_history.pop(0)
            self.url_history.pop(0)

            # Decrement action count
            self.action_counts[old_action] -= 1
            if self.action_counts[old_action] <= 0:
                del self.action_counts[old_action]

        # Check for loops
        self._check_for_loops()

    def is_looping(self) -> tuple[bool, str]:
        """Check if currently stuck in a loop.

        Returns:
            Tuple of (is_looping, nudge_message)
        """
        if not self._in_loop:
            return False, ""

        nudge = self._get_nudge_message()
        return True, nudge

    def reset(self) -> None:
        """Reset loop detection state."""
        self.recent_actions.clear()
        self.recent_pages.clear()
        self.page_hash_history.clear()
        self.url_history.clear()
        self.action_counts.clear()
        self._in_loop = False
        self._loop_type = ""

    def export_state(self) -> dict[str, Any]:
        """Return the bounded detector window for checkpoint/resume."""
        return {
            "schema_version": 1,
            "window_size": self.window_size,
            "threshold": self.threshold,
            "recent_actions": list(self.recent_actions),
            "recent_pages": list(self.recent_pages),
            "page_hash_history": list(self.page_hash_history),
            "url_history": list(self.url_history),
            "in_loop": self._in_loop,
            "loop_type": self._loop_type,
        }

    def import_state(self, state: dict[str, Any]) -> None:
        """Restore only a state produced by :meth:`export_state`."""
        if (
            state.get("schema_version") != 1
            or state.get("window_size") != self.window_size
            or state.get("threshold") != self.threshold
        ):
            raise ValueError("loop detector checkpoint configuration mismatch")
        keys = ("recent_actions", "recent_pages", "page_hash_history", "url_history")
        values: dict[str, list[str]] = {}
        for key in keys:
            value = state.get(key)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"loop detector checkpoint field {key} is invalid")
            if len(value) > self.window_size:
                raise ValueError(f"loop detector checkpoint field {key} exceeds its window")
            values[key] = list(value)
        lengths = {len(value) for value in values.values()}
        if len(lengths) != 1:
            raise ValueError("loop detector checkpoint history lengths differ")
        in_loop = state.get("in_loop")
        loop_type = state.get("loop_type")
        if not isinstance(in_loop, bool) or not isinstance(loop_type, str):
            raise ValueError("loop detector checkpoint status is invalid")
        self.recent_actions = values["recent_actions"]
        self.recent_pages = values["recent_pages"]
        self.page_hash_history = values["page_hash_history"]
        self.url_history = values["url_history"]
        self.action_counts = Counter(self.recent_actions)
        self._in_loop = in_loop
        self._loop_type = loop_type

    def _check_for_loops(self) -> None:
        """Internal method to detect loop patterns."""
        if len(self.recent_actions) < self.threshold:
            self._in_loop = False
            return

        # Detectors in priority order (most specific first).
        detectors = (
            ("action_repeat", self._is_action_repeat),
            ("scroll_churn", self._is_scroll_churn),
            ("page_stagnation", self._is_page_stagnation),
            ("url_oscillation", self._is_url_oscillation),
            ("action_variety_no_progress", self._is_action_variety_no_progress),
        )
        for loop_type, detector in detectors:
            if detector():
                self._in_loop = True
                self._loop_type = loop_type
                return

        self._in_loop = False

    def _is_scroll_churn(self) -> bool:
        """Detect repeated viewport traversal on one URL despite changing snapshots."""
        if len(self.recent_actions) < self.threshold:
            return False
        actions = self.recent_actions[-self.threshold :]
        urls = self.url_history[-self.threshold :]
        return (
            all(action.split(":", 1)[0] == "scroll" for action in actions) and len(set(urls)) == 1
        )

    def _is_action_repeat(self) -> bool:
        """Priority 1: the same action repeated on the same page."""
        action_page_pairs = list(zip(self.recent_actions, self.recent_pages, strict=True))
        return any(
            action_page_pairs.count(pair) >= self.threshold for pair in set(action_page_pairs)
        )

    def _is_page_stagnation(self) -> bool:
        """Priority 2: different actions but stuck on the same page.

        Skipped when all recent actions are non-navigation tools (they don't
        change pages by design, so stagnation is expected).
        """
        if len(self.page_hash_history) < self.threshold:
            return False
        recent_hashes = self.page_hash_history[-self.threshold :]
        if len(set(recent_hashes)) != 1:
            return False

        if self._recently_all_non_navigation():
            logger.debug(
                "Skipping page_stagnation: all recent actions are non-navigation tools (%s)",
                [a.split(":")[0] for a in self.recent_actions],
            )
            return False
        return True

    def _is_url_oscillation(self) -> bool:
        """Priority 3: bouncing between exactly 2 URLs.

        Genuine oscillation = the recent window visits 2 distinct URLs with real
        back-and-forth (≥2 transitions), e.g. A→B→A or A→B→A→B. A single A→B
        move (1 transition) or stagnation on one URL (0) is excluded.
        """
        if len(self.url_history) < self.threshold:
            return False
        recent_urls = self.url_history[-self.threshold :]
        if len(set(recent_urls)) != 2:
            return False
        transitions = sum(1 for a, b in pairwise(recent_urls) if a != b)
        return transitions >= 2

    def _is_action_variety_no_progress(self) -> bool:
        """Priority 4: many different actions but still only ≤2 distinct pages."""
        if len(self.recent_actions) < self.window_size:
            return False
        if len(set(self.recent_actions)) < self.threshold:
            return False
        return len(set(self.recent_pages)) <= 2

    def _is_research_loop(self) -> bool:
        """Return True if recent actions are research/extraction tools (not navigation)."""
        if not self.recent_actions:
            return False
        recent_tool_names = [a.split(":")[0] for a in self.recent_actions]
        research_count = sum(1 for t in recent_tool_names if t in NON_NAVIGATION_TOOLS)
        return research_count >= len(recent_tool_names) * 0.7

    def _recently_all_non_navigation(self) -> bool:
        """Return True if all recent actions are non-navigation tools."""
        if not self.recent_actions:
            return False
        recent_tool_names = [a.split(":")[0] for a in self.recent_actions]
        return all(t in NON_NAVIGATION_TOOLS for t in recent_tool_names)

    def _get_nudge_message(self) -> str:
        """Generate a helpful nudge message based on loop type and context."""
        # If mostly research/extraction tools, guide toward completion
        if self._is_research_loop():
            return (
                "IMPORTANT: You have been extracting information for many steps. "
                "You likely already have enough data to answer the task. "
                "STOP searching and call the 'done' tool NOW with a comprehensive "
                "summary of everything you have found so far. Include the figure "
                "caption, textual descriptions, and any key findings from the PDF."
            )

        if self._loop_type == "scroll_churn":
            return (
                "You have scrolled the same page repeatedly. Do not keep traversing the viewport. "
                "Use 'extract_text' once with a semantic container such as body, main, or article, "
                "or call 'done' now if the visible evidence already answers the task."
            )

        if self._loop_type == "action_repeat":
            most_common = self.action_counts.most_common(1)[0][0]
            tool_name = most_common.split(":")[0]
            return (
                f"You have repeated '{tool_name}' {self.action_counts[most_common]} times. "
                f"This action is not achieving the desired result. "
                f"Try a completely different approach, or if you have enough information, "
                f"call 'done' with a summary of your findings."
            )

        if self._loop_type == "page_stagnation":
            return (
                f"You have been on the same page for {self.threshold}+ steps. "
                f"Consider: 1) Looking for different elements, 2) Using different selectors, "
                f"3) Scrolling to reveal more content, 4) Trying a completely different strategy, "
                f"5) If you have gathered enough information, call 'done' with your findings."
            )

        if self._loop_type == "url_oscillation":
            return (
                "You are bouncing between pages. This suggests a navigation issue. "
                "Consider: 1) Waiting for page loads, 2) Checking if elements are ready, "
                "3) Using more specific selectors, 4) Reviewing your overall strategy."
            )

        if self._loop_type == "action_variety_no_progress":
            return (
                "You have tried many actions but are not making progress. "
                "If you have already gathered useful information, call 'done' with a summary. "
                "Otherwise, take a step back and try a completely different approach."
            )

        return "You appear to be stuck in a loop. Try a different approach or use different tools."

    @staticmethod
    def _hash_url(url: str) -> str:
        """Create a simple hash from URL for page tracking."""
        return hashlib.md5(url.encode()).hexdigest()[:16]

    @property
    def in_loop(self) -> bool:
        """Whether currently in a detected loop."""
        return self._in_loop

    @property
    def loop_type(self) -> str:
        """The type of loop currently detected."""
        return self._loop_type

    def get_stats(self) -> dict[str, Any]:
        """Get current statistics for debugging.

        Returns:
            Dictionary with loop detector state
        """
        return {
            "in_loop": self._in_loop,
            "loop_type": self._loop_type,
            "recent_actions": self.recent_actions.copy(),
            "recent_pages": self.recent_pages.copy(),
            "action_counts": dict(self.action_counts.most_common(5)),
            "unique_pages": len(set(self.recent_pages)),
            "window_size": self.window_size,
            "threshold": self.threshold,
        }
