"""Analysis helpers over parsed PDF documents.

Pure functions mining structured content out of a ``PDFParseResult``: HTML
table parsing, figure/table mention search, section hierarchy, citations,
paper metadata, metrics, and topic extraction. Extracted from the PDF mining
tools so each analysis is independently testable and reusable.
"""

from __future__ import annotations

import re
from collections import defaultdict
from html.parser import HTMLParser
from typing import Any

from webagent.parser import PDFParseResult, TextBlock

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
        self.cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.current_row = []
        elif tag in ("td", "th"):
            self.in_cell = True
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
    escaped_number = re.escape(figure_number) if figure_number else None

    for block in result.text_blocks:
        raw_text = block.text.strip()
        text = raw_text.lower()

        # Look for figure references
        if escaped_number:
            reference = rf"\b(?:figure|fig\.?)\s*{escaped_number}(?![\d.])"
            # Captions and generated Markdown image alt text describe the
            # visual itself; they are not places where the body refers to it.
            caption = rf"^(?:!\[)?\s*(?:figure|fig\.?)\s*{escaped_number}\s*:"
            if re.search(caption, raw_text, re.IGNORECASE):
                continue
            patterns = [reference]
        else:
            patterns = [r"\b(?:figure|fig\.?)\s*\d+(?![\d.])"]

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
    escaped_number = re.escape(table_number) if table_number else None

    for block in result.text_blocks:
        raw_text = block.text.strip()
        text = raw_text.lower()

        # Look for table references
        if escaped_number:
            reference = rf"\btable\s*{escaped_number}(?![\d.])"
            caption = rf"^(?:!\[)?\s*table\s*{escaped_number}\s*:"
            if re.search(caption, raw_text, re.IGNORECASE):
                continue
            patterns = [reference]
        else:
            patterns = [r"\btable\s*\d+(?![\d.])"]

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

    for section_key in result.sections:
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
    level_stack: list[dict[str, Any]] = []

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


def extract_citations(text: str) -> list[str]:
    """Extract academic citations from text.

    Args:
        text: Text content to search for citations

    Returns:
        List of Citation objects
    """
    citations: list[str] = []

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
        citations.extend(match.group(0) for match in matches)

    return citations[:50]  # Limit to first 50


def _extract_title(first_page_blocks: list[TextBlock]) -> str:
    """First level-1 heading on the first page is likely the paper title."""
    for block in first_page_blocks:
        if block.level == 1:
            return str(block.text)
    return ""


def _extract_authors(first_page_text: str) -> list[str]:
    """Extract the author list from first-page text.

    Finds a short, near-the-top line whose comma-separated parts ALL look like
    personal names (capitalised, ≤4 tokens). Each part must match so a normal
    sentence ("…year over year, led by…") isn't mistaken for an author list.
    """
    name_re = re.compile(r"^[A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]*){0,3}$")
    for line in first_page_text.split("\n"):
        line = line.strip()
        if "," not in line or len(line) > 200:
            continue
        parts = [a.strip() for a in line.split(",") if a.strip()]
        if 2 <= len(parts) <= 10 and all(len(p) <= 40 and name_re.match(p) for p in parts):
            return parts
    return []


def _extract_abstract(text_blocks: list[TextBlock]) -> str:
    """Collect the paragraphs immediately following the 'Abstract' heading."""
    for block in text_blocks:
        if "abstract" in block.text.lower():
            # Get next few paragraphs as abstract
            abstract_parts = []
            block_idx = text_blocks.index(block)
            for subsequent_block in text_blocks[block_idx:]:
                if subsequent_block.level == 0:
                    abstract_parts.append(subsequent_block.text)
                elif subsequent_block.level > 0:
                    break
            return " ".join(abstract_parts)[:500]
    return ""


def _extract_keywords(first_page_text: str) -> list[str]:
    keyword_pattern = r"(?:keywords?:|key words:)\s*([^\n]+)"
    keyword_match = re.search(keyword_pattern, first_page_text, re.IGNORECASE)
    if keyword_match:
        return [k.strip() for k in keyword_match.group(1).split(",")]
    return []


def extract_paper_metadata(result: PDFParseResult) -> dict[str, Any]:
    """Extract paper metadata from the first page.

    Args:
        result: Parsed MinerU result

    Returns:
        Paper metadata including title, authors, abstract, etc.
    """
    metadata: dict[str, Any] = {
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

    metadata["title"] = _extract_title(first_page_blocks)

    # Join by newline (not space) so line splitting actually works.
    all_text = "\n".join(b.text for b in first_page_blocks[:10])
    metadata["authors"] = _extract_authors(all_text)
    metadata["abstract"] = _extract_abstract(result.text_blocks)
    metadata["keywords"] = _extract_keywords(all_text)

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
