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
        self.action_counts: Counter = Counter()  # Action frequency

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

    def _check_for_loops(self) -> None:
        """Internal method to detect loop patterns."""
        if len(self.recent_actions) < self.threshold:
            self._in_loop = False
            return

        # Priority 1: Action repetition on the SAME page (most specific)
        # Only count as a loop if the same action is repeated on the same page
        if len(self.recent_actions) >= self.threshold:
            # Check if same action + same page is repeating
            action_page_pairs = list(zip(self.recent_actions, self.recent_pages, strict=True))
            for pair in set(action_page_pairs):
                if action_page_pairs.count(pair) >= self.threshold:
                    self._in_loop = True
                    self._loop_type = "action_repeat"
                    return

        # Priority 2: Page stagnation (different actions, same page)
        # This triggers when you're trying different things but stuck on same page
        if len(self.page_hash_history) >= self.threshold:
            recent_hashes = self.page_hash_history[-self.threshold :]
            if len(set(recent_hashes)) == 1:
                if self._recently_all_non_navigation():
                    logger.debug(
                        "Skipping page_stagnation: all recent actions are non-navigation tools (%s)",
                        [a.split(":")[0] for a in self.recent_actions],
                    )
                    # Don't flag as loop — non-navigation tools don't change pages by design
                    pass
                else:
                    self._in_loop = True
                    self._loop_type = "page_stagnation"
                    return

        # Priority 3: URL oscillation (bouncing between ≤2 pages)
        if len(self.url_history) >= self.threshold:
            recent_urls = self.url_history[-self.threshold :]
            # Genuine oscillation = the recent window visits ≤2 distinct URLs
            # with real back-and-forth (≥2 transitions), e.g. A→B→A or A→B→A→B.
            # A single A→B move (1 transition) or stagnation on one URL (0) is excluded.
            if len(set(recent_urls)) == 2:
                transitions = sum(1 for a, b in pairwise(recent_urls) if a != b)
                if transitions >= 2:
                    self._in_loop = True
                    self._loop_type = "url_oscillation"
                    return

        # Priority 4: Action variety but no progress (lowest priority)
        if len(self.recent_actions) >= self.window_size:
            unique_actions = len(set(self.recent_actions))
            if unique_actions >= self.threshold:
                # Many different actions but still looping
                unique_pages = len(set(self.recent_pages))
                if unique_pages <= 2:
                    self._in_loop = True
                    self._loop_type = "action_variety_no_progress"
                    return

        self._in_loop = False

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

        if self._loop_type == "action_repeat":
            most_common = self.action_counts.most_common(1)[0][0]
            tool_name = most_common.split(":")[0]
            return (
                f"You have repeated '{tool_name}' {self.action_counts[most_common]} times. "
                f"This action is not achieving the desired result. "
                f"Try a completely different approach, or if you have enough information, "
                f"call 'done' with a summary of your findings."
            )

        elif self._loop_type == "page_stagnation":
            return (
                f"You have been on the same page for {self.threshold}+ steps. "
                f"Consider: 1) Looking for different elements, 2) Using different selectors, "
                f"3) Scrolling to reveal more content, 4) Trying a completely different strategy, "
                f"5) If you have gathered enough information, call 'done' with your findings."
            )

        elif self._loop_type == "url_oscillation":
            return (
                "You are bouncing between pages. This suggests a navigation issue. "
                "Consider: 1) Waiting for page loads, 2) Checking if elements are ready, "
                "3) Using more specific selectors, 4) Reviewing your overall strategy."
            )

        elif self._loop_type == "action_variety_no_progress":
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
