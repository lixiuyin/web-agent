"""Context-aware retrieval over parsed PDF documents.

Pure functions scoring parsed PDF content against a user query: text chunking,
keyword relevance scoring, context assembly, and figure/table lookup by query
keywords. Extracted from the PDF Q&A tools so each step is independently
testable and reusable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from webagent.parser import (
    ImageInfo,
    PDFParseResult,
    TableInfo,
    TextBlock,
    find_images_by_keyword,
    find_tables_by_keyword,
)

# Query words ignored when scoring chunk relevance.
_STOP_WORDS = frozenset(
    {
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
)

# Query keywords that indicate the user is asking about figures/tables.
_FIGURE_KEYWORDS = ("figure", "chart", "graph", "plot", "diagram", "image")
_TABLE_KEYWORDS = ("table", "data", "comparison", "results")


def figure_sort_key(figure_number: str) -> tuple[int, str]:
    """Order figures by their number (``"1"`` < ``"2"`` < ``"3a"`` < ``"3b"``)."""
    match = re.match(r"(\d+)([a-z]?)", figure_number.strip(), re.IGNORECASE)
    if not match:
        return (10**9, figure_number)
    return (int(match.group(1)), match.group(2).lower())


def split_text_into_chunks(text: str, max_chars: int = 1000, overlap: int = 100) -> list[str]:
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


def compute_relevance_score(chunk: str, query: str) -> float:
    """Compute a simple relevance score between chunk and query.

    Uses keyword matching with position weighting for better results.
    """
    query_lower = query.lower()
    chunk_lower = chunk.lower()

    query_words = [
        w for w in re.findall(r"\w+", query_lower) if w not in _STOP_WORDS and len(w) > 2
    ]

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


def _score_text_blocks(
    result: PDFParseResult, query: str
) -> list[tuple[str, TextBlock | None, float]]:
    """Score text blocks by relevance, falling back to markdown chunks."""
    if result.text_blocks:
        scored = [
            (block.text, block, compute_relevance_score(block.text, query))
            for block in result.text_blocks
        ]
        return [entry for entry in scored if entry[2] > 0]

    # Fallback: chunk the markdown export and score the chunks instead.
    md_path = result.markdown_path
    if md_path and Path(md_path).exists():
        text = Path(md_path).read_text(encoding="utf-8", errors="replace")
        chunks = split_text_into_chunks(text, max_chars=800, overlap=50)
        return [(chunk, None, compute_relevance_score(chunk, query)) for chunk in chunks]
    return []


def _build_context(
    top_chunks: list[tuple[str, TextBlock | None, float]], max_context_chars: int
) -> tuple[str, list[dict[str, Any]]]:
    """Join top chunks into a context string under a character budget."""
    context_parts = []
    sources = []
    total_chars = 0

    for chunk_text, block, _score in top_chunks:
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

    return "\n\n---\n\n".join(context_parts), sources


def _image_entry(img: ImageInfo) -> dict[str, Any]:
    return {
        "path": img.path,
        "page": img.page_idx + 1,
        "caption": img.caption,
        "figure_number": img.figure_number,
    }


def _table_entry(table: TableInfo) -> dict[str, Any]:
    return {
        "path": table.path,
        "page": table.page_idx + 1,
        "caption": table.caption,
        "table_number": table.table_number,
        "html_body": table.html_body[:500] + "..."
        if len(table.html_body) > 500
        else table.html_body,
    }


def _find_query_figures(result: PDFParseResult, query_lower: str) -> list[dict[str, Any]]:
    """Find figures relevant to the query, by explicit number or caption keyword."""
    if not any(kw in query_lower for kw in _FIGURE_KEYWORDS):
        return []

    # Try to find by number mentioned in query
    fig_match = re.search(r"figure\s+(\d+)", query_lower, re.IGNORECASE)
    if fig_match:
        fig_num = fig_match.group(1)
        return [
            _image_entry(img)
            for img in result.images
            if img.figure_number == fig_num or str(fig_num) in img.caption.lower()
        ]

    # Search by keyword in captions
    search_kw = query_lower.split()[-1] if query_lower.split() else ""
    if search_kw and len(search_kw) > 3:
        matching_images: list[ImageInfo] = find_images_by_keyword(
            result, search_kw, case_sensitive=False
        )
        return [_image_entry(img) for img in matching_images[:3]]
    return []


def _find_query_tables(result: PDFParseResult, query_lower: str) -> list[dict[str, Any]]:
    """Find tables relevant to the query, by explicit number or caption keyword."""
    if not any(kw in query_lower for kw in _TABLE_KEYWORDS):
        return []

    table_match = re.search(r"table\s+(\d+)", query_lower, re.IGNORECASE)
    if table_match:
        table_num = table_match.group(1)
        return [
            _table_entry(table)
            for table in result.tables
            if table.table_number == table_num or str(table_num) in table.caption.lower()
        ]

    # Search by keyword
    search_kw = query_lower.split()[-1] if query_lower.split() else ""
    if search_kw and len(search_kw) > 3:
        matching_tables: list[TableInfo] = find_tables_by_keyword(
            result, search_kw, case_sensitive=False
        )
        return [_table_entry(table) for table in matching_tables[:3]]
    return []


def retrieve_relevant_sections(
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
    scored = _score_text_blocks(result, query)

    # Sort by relevance score
    scored.sort(key=lambda x: x[2], reverse=True)

    context, sources = _build_context(scored[:max_chunks], max_context_chars)
    query_lower = query.lower()

    return {
        "context": context,
        "sources": sources,
        "found_figures": _find_query_figures(result, query_lower),
        "found_tables": _find_query_tables(result, query_lower),
    }
