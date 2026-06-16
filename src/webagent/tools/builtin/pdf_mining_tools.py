"""Advanced PDF content mining tools that leverage the parser cascade's structured extraction.

This module provides deep analysis capabilities for PDF documents by:
1. Cross-reference analysis - linking figures/tables to their mentions in text
2. Table data extraction - parsing HTML tables into structured data
3. Section summarization - generating summaries of document sections
4. Topic clustering - finding themes across the document
5. Citation extraction - extracting academic references
6. Metadata extraction - parsing paper metadata, authors, affiliations
7. Result extraction - finding quantitative metrics and results
8. Methodology extraction - finding experimental setup descriptions
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from webagent.core.models import ToolResult
from webagent.tools.registry import tool
from webagent.utils.chandra_pdf import (
    PDFParseResult,
    parse_pdf_with_chandra,
)
from webagent.utils.paths import get_artifacts_dir, get_pdf_extract_dir
from webagent.utils.paths import resolve_pdf_path as _resolve_pdf_path

if TYPE_CHECKING:
    from webagent.core.config import AgentConfig

logger = logging.getLogger("webagent.pdf_mining")

# Reuse the cache from pdf_qa_tools
try:
    from webagent.tools.builtin.pdf_qa_tools import _pdf_cache, get_cached_pdf_result
except ImportError:
    # Fallback definitions if import fails
    _pdf_cache: dict[str, PDFParseResult] = {}  # type: ignore[no-redef]

    def get_cached_pdf_result(path: Path) -> PDFParseResult | None:
        return _pdf_cache.get(str(path.resolve()))


def _resolve_pdf_input(
    path_str: str, artifacts_dir: Path, tool_name: str
) -> tuple[Path | None, ToolResult | None]:
    """Resolve a PDF input path and reject reads outside the current output root."""
    path, _was_fallback, error = _resolve_pdf_path(path_str, artifacts_dir, use_fallback=False)
    if error:
        return None, ToolResult(success=False, tool_name=tool_name, error=error)
    return path, None


# ============================================================================
# Table Data Extraction
# ============================================================================


class TableDataParser(HTMLParser):
    """Parse HTML table content into structured data."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.current_row: list[str] = []
        self.in_cell = False
        self.in_header = False
        self.cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.current_row = []
        elif tag in ("td", "th"):
            self.in_cell = True
            self.in_header = tag == "th"
            self.cell_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self.current_row:
            self.rows.append(self.current_row)
        elif tag in ("td", "th") and self.in_cell:
            self.current_row.append("".join(self.cell_text).strip())
            self.in_cell = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_text.append(data)


def parse_table_html(html_content: str) -> dict[str, Any]:
    """Parse HTML table into structured data.

    Args:
        html_content: HTML content of the table

    Returns:
        Dictionary with headers, rows, and metadata
    """
    parser = TableDataParser()
    parser.feed(html_content)

    if not parser.rows:
        return {"headers": [], "rows": [], "row_count": 0, "col_count": 0}

    # Assume first row is headers
    headers = parser.rows[0] if parser.rows else []
    rows = parser.rows[1:] if len(parser.rows) > 1 else []

    return {
        "headers": headers,
        "rows": rows,
        "row_count": len(rows),
        "col_count": len(headers),
    }


def extract_table_data_structured(table_info: Any, query: str | None = None) -> dict[str, Any]:
    """Extract structured data from a table with optional query filtering.

    Args:
        table_info: TableInfo from MinerU
        query: Optional query to find relevant rows/columns

    Returns:
        Structured table data with query results
    """
    if not hasattr(table_info, "html_body") or not table_info.html_body:
        return {"error": "No HTML content available"}

    parsed = parse_table_html(table_info.html_body)

    result = {
        "caption": getattr(table_info, "caption", ""),
        "page": getattr(table_info, "page_idx", 0) + 1,
        "table_number": getattr(table_info, "table_number", ""),
        "headers": parsed["headers"],
        "rows": parsed["rows"],
        "row_count": parsed["row_count"],
        "col_count": parsed["col_count"],
    }

    # If query provided, find matching rows
    if query and parsed["rows"]:
        query_lower = query.lower()
        matching_rows = []
        for i, row in enumerate(parsed["rows"]):
            row_text = " ".join(str(cell) for cell in row).lower()
            if query_lower in row_text:
                matching_rows.append({"row_index": i, "data": row})

        result["matching_rows"] = matching_rows
        result["match_count"] = len(matching_rows)

    return result


# ============================================================================
# Cross-Reference Analysis
# ============================================================================


def find_figure_mentions(
    result: PDFParseResult, figure_number: str | None = None, caption_keyword: str | None = None
) -> dict[str, Any]:
    """Find where figures are mentioned in the text.

    Args:
        result: Parsed MinerU result
        figure_number: Specific figure number to look for
        caption_keyword: Keyword to search for in captions

    Returns:
        Dictionary with mentions and context
    """
    mentions = []

    for block in result.text_blocks:
        text = block.text.lower()

        # Look for figure references
        if figure_number:
            patterns = [f"figure {figure_number}", f"fig. {figure_number}", f"fig{figure_number}"]
        else:
            patterns = ["figure \\d+", "fig\\.? \\d+", "fig\\d+"]

        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                mentions.append(
                    {
                        "page": block.page_idx + 1,
                        "text": block.text[:200] + "..." if len(block.text) > 200 else block.text,
                        "block_type": block.block_type,
                        "level": block.level,
                    }
                )
                break

    return {
        "figure_number": figure_number,
        "caption_keyword": caption_keyword,
        "mention_count": len(mentions),
        "mentions": mentions[:10],  # Limit to first 10
    }


def find_table_mentions(
    result: PDFParseResult, table_number: str | None = None, caption_keyword: str | None = None
) -> dict[str, Any]:
    """Find where tables are mentioned in the text.

    Args:
        result: Parsed MinerU result
        table_number: Specific table number to look for
        caption_keyword: Keyword to search for in captions

    Returns:
        Dictionary with mentions and context
    """
    mentions = []

    for block in result.text_blocks:
        text = block.text.lower()

        # Look for table references
        if table_number:
            patterns = [f"table {table_number}"]
        else:
            patterns = ["table \\d+"]

        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                mentions.append(
                    {
                        "page": block.page_idx + 1,
                        "text": block.text[:200] + "..." if len(block.text) > 200 else block.text,
                        "block_type": block.block_type,
                        "level": block.level,
                    }
                )
                break

    return {
        "table_number": table_number,
        "caption_keyword": caption_keyword,
        "mention_count": len(mentions),
        "mentions": mentions[:10],  # Limit to first 10
    }


# ============================================================================
# Section Analysis
# ============================================================================


def get_section_content(result: PDFParseResult, section_title: str) -> dict[str, Any]:
    """Get full content of a specific section.

    Args:
        result: Parsed MinerU result
        section_title: Title of the section to extract

    Returns:
        Section content with subsections
    """
    # Find the section
    target_section = None
    target_level = None

    for section_key, _blocks in result.sections.items():
        if ":" in section_key:
            level, title = section_key.split(":", 1)
            if section_title.lower() in title.lower() or title.lower() in section_title.lower():
                target_section = section_key
                target_level = int(level)
                break

    if not target_section:
        return {"error": f"Section '{section_title}' not found"}

    # Get all blocks in this section and its subsections
    section_blocks = result.sections.get(target_section, [])

    # Find subsections (deeper levels that come after this section)
    subsections = []
    current_level = target_level

    for section_key, blocks in result.sections.items():
        if section_key == target_section:
            continue
        if ":" in section_key:
            level, title = section_key.split(":", 1)
            level_int = int(level)
            if current_level is not None and level_int > current_level:
                # Check if this subsection comes within our section
                # (by checking if there are blocks between them)
                subsections.append(
                    {
                        "title": title,
                        "level": level_int,
                        "block_count": len(blocks),
                    }
                )

    # Build text content
    full_text = "\n\n".join(block.text for block in section_blocks)

    return {
        "title": section_title,
        "level": target_level,
        "block_count": len(section_blocks),
        "text_length": len(full_text),
        "content": full_text[:2000] + "..." if len(full_text) > 2000 else full_text,
        "subsection_count": len(subsections),
        "subsections": subsections[:10],
    }


def extract_section_hierarchy(result: PDFParseResult) -> dict[str, Any]:
    """Extract the complete section hierarchy.

    Args:
        result: Parsed MinerU result

    Returns:
        Nested section structure
    """
    hierarchy = []

    # Sort sections by page then level
    sorted_sections = []
    for section_key, blocks in result.sections.items():
        if ":" in section_key:
            level, title = section_key.split(":", 1)
            page = blocks[0].page_idx + 1 if blocks else 0
            sorted_sections.append((int(level), title, page, len(blocks)))

    # Sort by level (H1 first), then by page
    sorted_sections.sort(key=lambda x: (x[0], x[2]))

    # Build hierarchy
    level_stack: list[dict] = []

    for level, title, page, block_count in sorted_sections:  # type: ignore[assignment]
        section = {
            "title": title,
            "level": level,
            "page": page,
            "block_count": block_count,
            "children": [],
        }

        # Find parent (lower level)
        while level_stack and level_stack[-1]["level"] >= level:
            level_stack.pop()

        if level_stack:
            level_stack[-1]["children"].append(section)
        else:
            hierarchy.append(section)

        level_stack.append(section)

    return {
        "total_sections": len(sorted_sections),
        "max_depth": max((s[0] for s in sorted_sections), default=0),
        "hierarchy": hierarchy,
    }


# ============================================================================
# Citation and Metadata Extraction
# ============================================================================


@dataclass
class Citation:
    """Represents an academic citation."""

    authors: list[str] = field(default_factory=list)
    title: str = ""
    year: str = ""
    venue: str = ""
    volume: str = ""
    pages: str = ""
    doi: str = ""
    url: str = ""
    raw: str = ""


def extract_citations(text: str) -> list[Citation]:
    """Extract academic citations from text.

    Args:
        text: Text content to search for citations

    Returns:
        List of Citation objects
    """
    citations = []

    # Common citation patterns
    patterns = [
        # (Author, Year) format
        r"\(([A-Z][a-z]+(?:\s+et\s+al\.?)?(?:,\s*[A-Z][a-z]+)*,\s*\d{4}[a-z]?)\)",
        # Author (Year) format
        r"([A-Z][a-z]+(?:\s+et\s+al\.?)?(?:,\s*[A-Z][a-z]+)*)\s+\(\d{4}[a-z]?\)",
        # [Number] format
        r"\[(\d+)\]",
        # DOI pattern
        r"(doi:\s*10\.\d{4,}/[^\s]+)",
        # URL pattern
        r"(https?://(?:arxiv\.org|dl\.acm\.org|ieeexplore\.ieee\.org)/[^\s]+)",
    ]

    for pattern in patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            citation = Citation(raw=match.group(0))
            # Try to extract structured info
            if "," in match.group(0) and "(" in match.group(0):
                # Likely Author, Year format
                parts = match.group(0).split("(")
                if len(parts) > 1:
                    authors = parts[0].strip()
                    citation.authors = [a.strip() for a in authors.split(",")]
                    year_part = parts[1].split(")")[0]
                    citation.year = year_part

            citations.append(citation)

    return citations[:50]  # Limit to first 50


def extract_paper_metadata(result: PDFParseResult) -> dict[str, Any]:
    """Extract paper metadata from the first page.

    Args:
        result: Parsed MinerU result

    Returns:
        Paper metadata including title, authors, abstract, etc.
    """
    metadata = {
        "title": "",
        "authors": [],
        "abstract": "",
        "affiliations": [],
        "keywords": [],
        "date": "",
        "venue": "",
    }

    # Get first page content
    first_page_blocks = [b for b in result.text_blocks if b.page_idx == 0]

    if not first_page_blocks:
        return metadata

    # First title is likely the paper title
    for block in first_page_blocks:
        if block.level == 1:
            metadata["title"] = block.text
            break

    # Extract authors: find a short, near-the-top line whose comma-separated
    # parts ALL look like personal names (capitalised, ≤4 tokens). Each part must
    # match so a normal sentence ("…year over year, led by…") isn't mistaken for
    # an author list. Join by newline (not space) so line splitting actually works.
    name_re = re.compile(r"^[A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]*){0,3}$")
    all_text = "\n".join(b.text for b in first_page_blocks[:10])
    for line in all_text.split("\n"):
        line = line.strip()
        if "," not in line or len(line) > 200:
            continue
        parts = [a.strip() for a in line.split(",") if a.strip()]
        if 2 <= len(parts) <= 10 and all(len(p) <= 40 and name_re.match(p) for p in parts):
            metadata["authors"] = parts
            break

    # Extract abstract
    for block in result.text_blocks:
        if "abstract" in block.text.lower():
            # Get next few paragraphs as abstract
            abstract_parts = []
            block_idx = result.text_blocks.index(block)
            for subsequent_block in result.text_blocks[block_idx:]:
                if subsequent_block.level == 0:
                    abstract_parts.append(subsequent_block.text)
                elif subsequent_block.level > 0:
                    break
            metadata["abstract"] = " ".join(abstract_parts)[:500]
            break

    # Extract keywords
    keyword_pattern = r"(?:keywords?:|key words:)\s*([^\n]+)"
    keyword_match = re.search(keyword_pattern, all_text, re.IGNORECASE)
    if keyword_match:
        keywords = [k.strip() for k in keyword_match.group(1).split(",")]
        metadata["keywords"] = keywords

    return metadata


# ============================================================================
# Result and Metrics Extraction
# ============================================================================


def extract_metrics_and_results(result: PDFParseResult) -> dict[str, Any]:
    """Extract quantitative metrics and results from the document.

    Args:
        result: Parsed MinerU result

    Returns:
        Dictionary with extracted metrics, scores, and results
    """
    metrics: dict[str, list[Any]] = {
        "performance_metrics": [],
        "comparison_results": [],
        "statistical_significance": [],
    }

    # Patterns for common metrics
    metric_patterns = [
        (r"accuracy[:\s]+([\d.]+)", "accuracy"),
        (r"precision[:\s]+([\d.]+)", "precision"),
        (r"recall[:\s]+([\d.]+)", "recall"),
        (r"F1[:\s]+([\d.]+)", "f1_score"),
        (r"F1-score[:\s]+([\d.]+)", "f1_score"),
        (r"bleu[:\s]+([\d.]+)", "bleu"),
        (r"rouge[:\s]+([\d.]+)", "rouge"),
        (r"(?:AIME|MATH|GSM8K|MMLU)[:\s]+([\d.]+)", "benchmark_score"),
        (r"p[-\s]?value[:\s]+([<]?[\d.]+)", "p_value"),
        (r"(?:improvement|gain|boost)[:\s]+([\d.]+%)", "improvement"),
    ]

    all_text = " ".join(block.text for block in result.text_blocks)

    for pattern, metric_name in metric_patterns:
        matches = re.finditer(pattern, all_text, re.IGNORECASE)
        for match in matches:
            metrics["performance_metrics"].append(
                {
                    "metric": metric_name,
                    "value": match.group(1),
                    "context": all_text[max(0, match.start() - 50) : match.end() + 50],
                }
            )

    # Find comparison statements
    comparison_patterns = [
        r"outperforms? ([\w-]+)",
        r"surpasses? ([\w-]+)",
        r"beats? ([\w-]+)",
        r"better than ([\w-]+)",
        r"(?:compared|in comparison) to ([\w-]+)",
    ]

    for pattern in comparison_patterns:
        matches = re.finditer(pattern, all_text, re.IGNORECASE)
        for match in matches:
            metrics["comparison_results"].append(
                {
                    "baseline": match.group(1),
                    "statement": all_text[max(0, match.start() - 80) : match.end() + 50],
                }
            )

    return metrics


# ============================================================================
# Topic and Theme Extraction
# ============================================================================


def extract_topics_and_keywords(result: PDFParseResult, top_n: int = 20) -> dict[str, Any]:
    """Extract main topics and keywords from the document.

    Args:
        result: Parsed MinerU result
        top_n: Number of top keywords to return

    Returns:
        Dictionary with topics, keywords, and their frequencies
    """
    # Word frequency analysis
    word_freq: defaultdict[str, int] = defaultdict(int)
    all_text = " ".join(block.text for block in result.text_blocks)

    # Extract words (lowercase, alphabetic, length > 3)
    words = re.findall(r"\b[a-zA-Z]{4,}\b", all_text.lower())

    # Filter common words
    stop_words = {
        "that",
        "this",
        "with",
        "from",
        "have",
        "been",
        "were",
        "they",
        "their",
        "would",
        "could",
        "should",
        "about",
        "after",
        "before",
        "between",
        "under",
        "over",
        "through",
        "during",
        "without",
        "within",
        "however",
        "therefore",
        "moreover",
        "furthermore",
        "nevertheless",
        "although",
        "though",
        "figure",
        "table",
        "section",
        "paper",
        "document",
        "results",
        "shows",
        "shown",
    }

    for word in words:
        if word not in stop_words:
            word_freq[word] += 1

    # Get top keywords
    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # Extract topic phrases from section titles
    topics = []
    for section_key in result.sections:
        if ":" in section_key:
            level, title = section_key.split(":", 1)
            if int(level) <= 2:  # Only H1 and H2
                blocks = result.sections.get(section_key, [])
                page = blocks[0].page_idx + 1 if blocks else 0
                topics.append(
                    {
                        "title": title,
                        "level": int(level),
                        "page": page,
                    }
                )

    return {
        "top_keywords": [{"word": w, "frequency": f} for w, f in sorted_words],
        "main_topics": topics[:15],
        "total_unique_words": len(word_freq),
    }


# ============================================================================
# Tool Implementations
# ============================================================================


@tool(
    "pdf_extract_table_data",
    "Extract structured data from a table in a PDF. "
    "Parses HTML table content into headers and rows. "
    "params: path (string), table_number (string), query? (string)",
)
class ExtractTableDataTool:
    """Extract structured data from PDF tables."""

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
        if not isinstance(params.get("table_number"), str) or not params["table_number"].strip():
            raise ValueError("'table_number' required - e.g., '1', '2'")

    async def execute(self, params: dict) -> ToolResult:
        path_str = params["path"].strip()
        path, path_error = _resolve_pdf_input(
            path_str, self.artifacts_dir, "pdf_extract_table_data"
        )
        if path_error:
            return path_error
        assert path is not None

        table_number = params["table_number"].strip()
        query = params.get("query", "").strip()

        # Check cache
        result = get_cached_pdf_result(path)
        if not result or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[str(path.resolve())] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_extract_table_data",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_extract_table_data",
                error=result.error,
            )

        # Find the table
        target_table = None
        for table in result.tables:
            if table.table_number == table_number:
                target_table = table
                break

        if not target_table:
            return ToolResult(
                success=True,
                tool_name="pdf_extract_table_data",
                data={
                    "found": False,
                    "message": f"Table {table_number} not found. Available tables: {len(result.tables)}",
                },
            )

        # Extract structured data
        table_data = extract_table_data_structured(target_table, query if query else None)

        return ToolResult(
            success=True,
            tool_name="pdf_extract_table_data",
            data=table_data,
        )


@tool(
    "pdf_find_mentions",
    "Find where a figure or table is mentioned in the text. "
    "Use this to understand context around visual elements. "
    "params: path (string), type (string: 'figure' or 'table'), number (string)",
)
class FindMentionsTool:
    """Find mentions of figures/tables in document text."""

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
        if params.get("type", "").lower() not in ("figure", "table"):
            raise ValueError("'type' must be 'figure' or 'table'")
        if not isinstance(params.get("number"), str) or not params["number"].strip():
            raise ValueError("'number' required - e.g., '1', '2'")

    async def execute(self, params: dict) -> ToolResult:
        path_str = params["path"].strip()
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_find_mentions")
        if path_error:
            return path_error
        assert path is not None

        ref_type = params["type"].lower()
        number = params["number"].strip()

        # Check cache
        result = get_cached_pdf_result(path)
        if not result or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[str(path.resolve())] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_find_mentions",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_find_mentions",
                error=result.error,
            )

        # Find mentions
        if ref_type == "figure":
            mentions = find_figure_mentions(result, figure_number=number)
        else:
            mentions = find_table_mentions(result, table_number=number)

        return ToolResult(
            success=True,
            tool_name="pdf_find_mentions",
            data=mentions,
        )


@tool(
    "pdf_get_section",
    "Get the full content of a specific section with its subsections. "
    "params: path (string), section_title (string)",
)
class GetSectionTool:
    """Extract complete section content."""

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
        if not isinstance(params.get("section_title"), str) or not params["section_title"].strip():
            raise ValueError("'section_title' required")

    async def execute(self, params: dict) -> ToolResult:
        path_str = params["path"].strip()
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_get_section")
        if path_error:
            return path_error
        assert path is not None

        section_title = params["section_title"].strip()

        # Check cache
        result = get_cached_pdf_result(path)
        if not result or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[str(path.resolve())] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_get_section",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_get_section",
                error=result.error,
            )

        section_content = get_section_content(result, section_title)

        return ToolResult(
            success=True,
            tool_name="pdf_get_section",
            data=section_content,
        )


@tool(
    "pdf_get_hierarchy",
    "Get the complete section hierarchy of a PDF document. "
    "Shows nested structure of all sections. "
    "params: path (string)",
)
class GetHierarchyTool:
    """Extract document section hierarchy."""

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
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_get_hierarchy")
        if path_error:
            return path_error
        assert path is not None

        # Check cache
        result = get_cached_pdf_result(path)
        if not result or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[str(path.resolve())] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_get_hierarchy",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_get_hierarchy",
                error=result.error,
            )

        hierarchy = extract_section_hierarchy(result)

        return ToolResult(
            success=True,
            tool_name="pdf_get_hierarchy",
            data=hierarchy,
        )


@tool(
    "pdf_get_metadata",
    "Extract paper metadata including title, authors, abstract, and keywords. "
    "params: path (string)",
)
class GetMetadataTool:
    """Extract paper metadata."""

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

        path, was_fallback, error = _resolve_pdf_path(
            path_str, self.artifacts_dir, use_fallback=True
        )

        if was_fallback:
            logger.info("Fallback used for pdf_get_metadata: %s", error)

        if error and not was_fallback:
            return ToolResult(
                success=False,
                tool_name="pdf_get_metadata",
                error=error,
            )

        if not path.exists():
            return ToolResult(
                success=False,
                tool_name="pdf_get_metadata",
                error=error,
            )

        # Check cache
        result = get_cached_pdf_result(path)
        if not result or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[str(path.resolve())] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_get_metadata",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_get_metadata",
                error=result.error,
            )

        metadata = extract_paper_metadata(result)

        return ToolResult(
            success=True,
            tool_name="pdf_get_metadata",
            data=metadata,
        )


@tool(
    "pdf_extract_metrics",
    "Extract quantitative metrics, benchmark scores, and comparison results from a paper. "
    "Use this to find performance numbers and comparisons. "
    "params: path (string)",
)
class ExtractMetricsTool:
    """Extract performance metrics and results."""

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
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_extract_metrics")
        if path_error:
            return path_error
        assert path is not None

        # Check cache
        result = get_cached_pdf_result(path)
        if not result or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[str(path.resolve())] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_extract_metrics",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_extract_metrics",
                error=result.error,
            )

        metrics = extract_metrics_and_results(result)

        return ToolResult(
            success=True,
            tool_name="pdf_extract_metrics",
            data=metrics,
        )


@tool(
    "pdf_extract_topics",
    "Extract main topics and keywords from a PDF document. "
    "Useful for understanding the document's themes. "
    "params: path (string), top_n? (number, default 20)",
)
class ExtractTopicsTool:
    """Extract topics and keywords."""

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
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_extract_topics")
        if path_error:
            return path_error
        assert path is not None

        top_n = params.get("top_n", 20)

        # Check cache
        result = get_cached_pdf_result(path)
        if not result or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[str(path.resolve())] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_extract_topics",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_extract_topics",
                error=result.error,
            )

        topics = extract_topics_and_keywords(result, top_n=top_n)

        return ToolResult(
            success=True,
            tool_name="pdf_extract_topics",
            data=topics,
        )


@tool(
    "pdf_extract_citations",
    "Extract academic citations and references from a PDF document. "
    "Useful for literature review and finding related work. "
    "params: path (string)",
)
class ExtractCitationsTool:
    """Extract citations and references."""

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
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_extract_citations")
        if path_error:
            return path_error
        assert path is not None

        # Check cache
        result = get_cached_pdf_result(path)
        if not result or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[str(path.resolve())] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_extract_citations",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_extract_citations",
                error=result.error,
            )

        # Look for references section
        all_text = " ".join(block.text for block in result.text_blocks)
        citations = extract_citations(all_text)

        return ToolResult(
            success=True,
            tool_name="pdf_extract_citations",
            data={
                "citation_count": len(citations),
                "citations": [{"raw": c.raw} for c in citations[:30]],
            },
        )


@tool(
    "pdf_summarize_sections",
    "Generate summaries for all major sections of a PDF document. "
    "Useful for getting an overview of the document structure. "
    "params: path (string)",
)
class SummarizeSectionsTool:
    """Summarize document sections."""

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
        path, path_error = _resolve_pdf_input(
            path_str, self.artifacts_dir, "pdf_summarize_sections"
        )
        if path_error:
            return path_error
        assert path is not None

        # Check cache
        result = get_cached_pdf_result(path)
        if not result or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[str(path.resolve())] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_summarize_sections",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_summarize_sections",
                error=result.error,
            )

        # Build section summaries
        summaries = []
        for section_key, blocks in result.sections.items():
            if ":" in section_key:
                level, title = section_key.split(":", 1)
                if int(level) <= 2:  # Only H1 and H2
                    # Get first few paragraphs as summary
                    summary_text = " ".join(b.text for b in blocks[:3])
                    summaries.append(
                        {
                            "title": title,
                            "level": int(level),
                            "page": blocks[0].page_idx + 1 if blocks else 0,
                            "block_count": len(blocks),
                            "summary": summary_text[:300] + "..."
                            if len(summary_text) > 300
                            else summary_text,
                        }
                    )

        # Sort by level and page
        summaries.sort(key=lambda s: (s["level"], s["page"]))

        return ToolResult(
            success=True,
            tool_name="pdf_summarize_sections",
            data={
                "section_count": len(summaries),
                "summaries": summaries,
            },
        )


@tool(
    "pdf_compare_entities",
    "Compare entities (models, methods, systems) mentioned in the PDF. "
    "Extracts comparative statements and performance data. "
    "params: path (string), entity? (string)",
)
class CompareEntitiesTool:
    """Compare entities mentioned in the document."""

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
        path, path_error = _resolve_pdf_input(path_str, self.artifacts_dir, "pdf_compare_entities")
        if path_error:
            return path_error
        assert path is not None

        entity = params.get("entity", "").strip()

        # Check cache
        result = get_cached_pdf_result(path)
        if not result or result.error:
            try:
                result = await asyncio.to_thread(
                    parse_pdf_with_chandra, path, get_pdf_extract_dir(self.artifacts_dir)
                )
                if not result.error:
                    _pdf_cache[str(path.resolve())] = result
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="pdf_compare_entities",
                    error=f"Failed to parse PDF: {e}",
                )

        if result.error:
            return ToolResult(
                success=False,
                tool_name="pdf_compare_entities",
                error=result.error,
            )

        # Extract comparative statements
        all_text = " ".join(block.text for block in result.text_blocks)

        # Find model/system names (capitalized words with numbers/hyphens)
        entity_pattern = r"\b[A-Z][a-zA-Z0-9-]+(?:-\d+[a-z]?|\s+\d+[a-z]?)?\b"
        entities = list(set(re.findall(entity_pattern, all_text)))
        entities = [e for e in entities if len(e) > 2 and e not in ["The", "This", "That"]]

        # Find comparison statements
        comparisons = []
        comparison_patterns = [
            r"([A-Z][a-zA-Z0-9-]+)\s+(?:outperforms?|surpasses?|beats?|is better than)\s+([A-Z][a-zA-Z0-9-]+)",
            r"([A-Z][a-zA-Z0-9-]+)\s+(?:vs\.?|versus|compared to)\s+([A-Z][a-zA-Z0-9-]+)",
        ]

        for pattern in comparison_patterns:
            matches = re.finditer(pattern, all_text)
            for match in matches:
                comparisons.append(
                    {
                        "entity_a": match.group(1),
                        "entity_b": match.group(2),
                        "context": all_text[max(0, match.start() - 50) : match.end() + 80],
                    }
                )

        # Filter by entity if specified
        if entity:
            comparisons = [
                c
                for c in comparisons
                if entity.lower() in c["entity_a"].lower()
                or entity.lower() in c["entity_b"].lower()
            ]
            entities = [e for e in entities if entity.lower() in e.lower()]

        return ToolResult(
            success=True,
            tool_name="pdf_compare_entities",
            data={
                "entity_filter": entity if entity else "all",
                "found_entities": entities[:30],
                "entity_count": len(entities),
                "comparisons": comparisons[:20],
                "comparison_count": len(comparisons),
            },
        )
