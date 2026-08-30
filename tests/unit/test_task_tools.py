"""Tests for the task lifecycle ``done`` tool."""

from __future__ import annotations

import pytest

from webagent.tools.builtin.task_tools import DoneTool


class TestDoneTool:
    def test_validation_requires_summary(self) -> None:
        tool = DoneTool()
        with pytest.raises(ValueError):
            tool.validate_params({})
        with pytest.raises(ValueError):
            tool.validate_params({"summary": "   "})

    def test_validation_accepts_result_alias(self) -> None:
        DoneTool().validate_params({"result": "an answer"})

    @pytest.mark.parametrize("value", [-0.01, 1.01, float("inf"), True, "0.8"])
    def test_validation_rejects_invalid_success_probability(self, value: object) -> None:
        with pytest.raises(ValueError, match="success_probability"):
            DoneTool().validate_params({"summary": "answer", "success_probability": value})

    async def test_execute_returns_summary_and_attachments(self) -> None:
        tool = DoneTool()
        result = await tool.execute({"summary": "final answer", "attachments": ["a.pdf", "b.png"]})
        assert result.success
        assert result.data["summary"] == "final answer"
        assert result.data["attachments"] == ["a.pdf", "b.png"]

    async def test_execute_preserves_optional_pre_judgment_success_probability(self) -> None:
        tool = DoneTool()
        params = {"summary": "final answer", "success_probability": 0.75}
        tool.validate_params(params)
        result = await tool.execute(params)

        assert result.data["success_probability"] == 0.75

    async def test_execute_uses_result_alias_and_default_attachments(self) -> None:
        tool = DoneTool()
        result = await tool.execute({"result": "answer via alias"})
        assert result.success
        assert result.data["summary"] == "answer via alias"
        assert result.data["attachments"] == []
        assert "success_probability" not in result.data
