"""Unit tests for loop detection."""

from __future__ import annotations

from webagent.agent.loop_detector import LoopDetector


class TestLoopDetector:
    """Tests for LoopDetector class."""

    def test_init(self):
        """Test detector initialization."""
        detector = LoopDetector(window_size=5, threshold=3)
        assert detector.window_size == 5
        assert detector.threshold == 3
        assert detector.in_loop is False
        assert detector.loop_type == ""

    def test_action_repeat_detection(self):
        """Test detection of repeated actions."""
        detector = LoopDetector(window_size=5, threshold=3)

        # Add same action 3 times
        for _ in range(3):
            detector.add_action("click", "https://example.com", "abc123")

        is_looping, nudge = detector.is_looping()

        assert is_looping is True
        assert "click" in nudge
        assert detector.loop_type == "action_repeat"

    def test_no_loop_below_threshold(self):
        """Test that no loop is detected below threshold."""
        detector = LoopDetector(window_size=5, threshold=3)

        # Add same action 2 times (below threshold)
        for _ in range(2):
            detector.add_action("click", "https://example.com", "abc123")

        is_looping, _ = detector.is_looping()

        assert is_looping is False

    def test_page_stagnation_detection(self):
        """Test detection of staying on same page."""
        detector = LoopDetector(window_size=5, threshold=3)

        # Different actions but same page hash
        for i in range(3):
            detector.add_action(f"action_{i}", "https://example.com", "same_hash_123")

        is_looping, nudge = detector.is_looping()

        assert is_looping is True
        assert "same page" in nudge.lower()
        assert detector.loop_type == "page_stagnation"

    def test_scroll_churn_ignores_changing_viewport_hashes(self):
        detector = LoopDetector(window_size=5, threshold=3)

        detector.add_action("scroll", "https://example.com/article", "top", {"amount": 800})
        detector.add_action("scroll", "https://example.com/article", "middle", {"amount": 900})
        detector.add_action("scroll", "https://example.com/article", "bottom", {"amount": 700})

        is_looping, nudge = detector.is_looping()

        assert is_looping is True
        assert detector.loop_type == "scroll_churn"
        assert "extract_text" in nudge

    def test_scroll_across_distinct_urls_is_not_churn(self):
        detector = LoopDetector(window_size=5, threshold=3)
        for index in range(3):
            detector.add_action(
                "scroll",
                f"https://example.com/article/{index}",
                f"hash-{index}",
                {"amount": 800},
            )

        assert detector.is_looping()[0] is False

    def test_url_oscillation_detection(self):
        """Test detection of bouncing between URLs."""
        detector = LoopDetector(window_size=7, threshold=3)

        # Simulate A -> B -> A pattern with unique action-page combinations
        # Use unique parameters to avoid action-repeat detection
        # But keep URLs oscillating between two pages
        actions = [
            ("click", {"selector": "#btn1"}),
            ("scroll", {"amount": 100}),
            ("click", {"selector": "#btn2"}),
            ("scroll", {"amount": 200}),
            ("click", {"selector": "#btn3"}),
            ("scroll", {"amount": 300}),
        ]
        urls = ["https://example.com/pageA", "https://example.com/pageB"] * 3

        for (action, params), url in zip(actions, urls, strict=True):
            detector.add_action(action, url, f"hash_{hash(url)}", params)

        is_looping, nudge = detector.is_looping()

        # Should detect oscillation since unique actions but URLs oscillate
        assert is_looping is True
        # Could be either oscillation or action_repeat depending on implementation
        assert (
            "bouncing" in nudge.lower()
            or "oscillation" in nudge.lower()
            or "navigation" in nudge.lower()
            or "repeated" in nudge.lower()
        )

    def test_action_variety_no_progress(self):
        """Test detection of many different actions with no progress."""
        detector = LoopDetector(window_size=5, threshold=3)

        # Many different actions but staying on same 2 pages
        pages = ["page_a", "page_b"]
        for i in range(5):
            detector.add_action(f"unique_action_{i}", pages[i % 2], f"hash_{pages[i % 2]}")

        is_looping, _nudge = detector.is_looping()

        # Should detect loop due to limited page variety
        assert is_looping is True or detector.loop_type == "action_variety_no_progress"

    def test_no_loop_with_progress(self):
        """Test that normal progress is not flagged as loop."""
        detector = LoopDetector(window_size=5, threshold=3)

        # Simulate normal progress through pages
        for i in range(5):
            detector.add_action("click", f"https://example.com/page{i}", f"hash_{i}")

        is_looping, nudge = detector.is_looping()

        # With normal progress through unique pages, should not loop
        assert is_looping is False, f"Unexpected loop detected: {nudge}"

    def test_reset(self):
        """Test that reset clears detector state."""
        detector = LoopDetector(window_size=5, threshold=3)

        # Create a loop
        for _ in range(3):
            detector.add_action("click", "https://example.com", "abc123")

        assert detector.in_loop is True

        detector.reset()

        assert detector.in_loop is False
        assert len(detector.recent_actions) == 0
        assert len(detector.action_counts) == 0

    def test_window_size_enforcement(self):
        """Test that window size is respected."""
        detector = LoopDetector(window_size=3, threshold=2)

        # Add more actions than window size
        for i in range(5):
            detector.add_action(f"action_{i}", f"https://example.com/page{i}", f"hash_{i}")

        assert len(detector.recent_actions) == 3

    def test_get_stats(self):
        """Test statistics retrieval."""
        detector = LoopDetector(window_size=5, threshold=3)

        detector.add_action("click", "https://example.com", "abc123")
        detector.add_action("scroll", "https://example.com", "abc123")
        detector.add_action("click", "https://example.com", "abc123")

        stats = detector.get_stats()

        assert isinstance(stats, dict)
        assert "in_loop" in stats
        assert "recent_actions" in stats
        assert "action_counts" in stats
        assert "window_size" in stats
        assert stats["window_size"] == 5  # Should match detector's window_size

    def test_parameter_fingerprinting(self):
        """Test that parameters are included in action signature."""
        detector = LoopDetector(window_size=5, threshold=2)

        # Same action but different parameters should count differently
        # Use different page hashes to avoid page_stagnation triggering
        detector.add_action("click", "https://example.com", "abc123", {"selector": "#button1"})
        detector.add_action(
            "click", "https://example.com/page2", "def456", {"selector": "#button2"}
        )

        # Should not be a loop since parameters AND pages differ
        is_looping, _ = detector.is_looping()

        assert is_looping is False

    def test_same_action_same_params_is_loop(self):
        """Test that same action with same parameters is detected as loop."""
        detector = LoopDetector(window_size=5, threshold=2)

        params = {"selector": "#button1"}
        detector.add_action("click", "https://example.com", "abc123", params)
        detector.add_action("click", "https://example.com", "abc123", params)

        is_looping, _ = detector.is_looping()

        assert is_looping is True
