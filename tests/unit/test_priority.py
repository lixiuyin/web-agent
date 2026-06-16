"""Tests for priority calculator."""

from webagent.browser.priority import (
    _extract_label,
    calculate_priority,
    get_top_elements_summary,
    sort_elements_by_priority,
)


class TestCalculatePriority:
    """Test priority calculation for elements."""

    def test_input_element_high_priority(self):
        """Input elements get high priority scores."""
        element = {
            "tag": "input",
            "text": "search query",
            "attrs": {"type": "search", "placeholder": "Search..."},
            "bbox": {"x": 100, "y": 50, "width": 300, "height": 40},
            "is_visible": True,
        }
        score = calculate_priority(element)
        # Input should get high score (> 50)
        assert score > 50

    def test_button_element_priority(self):
        """Button elements get good priority scores."""
        element = {
            "tag": "button",
            "text": "Submit",
            "attrs": {},
            "bbox": {"x": 100, "y": 100, "width": 100, "height": 40},
            "is_visible": True,
        }
        score = calculate_priority(element)
        # Button should get decent score (> 30)
        assert score > 30

    def test_position_affects_priority(self):
        """Elements higher on page get higher scores."""
        base_elem = {
            "tag": "button",
            "text": "Click",
            "attrs": {},
            "bbox": {"x": 100, "y": 50, "width": 100, "height": 40},
            "is_visible": True,
        }

        # Element at top of viewport
        top_element = {**base_elem, "bbox": {"x": 100, "y": 50, "width": 100, "height": 40}}

        # Element below fold
        bottom_element = {**base_elem, "bbox": {"x": 100, "y": 1000, "width": 100, "height": 40}}

        top_score = calculate_priority(top_element, viewport_height=720)
        bottom_score = calculate_priority(bottom_element, viewport_height=720)

        assert top_score > bottom_score

    def test_task_relevance_boost(self):
        """Matching task keywords boosts priority."""
        element = {
            "tag": "a",
            "text": "Download Qwen Technical Report PDF",
            "attrs": {"href": "/pdf"},
            "bbox": {"x": 100, "y": 200, "width": 200, "height": 30},
            "is_visible": True,
        }
        task = "Find and download the Qwen PDF report"

        score_with_task = calculate_priority(element, task=task)
        score_no_task = calculate_priority(element, task="")

        # Task relevance should boost score
        assert score_with_task > score_no_task

    def test_negative_indicators_reduce_priority(self):
        """Navigation and footer elements get lower priority."""
        footer_element = {
            "tag": "a",
            "text": "Contact",
            "attrs": {"id": "footer-contact", "class": "footer-link"},
            "bbox": {"x": 100, "y": 100, "width": 100, "height": 30},
            "is_visible": True,
        }

        main_element = {
            "tag": "a",
            "text": "Contact",
            "attrs": {"id": "main-contact", "class": "main-link"},
            "bbox": {"x": 100, "y": 100, "width": 100, "height": 30},
            "is_visible": True,
        }

        footer_score = calculate_priority(footer_element)
        main_score = calculate_priority(main_element)

        # Footer should have lower score
        assert footer_score < main_score

    def test_visibility_affects_priority(self):
        """Visible elements get higher scores."""
        base_elem = {
            "tag": "button",
            "text": "Click",
            "attrs": {},
            "bbox": {"x": 100, "y": 100, "width": 100, "height": 40},
        }

        visible_elem = {**base_elem, "is_visible": True}
        hidden_elem = {**base_elem, "is_visible": False}

        visible_score = calculate_priority(visible_elem)
        hidden_score = calculate_priority(hidden_elem)

        assert visible_score > hidden_score

    def test_score_clamped_to_100(self):
        """Priority scores are clamped to maximum of 100."""
        element = {
            "tag": "input",
            "text": "search qwen pdf download report",
            "attrs": {"id": "main-search-input", "class": "primary-search"},
            "bbox": {"x": 10, "y": 10, "width": 500, "height": 50},
            "is_visible": True,
        }
        task = "search for qwen pdf download report"

        score = calculate_priority(element, task=task)
        # Should not exceed 100
        assert score <= 100


class TestSortElementsByPriority:
    """Test element sorting by priority."""

    def test_sorts_descending_by_priority(self):
        """Elements are sorted by priority in descending order."""
        elements = [
            {
                "tag": "a",
                "text": "link",
                "attrs": {},
                "bbox": {"x": 0, "y": 1000, "width": 50, "height": 20},
                "is_visible": True,
            },
            {
                "tag": "input",
                "text": "search",
                "attrs": {},
                "bbox": {"x": 0, "y": 50, "width": 200, "height": 40},
                "is_visible": True,
            },
            {
                "tag": "button",
                "text": "click",
                "attrs": {},
                "bbox": {"x": 0, "y": 200, "width": 100, "height": 30},
                "is_visible": True,
            },
        ]

        sorted_elements = sort_elements_by_priority(elements)

        # Input should be first (highest priority)
        assert sorted_elements[0]["tag"] == "input"
        # Link should be last (lowest priority due to position)
        assert sorted_elements[-1]["tag"] == "a"

    def test_limits_to_max_elements(self):
        """Result is limited to max_elements."""
        elements = [
            {
                "tag": f"elem{i}",
                "text": f"text{i}",
                "attrs": {},
                "bbox": {"x": 0, "y": i * 10, "width": 50, "height": 20},
                "is_visible": True,
            }
            for i in range(10)
        ]

        result = sort_elements_by_priority(elements, max_elements=5)

        assert len(result) == 5

    def test_adds_priority_to_elements(self):
        """Priority score is added to each element."""
        elements = [
            {
                "tag": "input",
                "text": "search",
                "attrs": {},
                "bbox": {"x": 0, "y": 50, "width": 200, "height": 40},
                "is_visible": True,
            },
        ]

        sorted_elements = sort_elements_by_priority(elements)

        assert "_priority" in sorted_elements[0]
        assert isinstance(sorted_elements[0]["_priority"], (int, float))


class TestExtractLabel:
    """Test label extraction from elements."""

    def test_aria_label_priority(self):
        """ARIA label is preferred over text."""
        element = {
            "attrs": {"aria-label": "ARIA Label"},
            "text": "Text Content",
        }
        label = _extract_label(element)
        assert label == "ARIA Label"

    def test_text_content_fallback(self):
        """Text content is used when no ARIA label."""
        element = {
            "attrs": {},
            "text": "Button Text",
            "tag": "button",
        }
        label = _extract_label(element)
        assert label == "Button Text"

    def test_placeholder_for_input(self):
        """Placeholder is extracted for input elements."""
        element = {
            "attrs": {"placeholder": "Enter search terms"},
            "text": "",
            "tag": "input",
        }
        label = _extract_label(element)
        assert label == "Enter search terms"

    def test_title_attribute_fallback(self):
        """Title attribute is used when no other label available."""
        element = {
            "attrs": {"title": "Tooltip text"},
            "text": "",
            "tag": "div",
        }
        label = _extract_label(element)
        assert label == "Tooltip text"

    def test_tag_name_fallback(self):
        """Tag name is used as ultimate fallback."""
        element = {
            "attrs": {},
            "text": "",
            "tag": "span",
        }
        label = _extract_label(element)
        assert label == "span"

    def test_text_is_truncated(self):
        """Long text is truncated to 100 characters."""
        element = {
            "attrs": {},
            "text": "a" * 200,
            "tag": "div",
        }
        label = _extract_label(element)
        assert len(label) == 100


class TestGetTopElementsSummary:
    """Test summary generation for hidden elements."""

    def test_empty_elements_returns_empty_string(self):
        """Empty list returns empty string."""
        summary = get_top_elements_summary([])
        assert summary == ""

    def test_returns_count_of_remaining(self):
        """Summary includes count of elements not shown."""
        elements = [{"tag": "a"} for _ in range(5)]
        summary = get_top_elements_summary(elements, shown_count=10)
        assert "5 more" in summary

    def test_format_is_correct(self):
        """Summary format is 'and X more element(s)'."""
        elements = [{"tag": "a"} for _ in range(3)]
        summary = get_top_elements_summary(elements, shown_count=7)
        assert summary == "... and 3 more element(s)"
