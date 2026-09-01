"""Branch tests for the PDF Q&A tools (``pdf_qa_tools``).

Parse results are seeded into the shared cache so the full execute pipeline runs
offline; figure images are written to disk so the vision-analysis branches of
``pdf_analyze_figure`` execute against real files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from benchmarks.suites.document_figures.fast_path import build_benchmark_corpus
from PIL import Image

from webagent.core.config import AgentConfig
from webagent.parser.models import ImageInfo, PDFParseResult, TableInfo, TextBlock
from webagent.tools.builtin._pdf_common import pdf_cache_key, pdf_result_cache
from webagent.tools.builtin.pdf_qa_tools import (
    PdfAnalyzeFigureTool,
    PdfListFiguresTool,
    PdfListSectionsTool,
    PdfQATool,
    PdfSearchTool,
    _open_image,
    _pick_higher_res_image,
    _resolve_figure,
)


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


def _seed(artifacts_dir: Path, result: PDFParseResult) -> Path:
    pdf = artifacts_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    pdf_result_cache[pdf_cache_key(pdf, artifacts_dir)] = result
    return pdf


def _base_result(fig_path: str = "/nonexistent/fig1.png") -> PDFParseResult:
    r = PDFParseResult(
        markdown_path=None, json_path=None, images_dir="i", output_dir="o", backend="marker"
    )
    r.text_blocks.extend(
        [
            TextBlock("The Transformer architecture uses self-attention.", 0, (0, 0, 0, 0)),
            TextBlock("BLEU scores improved on translation benchmarks.", 1, (0, 0, 0, 0)),
        ]
    )
    r.images.append(
        ImageInfo(fig_path, 0, (0, 0, 0, 0), caption="architecture overview", figure_number="1")
    )
    r.tables.append(
        TableInfo(
            "/tmp/t1.html",
            1,
            (0, 0, 0, 0),
            caption="BLEU results",
            table_number="1",
            html_body="<table></table>",
        )
    )
    r.sections["1:Introduction"] = [TextBlock("Intro", 0, (0, 0, 0, 0), level=1)]
    return r


class TestPdfQA:
    async def test_url_path_rejected(self, artifacts_dir: Path) -> None:
        tool = PdfQATool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": "http://example.com/a.pdf", "question": "q"})
        assert not result.success

    async def test_figure_and_table_hints(self, artifacts_dir: Path) -> None:
        pdf = _seed(artifacts_dir, _base_result())
        tool = PdfQATool(artifacts_dir=artifacts_dir)
        result = await tool.execute(
            {"path": str(pdf), "question": "What does figure 1 and table 1 show?"}
        )
        assert result.success
        assert result.data["found_figures"]
        assert result.data["found_tables"]
        assert "figure" in result.data["hints"].lower()

    async def test_no_relevant_text_hint(self, artifacts_dir: Path) -> None:
        pdf = _seed(artifacts_dir, _base_result())
        tool = PdfQATool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf), "question": "zzz qqqq wwww"})
        assert result.success
        assert "No directly relevant text" in result.data["hints"]


class TestPdfSearch:
    async def test_results_have_ranked_sources(self, artifacts_dir: Path) -> None:
        pdf = _seed(artifacts_dir, _base_result())
        tool = PdfSearchTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf), "query": "attention architecture"})
        assert result.success
        assert result.data["results"]
        assert result.data["results"][0]["rank"] == 1


class TestLocalFigureFastPath:
    async def test_unambiguous_vector_figure_bypasses_cloud_parser(
        self,
        artifacts_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        corpus = build_benchmark_corpus(artifacts_dir / "corpus")
        document = next(item for item in corpus if item.name == "vector_caption_below")

        async def cloud_parser_must_not_run(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("cloud parser should be bypassed")

        monkeypatch.setattr(
            "webagent.tools.builtin.pdf_qa_tools.load_pdf_result",
            cloud_parser_must_not_run,
        )

        class Planner:
            vision_actually_works = True
            last_call_metadata = {"total_tokens": 20}

            async def analyze_image(self, image: Image.Image, question: str) -> str:
                assert image.width >= 700
                assert "Figure 1:" in question
                return "The locally rendered vector chart compares model accuracy and efficiency."

        config = AgentConfig(
            _env_file=None,
            output_dir=artifacts_dir.parent,
            local_figure_fast_path=True,
        )
        tool = PdfAnalyzeFigureTool(
            artifacts_dir=artifacts_dir,
            planner=Planner(),
            config=config,
        )

        result = await tool.execute(
            {
                "path": str(document.path),
                "figure_number_or_caption": "Figure 1",
                "question": "What does it show?",
            }
        )

        assert result.success is True
        assert result.data["local_figure_fast_path"]["used"] is True
        assert result.data["local_figure_fast_path"]["confidence"] >= 0.9
        assert result.data["local_figure_fast_path"]["visual_kind"] == "vector"
        assert result.data["related_tables"] == []
        assert Path(result.data["image_path"]).is_file()

    async def test_low_confidence_layout_falls_back_to_cached_structured_parse(
        self,
        artifacts_dir: Path,
    ) -> None:
        corpus = build_benchmark_corpus(artifacts_dir / "corpus")
        document = next(item for item in corpus if item.name == "vector_caption_above")
        cloud_image = artifacts_dir / "cloud_figure.png"
        Image.new("RGB", (300, 200), "blue").save(cloud_image)
        parsed = _base_result(str(cloud_image))
        pdf_result_cache[pdf_cache_key(document.path, artifacts_dir)] = parsed

        class Planner:
            vision_actually_works = True

            async def analyze_image(self, image: Image.Image, question: str) -> str:
                return "The structured-parser figure remains the safe fallback."

        config = AgentConfig(
            _env_file=None,
            output_dir=artifacts_dir.parent,
            local_figure_fast_path=True,
            local_figure_min_confidence=0.9,
        )
        tool = PdfAnalyzeFigureTool(
            artifacts_dir=artifacts_dir,
            planner=Planner(),
            config=config,
        )

        result = await tool.execute({"path": str(document.path), "figure_number_or_caption": "1"})

        assert result.success is True
        assert result.data["local_figure_fast_path"]["used"] is False
        assert result.data["local_figure_fast_path"]["duration_seconds"] >= 0
        assert result.data["image_path"] == str(cloud_image)
        pdf_result_cache.clear()


class TestPdfListFigures:
    async def test_labeled_and_unlabeled_with_browser(self, artifacts_dir: Path) -> None:
        result = _base_result()
        result.images.append(ImageInfo("/tmp/logo.png", 0, (0, 0, 0, 0), caption=""))
        pdf = _seed(artifacts_dir, result)

        opened: dict[str, Any] = {}

        class FakeBrowser:
            async def open_local_file(self, path: str) -> dict[str, Any]:
                opened["path"] = path
                return {"success": True, "url": "file:///opened"}

        tool = PdfListFiguresTool(browser=FakeBrowser(), artifacts_dir=artifacts_dir)
        r = await tool.execute({"path": str(pdf)})
        assert r.success
        assert r.data["total_figures"] == 1
        assert r.data["unlabeled_image_count"] == 1
        assert r.data["browser_url"] == "file:///opened"


class TestPdfListSections:
    async def test_valid_and_invalid_levels(self, artifacts_dir: Path) -> None:
        result = _base_result()
        result.sections["2:Method"] = [TextBlock("m", 3, (0, 0, 0, 0), level=2)]
        result.sections["x:Bad"] = [TextBlock("b", 0, (0, 0, 0, 0))]
        pdf = _seed(artifacts_dir, result)
        tool = PdfListSectionsTool(artifacts_dir=artifacts_dir)
        r = await tool.execute({"path": str(pdf)})
        assert r.success
        # "x:Bad" is skipped (non-numeric level); only the two valid ones remain.
        titles = {s["title"] for s in r.data["sections"]}
        assert titles == {"Introduction", "Method"}


class _VisionPlanner:
    def __init__(self, works: bool = True, analysis: str = "A detailed figure.") -> None:
        self.vision_actually_works = works
        self._analysis = analysis
        self.last_call_metadata = {"total_tokens": 42}
        self.questions: list[str] = []

    async def analyze_image(self, img: Any, question: str) -> str:
        self.questions.append(question)
        return self._analysis


class TestPdfAnalyzeFigure:
    def _fig_result(self, artifacts_dir: Path, size: tuple[int, int]) -> Path:
        img_path = artifacts_dir / "fig1.png"
        Image.new("RGB", size, "blue").save(img_path)
        result = _base_result(fig_path=str(img_path))
        return _seed(artifacts_dir, result)

    async def test_success_with_vision(self, artifacts_dir: Path) -> None:
        pdf = self._fig_result(artifacts_dir, (150, 150))
        planner = _VisionPlanner()
        tool = PdfAnalyzeFigureTool(artifacts_dir=artifacts_dir, planner=planner)
        r = await tool.execute({"path": str(pdf), "figure_number_or_caption": "1"})
        assert r.success and r.data["found"] is True
        assert r.data["vision_analysis"] == "A detailed figure."
        assert "Source figure caption:\narchitecture overview" in planner.questions[0]
        assert r.data["vision_metadata"] == {"total_tokens": 42}
        assert r.data["vision_duration_seconds"] >= 0

    async def test_image_too_small(self, artifacts_dir: Path) -> None:
        pdf = self._fig_result(artifacts_dir, (20, 20))
        tool = PdfAnalyzeFigureTool(artifacts_dir=artifacts_dir, planner=_VisionPlanner())
        r = await tool.execute({"path": str(pdf), "figure_number_or_caption": "1"})
        assert r.success
        assert "resolution too low" in r.data["vision_analysis"]

    async def test_vision_unavailable(self, artifacts_dir: Path) -> None:
        pdf = self._fig_result(artifacts_dir, (150, 150))
        tool = PdfAnalyzeFigureTool(
            artifacts_dir=artifacts_dir, planner=_VisionPlanner(works=False)
        )
        r = await tool.execute({"path": str(pdf), "figure_number_or_caption": "1"})
        assert not r.success
        assert "Vision analysis failed or is not available" in r.error

    async def test_vision_reports_not_functioning(self, artifacts_dir: Path) -> None:
        pdf = self._fig_result(artifacts_dir, (150, 150))
        planner = _VisionPlanner(analysis="Sorry, the vision API is not functioning right now.")
        tool = PdfAnalyzeFigureTool(artifacts_dir=artifacts_dir, planner=planner)
        r = await tool.execute({"path": str(pdf), "figure_number_or_caption": "1"})
        assert not r.success

    async def test_analyze_image_raises(self, artifacts_dir: Path) -> None:
        pdf = self._fig_result(artifacts_dir, (150, 150))

        class RaisingPlanner:
            vision_actually_works = True

            async def analyze_image(self, img: Any, question: str) -> str:
                raise RuntimeError("api down")

        tool = PdfAnalyzeFigureTool(artifacts_dir=artifacts_dir, planner=RaisingPlanner())
        r = await tool.execute({"path": str(pdf), "figure_number_or_caption": "1"})
        assert not r.success
        assert "Vision analysis failed or is not available" in r.error

    async def test_figure_not_found(self, artifacts_dir: Path) -> None:
        pdf = self._fig_result(artifacts_dir, (150, 150))
        tool = PdfAnalyzeFigureTool(artifacts_dir=artifacts_dir, planner=_VisionPlanner())
        r = await tool.execute({"path": str(pdf), "figure_number_or_caption": "99"})
        assert r.success and r.data["found"] is False

    async def test_image_file_missing(self, artifacts_dir: Path) -> None:
        result = _base_result(fig_path="/nonexistent/fig1.png")
        pdf = _seed(artifacts_dir, result)
        tool = PdfAnalyzeFigureTool(artifacts_dir=artifacts_dir, planner=_VisionPlanner())
        r = await tool.execute({"path": str(pdf), "figure_number_or_caption": "1"})
        assert not r.success and "Image file not found" in r.error

    async def test_opens_in_browser(self, artifacts_dir: Path) -> None:
        pdf = self._fig_result(artifacts_dir, (150, 150))
        opened: dict[str, Any] = {}

        class FakeBrowser:
            async def open_local_file(self, path: str) -> dict[str, Any]:
                opened["path"] = path
                return {"success": True, "url": "file:///fig"}

        tool = PdfAnalyzeFigureTool(
            artifacts_dir=artifacts_dir, planner=_VisionPlanner(), browser=FakeBrowser()
        )
        r = await tool.execute({"path": str(pdf), "figure_number_or_caption": "1"})
        assert r.success and r.data["browser_url"] == "file:///fig"

    async def test_related_tables_surfaced_on_figure_page(self, artifacts_dir: Path) -> None:
        pdf = self._fig_result(artifacts_dir, (150, 150))
        result = pdf_result_cache[pdf_cache_key(pdf, artifacts_dir)]
        result.tables.append(
            TableInfo(
                "/tmp/t.html",
                0,
                (0, 0, 0, 0),
                caption="Table 1: benchmark",
                table_number="1",
                html_body=(
                    "<table><tr><th>Model</th><th>Score</th></tr>"
                    "<tr><td>Claude</td><td>83.4</td></tr></table>"
                ),
            )
        )
        tool = PdfAnalyzeFigureTool(artifacts_dir=artifacts_dir, planner=_VisionPlanner())
        r = await tool.execute({"path": str(pdf), "figure_number_or_caption": "1"})
        assert r.success
        assert len(r.data["related_tables"]) == 1
        assert r.data["related_tables"][0]["rows"] == [["Claude", "83.4"]]


class TestResolveFigure:
    def test_by_number(self) -> None:
        result = _base_result()
        assert _resolve_figure(result, "1") is result.images[0]

    def test_by_figure_prefix(self) -> None:
        result = _base_result()
        assert _resolve_figure(result, "Figure 1") is result.images[0]

    def test_by_caption_keyword(self) -> None:
        result = _base_result()
        assert _resolve_figure(result, "architecture") is result.images[0]

    def test_not_found(self) -> None:
        result = _base_result()
        assert _resolve_figure(result, "nonexistent-thing") is None

    def test_prefers_higher_resolution_when_number_duplicated(self, tmp_path: Path) -> None:
        result = _base_result()
        small = tmp_path / "small.jpg"
        large = tmp_path / "large.jpg"
        Image.new("RGB", (20, 20), "red").save(small)
        Image.new("RGB", (200, 200), "red").save(large)
        result.images = [
            ImageInfo(str(small), 0, (0, 0, 0, 0), caption="Figure 1: a", figure_number="1"),
            ImageInfo(str(large), 0, (0, 0, 0, 0), caption="Figure 1: b", figure_number="1"),
        ]
        assert _resolve_figure(result, "1").path == str(large)


class TestImageHelpers:
    def test_pick_higher_res_prefers_larger_sibling(self, tmp_path: Path) -> None:
        base = tmp_path / "fig.png"
        Image.new("RGB", (50, 50), "red").save(base)
        high = tmp_path / "fig_high.png"
        Image.new("RGB", (400, 400), "red").save(high)
        # The larger sibling must be picked when it is bigger on disk.
        assert _pick_higher_res_image(base) == high

    def test_pick_higher_res_missing_returns_input(self, tmp_path: Path) -> None:
        missing = tmp_path / "gone.png"
        assert _pick_higher_res_image(missing) == missing

    def test_open_image_failure_returns_none(self, tmp_path: Path) -> None:
        bad = tmp_path / "notimage.png"
        bad.write_text("not an image")
        assert _open_image(bad) is None
