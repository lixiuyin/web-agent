"""PDF Question-Answering tools with context-aware retrieval.

This module provides intelligent Q&A capabilities for PDF documents by:
1. Parsing PDF with MinerU to get structured content
2. Implementing semantic search across document sections
3. Retrieving relevant context based on user questions
4. Supporting both text-only and vision-enhanced Q&A
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from webagent.core.models import ToolResult
from webagent.tools.registry import tool
from webagent.utils.chandra_pdf import (
    ImageInfo,
    PDFParseResult,
    TableInfo,
    TextBlock,
    find_images_by_keyword,
    find_tables_by_keyword,
    parse_pdf_with_chandra,
)
from webagent.utils.paths import get_artifacts_dir, get_pdf_extract_dir
from webagent.utils.paths import resolve_pdf_path as _resolve_pdf_path

if TYPE_CHECKING:
    from webagent.core.config import AgentConfig

logger = logging.getLogger("webagent.pdf_qa")


def _figure_sort_key(figure_number: str) -> tuple[int, str]:
    """Order figures by their number (``"1"`` < ``"2"`` < ``"3a"`` < ``"3b"``)."""
    match = re.match(r"(\d+)([a-z]?)", figure_number.strip(), re.IGNORECASE)
    if not match:
        return (10**9, figure_number)
    return (int(match.group(1)), match.group(2).lower())


class _PdfResultCache(dict[str, PDFParseResult]):
    """PDF parse cache that refuses to store degraded results.

    Errored parses and the local PyMuPDF fallback (used only when every cloud
    provider was unavailable) are never cached, so a later run with the cloud
    cascade reachable isn't served stale, lower-quality text. Single-point guard
    covering every ``_pdf_cache[key] = result`` site across the PDF tools.
    """

    _DEGRADED_BACKENDS = frozenset({"pymupdf", "local"})

    def __setitem__(self, key: str, value: PDFParseResult) -> None:
        if value.error or value.backend in self._DEGRADED_BACKENDS:
            return
        super().__setitem__(key, value)


# Global cache for parsed PDF results to enable multi-turn conversations
_pdf_cache: dict[str, PDFParseResult] = _PdfResultCache()


def _get_cache_key(path: Path) -> str:
    """Generate a cache key for a PDF path."""
    return str(path.resolve())


def _resolve_pdf_input(
    path_str: str, artifacts_dir: Path, tool_name: str
) -> tuple[Path | None, ToolResult | None]:
    """Resolve a PDF input path and reject reads outside the current output root."""
    path, _was_fallback, error = _resolve_pdf_path(path_str, artifacts_dir, use_fallback=False)
    if error:
        return None, ToolResult(success=False, tool_name=tool_name, error=error)
    return path, None


def _split_text_into_chunks(text: str, max_chars: int = 1000, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks for semantic search.

    Args:
        text: The text to split
        max_chars: Maximum characters per chunk
        overlap: Number of overlapping characters between chunks

    Returns:
        List of text chunks
    """
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        # Try to break at a sentence boundary
        if end < len(text):
            # Look for sentence endings near the end
            for sep in [". ", "\n", "! ", "? "]:
                last_sep = text.rfind(sep, start + max_chars // 2, end + 50)
                if last_sep > start + max_chars // 2:
                    end = last_sep + len(sep)
                    break

        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else len(text)

    return [c for c in chunks if c]


def _compute_relevance_score(chunk: str, query: str) -> float:
    """Compute a simple relevance score between chunk and query.

    Uses keyword matching with position weighting for better results.
    """
    query_lower = query.lower()
    chunk_lower = chunk.lower()

    # Extract key terms from query (remove common words)
    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "can",
        "what",
        "which",
        "where",
        "when",
        "how",
        "who",
        "why",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "about",
        "and",
        "or",
        "but",
        "not",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "pdf",
        "document",
        "paper",
    }

    query_words = [w for w in re.findall(r"\w+", query_lower) if w not in stop_words and len(w) > 2]

    if not query_words:
        return 0.0

    score = 0.0
    for word in query_words:
        # Exact phrase match gets highest score
        if word in chunk_lower:
            # Count occurrences with position weighting (earlier = higher)
            occurrences = chunk_lower.count(word)
            first_pos = chunk_lower.find(word)
            pos_weight = 1.0 - (first_pos / len(chunk_lower)) * 0.3  # Up to 30% reduction
            score += occurrences * pos_weight

    # Bonus for partial phrase matches (2+ consecutive words)
    query_bigrams = [" ".join(query_words[i : i + 2]) for i in range(len(query_words) - 1)]
    for bigram in query_bigrams:
        if bigram in chunk_lower:
            score += 2.0

    return score


def _retrieve_relevant_sections(
    result: PDFParseResult,
    query: str,
    max_chunks: int = 5,
    max_context_chars: int = 3000,
) -> dict[str, Any]:
    """Retrieve the most relevant sections from the document for a query.

    Args:
        result: Parsed MinerU result
        query: User's question
        max_chunks: Maximum number of chunks to retrieve
        max_context_chars: Maximum total characters to return

    Returns:
        Dictionary with relevant context and metadata
    """
    # Build full text from text blocks
    all_text_blocks: list[tuple[str, TextBlock]] = []
    for block in result.text_blocks:
        all_text_blocks.append((block.text, block))

    # Initialize scored list
    scored: list[tuple[str, TextBlock | None, float]] = []

    if not all_text_blocks:
        # Fallback to markdown
        md_path = result.markdown_path
        if md_path and Path(md_path).exists():
            text = Path(md_path).read_text(encoding="utf-8", errors="replace")
            chunks = _split_text_into_chunks(text, max_chars=800, overlap=50)
            scored = [(chunk, None, _compute_relevance_score(chunk, query)) for chunk in chunks]
        else:
            return {"context": "", "sources": [], "found_figures": [], "found_tables": []}
    else:
        # Score text blocks by relevance
        for block_text, block in all_text_blocks:
            score = _compute_relevance_score(block_text, query)
            if score > 0:
                scored.append((block_text, block, score))

    # Sort by relevance score
    scored.sort(key=lambda x: x[2], reverse=True)

    # Take top chunks
    top_chunks = scored[:max_chunks]

    # Build context with character limit
    context_parts = []
    sources = []
    total_chars = 0

    for chunk_text, block, _score in top_chunks:  # type: ignore[assignment]
        if total_chars >= max_context_chars:
            break

        # Truncate if needed
        remaining = max_context_chars - total_chars
        if len(chunk_text) > remaining:
            chunk_text = chunk_text[:remaining] + "..."

        context_parts.append(chunk_text)
        total_chars += len(chunk_text)

        # Track source info
        if block:
            sources.append(
                {
                    "type": "text_block",
                    "page": block.page_idx + 1,
                    "level": block.level,
                    "preview": block.text[:100] + "..." if len(block.text) > 100 else block.text,
                }
            )

    context = "\n\n---\n\n".join(context_parts)

    # Check for relevant figures and tables
    query_lower = query.lower()
    found_figures: list[dict[str, Any]] = []
    found_tables: list[dict[str, Any]] = []

    # Look for figure mentions
    figure_keywords = ["figure", "chart", "graph", "plot", "diagram", "image"]
    if any(kw in query_lower for kw in figure_keywords):
        # Try to find by number mentioned in query
        fig_match = re.search(r"figure\s+(\d+)", query_lower, re.IGNORECASE)
        if fig_match:
            fig_num = fig_match.group(1)
            for img in result.images:
                if img.figure_number == fig_num or str(fig_num) in img.caption.lower():
                    found_figures.append(
                        {
                            "path": img.path,
                            "page": img.page_idx + 1,
                            "caption": img.caption,
                            "figure_number": img.figure_number,
                        }
                    )
        else:
            # Search by keyword in captions
            search_kw = query_lower.split()[-1] if query_lower.split() else ""
            if search_kw and len(search_kw) > 3:
                matching_images: list[ImageInfo] = find_images_by_keyword(
                    result, search_kw, case_sensitive=False
                )
                found_figures = [
                    {
                        "path": img.path,
                        "page": img.page_idx + 1,
                        "caption": img.caption,
                        "figure_number": img.figure_number,
                    }
                    for img in matching_images[:3]
                ]

    # Look for table mentions
    table_keywords = ["table", "data", "comparison", "results"]
    if any(kw in query_lower for kw in table_keywords):
        table_match = re.search(r"table\s+(\d+)", query_lower, re.IGNORECASE)
        if table_match:
            table_num = table_match.group(1)
            for table in result.tables:
                if table.table_number == table_num or str(table_num) in table.caption.lower():
                    found_tables.append(
                        {
                            "path": table.path,
                            "page": table.page_idx + 1,
                            "caption": table.caption,
                            "table_number": table.table_number,
                            "html_body": table.html_body[:500] + "..."
                            if len(table.html_body) > 500
                            else table.html_body,
                        }
                    )
        else:
            # Search by keyword
            search_kw = query_lower.split()[-1] if query_lower.split() else ""
            if search_kw and len(search_kw) > 3:
                matching_tables: list[TableInfo] = find_tables_by_keyword(
                    result, search_kw, case_sensitive=False
                )
                found_tables = [
                    {
                        "path": table.path,
                        "page": table.page_idx + 1,
                        "caption": table.caption,
                        "table_number": table.table_number,
                        "html_body": table.html_body[:500] + "..."
                        if len(table.html_body) > 500
                        else table.html_body,
                    }
                    for table in matching_tables[:3]
                ]

    return {
        "context": context,
        "sources": sources,
        "found_figures": found_figures,
        "found_tables": found_tables,
    }


@tool(
    "pdf_qa",
    "Ask a question about a PDF document. Retrieves relevant content and provides context. "
    "Use this for Q&A on academic papers, reports, or any PDF. "
    "params: path (string), question (string)",
)
class PdfQATool:
    """Intelligent PDF Q&A with context-aware retrieval."""

    def __init__(
        self,
        browser: Any = None,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.browser = browser
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)

    def validate_params(self, params: dict) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required - PDF file path")
        if not isinstance(params.get("question"), str) or not params["question"].strip():
            raise ValueError("'question' required - Your question about the PDF")

    async def execute(self, params: dict) -> ToolResult:
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

        # Check cache first
        cache_key = _get_cache_key(path)
        result = _pdf_cache.get(cache_key)

        if result is None or result.error:
            # Parse the PDF
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[cache_key] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_qa",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_qa",
                error=result.error,
            )

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
class PdfSearchTool:
    """Semantic search within PDF documents."""

    def __init__(
        self,
        browser: Any = None,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.browser = browser
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)

    def validate_params(self, params: dict) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")
        if not isinstance(params.get("query"), str) or not params["query"].strip():
            raise ValueError("'query' required")

    async def execute(self, params: dict) -> ToolResult:
        path_str = params["path"].strip()
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_search")
        if path_error:
            return path_error
        assert path is not None

        query = params["query"].strip()
        max_results = params.get("max_results", 5)

        # Check cache
        cache_key = _get_cache_key(path)
        result = _pdf_cache.get(cache_key)

        if result is None or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[cache_key] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_search",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_search",
                error=result.error,
            )

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
class PdfListFiguresTool:
    """List all figures in a PDF document."""

    def __init__(
        self,
        browser: Any = None,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.browser = browser
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)

    def validate_params(self, params: dict) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict) -> ToolResult:
        path_str = params["path"].strip()
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_list_figures")
        if path_error:
            return path_error
        assert path is not None

        # Check cache
        cache_key = _get_cache_key(path)
        result = _pdf_cache.get(cache_key)

        if result is None or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[cache_key] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_list_figures",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_list_figures",
                error=result.error,
            )

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

    async def _open_first_figure(self, figures: list[dict]) -> str | None:
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
class PdfListTablesTool:
    """List all tables in a PDF document."""

    def __init__(
        self,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)

    def validate_params(self, params: dict) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict) -> ToolResult:
        path_str = params["path"].strip()
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_list_tables")
        if path_error:
            return path_error
        assert path is not None

        # Check cache
        cache_key = _get_cache_key(path)
        result = _pdf_cache.get(cache_key)

        if result is None or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[cache_key] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_list_tables",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_list_tables",
                error=result.error,
            )

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
class PdfListSectionsTool:
    """List all sections in a PDF document."""

    def __init__(
        self,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)

    def validate_params(self, params: dict) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict) -> ToolResult:
        path_str = params["path"].strip()
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_list_sections")
        if path_error:
            return path_error
        assert path is not None

        # Check cache
        cache_key = _get_cache_key(path)
        result = _pdf_cache.get(cache_key)

        if result is None or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[cache_key] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_list_sections",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_list_sections",
                error=result.error,
            )

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
    "Resolves the figure by its number/caption from the parsed content and analyzes the "
    "correct image automatically — prefer this over manually picking an image path when the "
    "task names a figure number. "
    "params: path (string - PDF path), figure_number_or_caption (string, e.g. '1'), question? (string)",
)
class PdfAnalyzeFigureTool:
    """Analyze a figure using vision capabilities."""

    def __init__(
        self,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        browser: Any = None,
        planner: Any = None,
        **kw: Any,
    ) -> None:
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)
        self.browser = browser
        self._planner = planner  # Store planner for vision analysis

    def validate_params(self, params: dict) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")
        if (
            not isinstance(params.get("figure_number_or_caption"), str)
            or not params["figure_number_or_caption"].strip()
        ):
            raise ValueError("'figure_number_or_caption' required - e.g., '1' or 'architecture'")

    async def execute(self, params: dict) -> ToolResult:
        path_str = params["path"].strip()
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_analyze_figure")
        if path_error:
            return path_error
        assert path is not None

        figure_ref = params["figure_number_or_caption"].strip()
        question = params.get("question", "Describe this figure in detail.").strip()

        # Check cache
        cache_key = _get_cache_key(path)
        result = _pdf_cache.get(cache_key)

        if result is None or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[cache_key] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_analyze_figure",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_analyze_figure",
                error=result.error,
            )

        # Find the figure — resolve "Figure 1", "fig 1", "1", "1a" to a number
        # and match it against the parsed figure numbers (NOT extraction order).
        target_figure = None
        ref = figure_ref.strip()
        num = ref if ref.isdigit() else ""
        if not num:
            match = re.search(r"(\d+[a-z]?)", ref, re.IGNORECASE)
            num = match.group(1) if match else ""
        if num:
            target_figure = next((img for img in result.images if img.figure_number == num), None)

        # Fall back to caption keyword match (e.g. figure_ref='architecture').
        if not target_figure:
            matching = find_images_by_keyword(result, figure_ref, case_sensitive=False)
            if matching:
                target_figure = matching[0]

        if not target_figure:
            return ToolResult(
                success=True,
                tool_name="pdf_analyze_figure",
                data={
                    "found": False,
                    "message": f"Figure '{figure_ref}' not found. Use pdf_list_figures to see available figures.",
                    "available_figures": len(result.images),
                },
            )

        # Check if image file exists
        img_path = Path(target_figure.path)
        if not img_path.exists():
            return ToolResult(
                success=False,
                tool_name="pdf_analyze_figure",
                error=f"Image file not found: {img_path}",
            )

        # Try to open in browser for vision analysis
        browser_url = None
        vision_analysis = None
        pil_img = None

        # Try to get a better image: check for higher-res version in same directory
        img_dir = img_path.parent
        img_stem = img_path.stem

        # Look for larger version (sometimes MinerU saves multiple sizes)
        possible_sizes = ["", "_high", "_full", "_original"]
        for suffix in possible_sizes:
            larger_path = img_dir / f"{img_stem}{suffix}.png"
            if larger_path.exists() and larger_path.stat().st_size > img_path.stat().st_size:
                img_path = larger_path
                break

        # Open image
        try:
            from PIL import Image as PILImage

            pil_img = PILImage.open(img_path)
            logger.info(
                "Vision: opened image %s, size=%dx%d", img_path, pil_img.width, pil_img.height
            )
        except Exception as e:
            logger.warning("Failed to open image: %s", e)

        # Use browser to view the image
        if self.browser:
            open_result = await self.browser.open_local_file(str(img_path))
            if open_result.get("success"):
                browser_url = open_result.get("url")

        # Check if vision is available before attempting analysis
        vision_unavailable = bool(
            self._planner
            and hasattr(self._planner, "vision_actually_works")
            and not self._planner.vision_actually_works
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
                vision_analysis = f"Image resolution too low for detailed analysis. The extracted figure is only {pil_img.width}x{pil_img.height} pixels."
            else:
                try:
                    vision_analysis = await self._planner.analyze_image(pil_img, question)
                except Exception as e:
                    logger.warning("Vision analysis failed: %s", e)

            # Check if the vision analysis indicates failure
            if vision_analysis and "vision api is not functioning" in vision_analysis.lower():
                vision_unavailable = True
                vision_analysis = None

        fig_page = target_figure.page_idx + 1

        if vision_unavailable:
            return ToolResult(
                success=False,
                tool_name="pdf_analyze_figure",
                error=(
                    f"Vision API is not available to analyze Figure {target_figure.figure_number}. "
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
            },
        )


def clear_pdf_cache(path: Path | None = None) -> None:
    """Clear the PDF cache.

    Args:
        path: If provided, only clear cache for this specific PDF.
    """
    if path:
        cache_key = _get_cache_key(path)
        _pdf_cache.pop(cache_key, None)
    else:
        _pdf_cache.clear()


def get_cached_pdf_result(path: Path) -> PDFParseResult | None:
    """Get a cached PDF result if available.

    Args:
        path: Path to the PDF file.

    Returns:
        Cached PDFParseResult or None.
    """
    cache_key = _get_cache_key(path)
    return _pdf_cache.get(cache_key)
