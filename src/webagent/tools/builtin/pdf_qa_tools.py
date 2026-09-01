"""PDF Question-Answering tools with context-aware retrieval.

This module provides intelligent Q&A capabilities for PDF documents by:
1. Parsing PDFs through the configured provider cascade
2. Implementing semantic search across document sections
3. Retrieving relevant context based on user questions
4. Supporting both text-only and vision-enhanced Q&A

Retrieval scoring and context assembly live in ``_pdf_retrieval``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from webagent.core.models import ToolResult
from webagent.parser import ImageInfo, PDFParseResult, find_images_by_keyword
from webagent.tools.builtin._pdf_analysis import parse_table_html
from webagent.tools.builtin._pdf_common import PdfToolBase, load_pdf_result
from webagent.tools.builtin._pdf_retrieval import (
    figure_sort_key as _figure_sort_key,
)
from webagent.tools.builtin._pdf_retrieval import (
    retrieve_relevant_sections as _retrieve_relevant_sections,
)
from webagent.tools.registry import tool
from webagent.utils.paths import get_pdf_extract_dir
from webagent.utils.paths import resolve_pdf_path as _resolve_pdf_path
from webagent.utils.pdf_figures import detect_and_render_local_figure

logger = logging.getLogger("webagent.pdf_qa")


@tool(
    "pdf_qa",
    "Ask a question about a PDF document. Retrieves relevant content and provides context. "
    "Use this for Q&A on academic papers, reports, or any PDF. "
    "params: path (string), question (string)",
)
class PdfQATool(PdfToolBase):
    """Intelligent PDF Q&A with context-aware retrieval."""

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.browser = browser

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required - PDF file path")
        if not isinstance(params.get("question"), str) or not params["question"].strip():
            raise ValueError("'question' required - Your question about the PDF")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        path_str = params["path"].strip()
        question = params["question"].strip()

        # Use intelligent path resolution with fallback
        path, was_fallback, warning = _resolve_pdf_path(
            path_str, self.artifacts_dir, use_fallback=True
        )

        if was_fallback:
            logger.info("Fallback used for pdf_qa: %s", warning)

        if warning and not was_fallback:
            return ToolResult(success=False, tool_name="pdf_qa", error=warning)

        if not path.exists():
            return ToolResult(
                success=False,
                tool_name="pdf_qa",
                error=warning or f"PDF not found: {path}",
            )

        result, parse_error = await load_pdf_result(
            path, self.artifacts_dir, "pdf_qa", config=self.config
        )
        if parse_error:
            return parse_error
        assert result is not None

        # Retrieve relevant sections
        retrieval = _retrieve_relevant_sections(result, question)

        # Build response
        data = {
            "question": question,
            "context": retrieval["context"],
            "context_length": len(retrieval["context"]),
            "sources": retrieval["sources"],
            "found_figures": retrieval["found_figures"],
            "found_tables": retrieval["found_tables"],
            "markdown_path": result.markdown_path,
            "total_images": len(result.images),
            "total_tables": len(result.tables),
            "total_sections": len(result.sections),
        }

        # Add hints for the LLM
        hints = []
        if retrieval["found_figures"]:
            hints.append(
                f"Found {len(retrieval['found_figures'])} relevant figure(s) with captions."
            )
        if retrieval["found_tables"]:
            hints.append(f"Found {len(retrieval['found_tables'])} relevant table(s).")
        if not retrieval["context"]:
            hints.append("No directly relevant text found. Try rephrasing your question.")

        if hints:
            data["hints"] = " ".join(hints)

        return ToolResult(
            success=True,
            tool_name="pdf_qa",
            data=data,
        )


@tool(
    "pdf_search",
    "Search for content within a PDF document using semantic matching. "
    "Returns relevant text chunks with sources. "
    "params: path (string), query (string), max_results? (number, default 5)",
)
class PdfSearchTool(PdfToolBase):
    """Semantic search within PDF documents."""

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.browser = browser

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")
        if not isinstance(params.get("query"), str) or not params["query"].strip():
            raise ValueError("'query' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        query = params["query"].strip()
        max_results = params.get("max_results", 5)

        result, error = await self._load_pdf(params, "pdf_search")
        if error:
            return error
        assert result is not None

        # Retrieve relevant sections
        retrieval = _retrieve_relevant_sections(result, query, max_chunks=max_results)

        # Build results with scores
        results = []
        for i, source in enumerate(retrieval["sources"]):
            results.append(
                {
                    "rank": i + 1,
                    "page": source.get("page"),
                    "type": source.get("type"),
                    "preview": source.get("preview", ""),
                }
            )

        return ToolResult(
            success=True,
            tool_name="pdf_search",
            data={
                "query": query,
                "results": results,
                "context": retrieval["context"],
                "found_figures": retrieval["found_figures"],
                "found_tables": retrieval["found_tables"],
            },
        )


@tool(
    "pdf_list_figures",
    "List the numbered figures in a PDF (Figure 1, Figure 2, ...) with captions and pages, "
    "sorted by figure number. 'figures' holds real captioned figures; uncaptioned logos/"
    "decorations are kept separately in 'unlabeled_images' and are NOT numbered figures. "
    "To analyze 'Figure N', prefer pdf_analyze_figure with that number. params: path (string)",
)
class PdfListFiguresTool(PdfToolBase):
    """List all figures in a PDF document."""

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.browser = browser

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result, error = await self._load_pdf(params, "pdf_list_figures")
        if error:
            return error
        assert result is not None

        # Separate real captioned figures (e.g. "Figure 1: ...") from uncaptioned
        # images (logos, cover art, decorations). Without this split, an
        # uncaptioned cover logo listed first is easily mistaken for "Figure 1".
        labeled = sorted(
            (img for img in result.images if img.figure_number),
            key=lambda im: _figure_sort_key(im.figure_number),
        )
        unlabeled = [img for img in result.images if not img.figure_number]

        figures = [
            {
                "figure_number": img.figure_number,
                "page": img.page_idx + 1,
                "caption": img.caption,
                "path": img.path,
            }
            for img in labeled
        ]
        unlabeled_images = [
            {
                "page": img.page_idx + 1,
                "path": img.path,
                "note": "unlabeled image (no figure caption; likely a logo or decoration, not a numbered figure)",
            }
            for img in unlabeled
        ]

        browser_url = await self._open_first_figure(figures or unlabeled_images)
        return ToolResult(
            success=True,
            tool_name="pdf_list_figures",
            data={
                "total_figures": len(figures),
                "figures": figures,
                "unlabeled_image_count": len(unlabeled_images),
                "unlabeled_images": unlabeled_images,
                "browser_url": browser_url,
            },
        )

    async def _open_first_figure(self, figures: list[dict[str, Any]]) -> str | None:
        if not self.browser or not figures:
            return None
        first_path = figures[0].get("path")
        if not first_path:
            return None
        try:
            result = await self.browser.open_local_file(str(first_path))
            if result.get("success"):
                return str(result.get("url") or "")
        except Exception:
            return None
        return None


@tool(
    "pdf_list_tables",
    "List all tables in a PDF with their captions and page numbers. "
    "Use this to get an overview of tabular data. "
    "params: path (string)",
)
class PdfListTablesTool(PdfToolBase):
    """List all tables in a PDF document."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result, error = await self._load_pdf(params, "pdf_list_tables")
        if error:
            return error
        assert result is not None

        # Build table list
        tables = []
        for i, table in enumerate(result.tables, 1):
            tables.append(
                {
                    "index": i,
                    "table_number": table.table_number or f"table_{i}",
                    "page": table.page_idx + 1,
                    "caption": table.caption,
                    "html_body": table.html_body[:200] + "..."
                    if len(table.html_body) > 200
                    else table.html_body,
                    "path": table.path,
                }
            )

        return ToolResult(
            success=True,
            tool_name="pdf_list_tables",
            data={
                "total_tables": len(tables),
                "tables": tables,
            },
        )


@tool(
    "pdf_list_sections",
    "List all sections in a PDF with their hierarchy and page information. "
    "Use this to navigate the document structure. "
    "params: path (string)",
)
class PdfListSectionsTool(PdfToolBase):
    """List all sections in a PDF document."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result, error = await self._load_pdf(params, "pdf_list_sections")
        if error:
            return error
        assert result is not None

        # Build section list
        sections = []
        for section_key, blocks in result.sections.items():
            if ":" in section_key:
                level_str, title = section_key.split(":", 1)
                try:
                    level = int(level_str)
                    # Get the page from the first block in this section
                    page = blocks[0].page_idx + 1 if blocks else None
                    block_count = len(blocks)

                    sections.append(
                        {
                            "title": title,
                            "level": level,
                            "page": page,
                            "block_count": block_count,
                        }
                    )
                except ValueError:
                    pass

        # Sort by level and page
        sections.sort(key=lambda s: (s["level"], s["page"] or 0))

        return ToolResult(
            success=True,
            tool_name="pdf_list_sections",
            data={
                "total_sections": len(sections),
                "sections": sections,
            },
        )


@tool(
    "pdf_analyze_figure",
    "Analyze a specific NUMBERED figure (e.g. Figure 1) from a PDF using vision. "
    "First tries a conservative local vector/raster render, then falls back to the structured "
    "parser when the caption-to-graphic match is ambiguous. Resolves the figure by its "
    "number/caption and analyzes the "
    "correct image automatically — prefer this over manually picking an image path when the "
    "task names a figure number. It parses and caches the PDF itself: call it directly after "
    "download_pdf; do not call pdf_parse/pdf_list_figures first unless their separate output is needed. "
    "params: path (string - PDF path), figure_number_or_caption (string, e.g. '1'), question? (string)",
)
class PdfAnalyzeFigureTool(PdfToolBase):
    """Analyze a figure using vision capabilities."""

    def __init__(self, browser: Any = None, planner: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.browser = browser
        self._planner = planner  # Store planner for vision analysis

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")
        if (
            not isinstance(params.get("figure_number_or_caption"), str)
            or not params["figure_number_or_caption"].strip()
        ):
            raise ValueError("'figure_number_or_caption' required - e.g., '1' or 'architecture'")

    async def _try_local_figure(
        self,
        path: Path,
        figure_ref: str,
    ) -> tuple[ImageInfo | None, dict[str, Any]]:
        metadata: dict[str, Any] = {"used": False}
        exact_number = _exact_figure_number(figure_ref)
        if not exact_number or not bool(getattr(self.config, "local_figure_fast_path", False)):
            return None, metadata
        started = time.monotonic()
        rendered = await asyncio.to_thread(
            detect_and_render_local_figure,
            path,
            exact_number,
            get_pdf_extract_dir(self.artifacts_dir, path) / "figures" / "local",
            dpi=int(getattr(self.config, "local_figure_render_dpi", 144)),
            min_confidence=float(getattr(self.config, "local_figure_min_confidence", 0.9)),
        )
        metadata["duration_seconds"] = time.monotonic() - started
        if rendered is None:
            return None, metadata
        region = rendered.region
        metadata.update(
            {
                "used": True,
                "confidence": region.confidence,
                "caption_position": region.caption_position,
                "visual_kind": region.visual_kind,
                "bbox": list(region.bbox),
                "render_width": rendered.width,
                "render_height": rendered.height,
            }
        )
        return (
            ImageInfo(
                path=str(rendered.image_path),
                page_idx=region.page_idx,
                bbox=(
                    round(region.bbox[0]),
                    round(region.bbox[1]),
                    round(region.bbox[2]),
                    round(region.bbox[3]),
                ),
                caption=region.caption,
                figure_number=region.figure_number,
            ),
            metadata,
        )

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        figure_ref = params["figure_number_or_caption"].strip()
        question = params.get("question", "Describe this figure in detail.").strip()

        path, error = self._resolve_pdf(params, "pdf_analyze_figure")
        if error:
            return error
        assert path is not None

        result: PDFParseResult | None = None
        target_figure, local_metadata = await self._try_local_figure(path, figure_ref)

        if target_figure is None:
            result, error = await load_pdf_result(
                path,
                self.artifacts_dir,
                "pdf_analyze_figure",
                config=self.config,
            )
            if error:
                return error
            assert result is not None

            # Find the figure — resolve "Figure 1", "fig 1", "1", "1a" to a number
            # and match it against parsed figure numbers (NOT extraction order).
            target_figure = _resolve_figure(result, figure_ref)

        if not target_figure:
            return ToolResult(
                success=True,
                tool_name="pdf_analyze_figure",
                data={
                    "found": False,
                    "message": f"Figure '{figure_ref}' not found. Use pdf_list_figures to see available figures.",
                    "available_figures": len(result.images) if result is not None else 0,
                    "local_figure_fast_path": local_metadata,
                },
            )

        # Check if image file exists
        img_path = _pick_higher_res_image(Path(target_figure.path))
        if not img_path.exists():
            return ToolResult(
                success=False,
                tool_name="pdf_analyze_figure",
                error=f"Image file not found: {img_path}",
            )

        # Open the image locally (best effort) for vision analysis
        pil_img = _open_image(img_path)

        # Use browser to view the image
        browser_url = None
        if self.browser:
            open_result = await self.browser.open_local_file(str(img_path))
            if open_result.get("success"):
                browser_url = open_result.get("url")

        caption = target_figure.caption or "(no source caption extracted)"
        vision_question = f"Source figure caption:\n{caption}\n\nRequested analysis:\n{question}"
        vision_started = time.monotonic()
        vision_unavailable, vision_analysis, vision_metadata = await self._analyze_with_vision(
            pil_img, vision_question
        )
        vision_duration = time.monotonic() - vision_started

        fig_page = target_figure.page_idx + 1

        if vision_unavailable:
            return ToolResult(
                success=False,
                tool_name="pdf_analyze_figure",
                error=(
                    f"Vision analysis failed or is not available for Figure "
                    f"{target_figure.figure_number}. "
                    f"The figure is on page {fig_page} with caption: '{target_figure.caption}'. "
                    f"Use 'pdf_extract_text' with pages={fig_page}-{fig_page + 1} "
                    f"to read the surrounding text and interpret the figure from its textual description. "
                    f"Then use 'done' to report your findings."
                ),
            )

        return ToolResult(
            success=True,
            tool_name="pdf_analyze_figure",
            data={
                "found": True,
                "figure_number": target_figure.figure_number,
                "page": fig_page,
                "caption": target_figure.caption,
                "image_path": str(img_path),
                "browser_url": browser_url,
                "vision_analysis": vision_analysis,
                "vision_duration_seconds": vision_duration,
                "vision_metadata": vision_metadata,
                "local_figure_fast_path": local_metadata,
                "related_tables": (
                    _tables_on_page(result, target_figure.page_idx) if result is not None else []
                ),
            },
        )

    async def _analyze_with_vision(
        self, pil_img: Any, question: str
    ) -> tuple[bool, str | None, dict[str, Any]]:
        """Run vision analysis and return availability, answer, and call metadata."""
        vision_analysis: str | None = None
        vision_metadata: dict[str, Any] = {}

        # Check if vision is available before attempting analysis
        vision_unavailable = bool(
            not pil_img
            or not self._planner
            or not hasattr(self._planner, "analyze_image")
            or (
                hasattr(self._planner, "vision_actually_works")
                and not self._planner.vision_actually_works
            )
        )

        # Perform vision analysis if planner supports it and image is large enough
        if (
            not vision_unavailable
            and pil_img
            and self._planner
            and hasattr(self._planner, "analyze_image")
        ):
            # Check if image is too small for meaningful analysis
            if pil_img.width < 100 or pil_img.height < 100:
                logger.warning("Image too small for analysis: %dx%d", pil_img.width, pil_img.height)
                vision_analysis = (
                    f"Image resolution too low for detailed analysis. "
                    f"The extracted figure is only {pil_img.width}x{pil_img.height} pixels."
                )
            else:
                try:
                    vision_analysis = await self._planner.analyze_image(pil_img, question)
                    raw_metadata = getattr(self._planner, "last_call_metadata", {})
                    if isinstance(raw_metadata, dict):
                        vision_metadata = raw_metadata
                except Exception as e:
                    logger.warning("Vision analysis failed: %s", e)
                    vision_unavailable = True

            # Check if the vision analysis indicates failure
            if vision_analysis and "vision api is not functioning" in vision_analysis.lower():
                vision_unavailable = True
                vision_analysis = None
            elif not vision_analysis:
                vision_unavailable = True

        return vision_unavailable, vision_analysis, vision_metadata


def _exact_figure_number(figure_ref: str) -> str:
    """Return a number only when the whole reference names one exact figure."""
    match = re.fullmatch(
        r"\s*(?:(?:figure|fig\.?)\s*)?(\d+[a-z]?)\s*",
        figure_ref,
        re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _resolve_figure(result: PDFParseResult, figure_ref: str) -> ImageInfo | None:
    """Resolve a figure reference to a parsed image.

    Tries, in order: "Figure 1"/"fig 1"/"1"/"1a" resolved to a figure number,
    then caption keyword match (e.g. figure_ref='architecture'). When several
    images share the same figure number (e.g. a mislabelled cover logo), the
    highest-resolution one is returned rather than blindly the first in
    extraction order.
    """
    ref = figure_ref.strip()
    num = ref if ref.isdigit() else ""
    if not num:
        match = re.search(r"(\d+[a-z]?)", ref, re.IGNORECASE)
        num = match.group(1) if match else ""
    if num:
        by_number = [img for img in result.images if img.figure_number == num]
        if by_number:
            return max(by_number, key=lambda im: _image_pixel_area(im.path))

    matching = find_images_by_keyword(result, figure_ref, case_sensitive=False)
    return matching[0] if matching else None


def _image_pixel_area(path: str) -> int:
    """Return width*height for an image file, or 0 if it can't be read."""
    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            return int(im.width) * int(im.height)
    except Exception:
        return 0


def _tables_on_page(result: PDFParseResult, page_idx: int) -> list[dict[str, Any]]:
    """Return structured table data for tables on a given page.

    Surfacing the extracted table next to the vision analysis of a benchmark
    chart gives the planner ground-truth numbers to cross-check the vision
    model's reading (a highlighted bar is not necessarily the tallest one).
    """
    out: list[dict[str, Any]] = []
    for table in result.tables:
        if table.page_idx != page_idx:
            continue
        parsed = parse_table_html(table.html_body or "")
        out.append(
            {
                "table_number": table.table_number,
                "caption": table.caption,
                "page": table.page_idx + 1,
                "headers": parsed["headers"],
                "rows": parsed["rows"],
            }
        )
    return out


def _pick_higher_res_image(img_path: Path) -> Path:
    """Prefer a higher-resolution sibling of the same image if one exists.

    MinerU sometimes saves multiple sizes (``_high``, ``_full``, ``_original``).
    """
    if not img_path.exists():
        return img_path
    possible_sizes = ["", "_high", "_full", "_original"]
    for suffix in possible_sizes:
        larger_path = img_path.parent / f"{img_path.stem}{suffix}.png"
        if larger_path.exists() and larger_path.stat().st_size > img_path.stat().st_size:
            return larger_path
    return img_path


def _open_image(img_path: Path) -> Any:
    """Open an image with PIL, returning None (and logging) on failure."""
    from PIL import Image as PILImage

    try:
        pil_img = PILImage.open(img_path)
        logger.info("Vision: opened image %s, size=%dx%d", img_path, pil_img.width, pil_img.height)
        return pil_img
    except Exception as e:
        logger.warning("Failed to open image: %s", e)
        return None
