"""Structured data models for document parsing + pure query helpers.

These dataclasses are the public output contract of the parser cascade and are
used by both parser providers and PDF tools, so the
PDF tool modules keep working unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FIGURE_PATTERN = re.compile(r"Figure\s+(\d+[a-z]?)", re.IGNORECASE)
_TABLE_PATTERN = re.compile(r"Table\s+(\d+[a-z]?)", re.IGNORECASE)

BBox = tuple[int, int, int, int]


@dataclass
class ImageInfo:
    """Structured information about an extracted image."""

    path: str
    page_idx: int
    bbox: BBox  # (x0, y0, x1, y1)
    caption: str = ""
    footnote: str = ""
    figure_number: str = ""


@dataclass
class TableInfo:
    """Structured information about an extracted table."""

    path: str
    page_idx: int
    bbox: BBox
    caption: str = ""
    footnote: str = ""
    html_body: str = ""  # HTML / markdown representation
    table_number: str = ""


@dataclass
class TextBlock:
    """Structured information about a text block."""

    text: str
    page_idx: int
    bbox: BBox
    level: int = 0  # Header level (1=H1, 2=H2, …), 0 for body text
    block_type: str = "text"  # title, paragraph, caption, …


@dataclass
class PDFParseResult:
    """Complete result from PDF parsing."""

    markdown_path: str | None
    json_path: str | None
    images_dir: str
    output_dir: str
    method: str = "cascade"
    backend: str | None = None
    error: str | None = None

    # Structured content
    images: list[ImageInfo] = field(default_factory=list)
    tables: list[TableInfo] = field(default_factory=list)
    text_blocks: list[TextBlock] = field(default_factory=list)
    sections: dict[str, list[TextBlock]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pure content-query helpers.
# ---------------------------------------------------------------------------


def extract_figure_number(text: str) -> str:
    """Extract a figure number (e.g. ``3a``) from caption text, or ``''``."""
    match = _FIGURE_PATTERN.search(text or "")
    return match.group(1) if match else ""


def extract_table_number(text: str) -> str:
    """Extract a table number (e.g. ``2``) from caption text, or ``''``."""
    match = _TABLE_PATTERN.search(text or "")
    return match.group(1) if match else ""


def find_images_by_keyword(
    result: PDFParseResult, keyword: str, case_sensitive: bool = False
) -> list[ImageInfo]:
    """Find images whose captions contain the given keyword."""
    if not case_sensitive:
        keyword = keyword.lower()
    matching = []
    for img in result.images:
        caption = img.caption if case_sensitive else img.caption.lower()
        if keyword in caption:
            matching.append(img)
    return matching


def find_tables_by_keyword(
    result: PDFParseResult, keyword: str, case_sensitive: bool = False
) -> list[TableInfo]:
    """Find tables whose captions contain the given keyword."""
    if not case_sensitive:
        keyword = keyword.lower()
    matching = []
    for table in result.tables:
        caption = table.caption if case_sensitive else table.caption.lower()
        if keyword in caption:
            matching.append(table)
    return matching


def find_section_by_title(
    result: PDFParseResult, title: str, case_sensitive: bool = False
) -> list[TextBlock] | None:
    """Find a section by its title and return its text blocks."""
    if not case_sensitive:
        title = title.lower()
    for section_key, blocks in result.sections.items():
        if ":" in section_key:
            section_title = section_key.split(":", 1)[1]
            if not case_sensitive:
                section_title = section_title.lower()
            if title in section_title or section_title in title:
                return blocks
    return None


def generate_content_summary(result: PDFParseResult) -> str:
    """Generate a text summary of the document structure."""
    lines = [
        "# Document Structure Summary",
        "",
        f"## Images ({len(result.images)})",
    ]
    for i, img in enumerate(result.images, 1):
        lines.append(f"{i}. Page {img.page_idx + 1}: {img.caption or '(no caption)'}")
        if img.figure_number:
            lines.append(f"   - Figure {img.figure_number}")

    lines.append("")
    lines.append(f"## Tables ({len(result.tables)})")
    for i, table in enumerate(result.tables, 1):
        lines.append(f"{i}. Page {table.page_idx + 1}: {table.caption or '(no caption)'}")
        if table.table_number:
            lines.append(f"   - Table {table.table_number}")

    lines.append("")
    lines.append(f"## Sections ({len(result.sections)})")
    for section_key in result.sections:
        if ":" in section_key:
            level, title = section_key.split(":", 1)
            indent = "  " * (int(level) - 1) if level.isdigit() else ""
            lines.append(f"{indent}- {title}")

    return "\n".join(lines)
