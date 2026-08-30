"""Tests for agent-loop helper functions and CLI override handling."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from webagent.agent.loop import _attach_figure, _is_browser_disconnect, _select_figure
from webagent.cli import _apply_cli_overrides
from webagent.core.config import AgentConfig
from webagent.tools.builtin.file_tools import _detect_vision_failure, _resize_for_api


class TestIsBrowserDisconnect:
    def test_target_closed(self) -> None:
        assert _is_browser_disconnect(RuntimeError("Target closed"))

    def test_connection_closed(self) -> None:
        assert _is_browser_disconnect(Exception("Connection closed by peer"))

    def test_unrelated_error(self) -> None:
        assert not _is_browser_disconnect(ValueError("bad parameter"))


class TestSelectFigure:
    def _artifacts(self, tmp_path: Path) -> Path:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        return artifacts

    def test_first_image_attachment_wins(self, tmp_path: Path) -> None:
        artifacts = self._artifacts(tmp_path)
        img = artifacts / "a.png"
        img.write_bytes(b"x")
        found = _select_figure([str(img), "notes.txt"], None, artifacts)
        assert found == img

    def test_falls_back_to_last_figure_path(self, tmp_path: Path) -> None:
        artifacts = self._artifacts(tmp_path)
        img = artifacts / "b.jpg"
        img.write_bytes(b"x")
        found = _select_figure(["not-an-image"], str(img), artifacts)
        assert found == img

    def test_no_candidates(self, tmp_path: Path) -> None:
        assert _select_figure(None, None, self._artifacts(tmp_path)) is None


class TestAttachFigure:
    def test_backfills_when_model_omits_attachment(self) -> None:
        result: dict[str, object] = {"summary": "s", "attachments": []}
        _attach_figure(result, Path("/x/artifacts/fig.jpg"))
        assert result["attachments"] == ["/x/artifacts/fig.jpg"]

    def test_no_duplicate_when_already_present(self) -> None:
        result: dict[str, object] = {"summary": "s", "attachments": ["/x/artifacts/fig.jpg"]}
        _attach_figure(result, Path("/x/artifacts/fig.jpg"))
        assert result["attachments"] == ["/x/artifacts/fig.jpg"]

    def test_normalizes_missing_attachments_field(self) -> None:
        result: dict[str, object] = {"summary": "s"}
        _attach_figure(result, Path("/x/artifacts/fig.jpg"))
        assert result["attachments"] == ["/x/artifacts/fig.jpg"]

    def test_noop_without_figure(self) -> None:
        result: dict[str, object] = {"summary": "s", "attachments": []}
        _attach_figure(result, None)
        assert result["attachments"] == []


class TestResizeForApi:
    def test_small_image_untouched(self) -> None:
        img = Image.new("RGB", (100, 50))
        assert _resize_for_api(img) is img

    def test_large_image_downscaled(self) -> None:
        img = Image.new("RGB", (4000, 2000))
        out = _resize_for_api(img)
        assert max(out.width, out.height) == 2000
        assert out.height == 1000


class TestDetectVisionFailure:
    def test_api_not_functioning(self) -> None:
        assert _detect_vision_failure("The Vision API is not functioning right now")

    def test_no_image_phrases(self) -> None:
        assert _detect_vision_failure("I cannot see any content in this request")

    def test_normal_analysis(self) -> None:
        assert not _detect_vision_failure("The chart shows rising revenue in Q3")


def _args(**overrides: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "model": None,
        "api_url": None,
        "api_key": None,
        "use_vllm": None,
        "vllm_model_name": None,
        "vllm_api_url": None,
        "vllm_api_key": None,
        "headless": False,
        "headed": False,
        "output": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestApplyCliOverrides:
    def test_no_flags_leaves_defaults(self) -> None:
        cfg = AgentConfig()
        before = cfg.model_name
        _apply_cli_overrides(cfg, _args())
        assert cfg.model_name == before

    def test_model_overrides_applied(self) -> None:
        cfg = AgentConfig()
        _apply_cli_overrides(
            cfg,
            _args(
                model="gpt-test",
                api_url="https://api.test/v1",
                api_key="sk-test",
                vllm_model_name="qwen-test",
                vllm_api_url="http://localhost:8000",
                vllm_api_key="local",
            ),
        )
        assert cfg.model_name == "gpt-test"
        assert cfg.model_api_url == "https://api.test/v1"
        assert cfg.model_api_key == "sk-test"
        assert cfg.vllm_model_name == "qwen-test"
        assert cfg.vllm_api_url == "http://localhost:8000"
        assert cfg.vllm_api_key == "local"

    def test_vllm_toggle_and_headless_flags(self) -> None:
        cfg = AgentConfig()
        _apply_cli_overrides(cfg, _args(use_vllm=True, headless=True))
        assert cfg.use_vllm is True
        assert cfg.browser_headless is True

    def test_headed_wins_over_default(self) -> None:
        cfg = AgentConfig()
        _apply_cli_overrides(cfg, _args(headed=True))
        assert cfg.browser_headless is False

    def test_output_dir_override(self, tmp_path: Path) -> None:
        cfg = AgentConfig()
        _apply_cli_overrides(cfg, _args(output=str(tmp_path / "out")))
        assert cfg.output_dir == tmp_path / "out"
