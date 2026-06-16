"""Helpers to build a ``PDFParseResult`` from markdown / page text.

Shared by the Marker and local providers (and the MinerU markdown fallback).
The MinerU provider builds richer structures directly from ``content_list.json``;
this module covers the markdown-only path.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .models import (
    PDFParseResult,
    TableInfo,
    TextBlock,
    extract_table_number,
)

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_MD_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

# Markdown inline image: ![alt](key). Captures alt text and the image key.
_IMG_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
# A figure-caption line, e.g. "Figure 1: The Transformer ..." (colon or period
# after the number distinguishes a caption from a body-text mention).
_FIG_CAPTION_RE = re.compile(r"Figure\s+\d+[a-z]?\s*[:.][^\n]*", re.IGNORECASE)
# Loose "figure N" mention, used to detect when an image's alt text is itself a caption.
_FIG_MENTION_RE = re.compile(r"fig(?:ure|\.)?\s*\d", re.IGNORECASE)


def image_captions_from_pages(page_texts: list[str]) -> dict[str, tuple[int, str]]:
    """Map each markdown image key (basename) to ``(page_idx, caption)``.

    Marker embeds images as ``![alt](key)``. The caption is the alt text when it
    already reads like a figure caption (e.g. ``![Figure 1: ...](key)``),
    otherwise the nearest ``Figure N: ...`` caption line on the same page. This
    lets ``Figure N`` be resolved to the correct image instead of relying on
    arbitrary extraction order.
    """
    mapping: dict[str, tuple[int, str]] = {}
    for page_idx, page_md in enumerate(page_texts):
        captions = [(m.start(), m.group(0).strip()) for m in _FIG_CAPTION_RE.finditer(page_md)]
        for ref in _IMG_REF_RE.finditer(page_md):
            alt = ref.group(1).strip()
            name = Path(ref.group(2).strip()).name
            if name in mapping:
                continue
            if _FIG_MENTION_RE.search(alt):
                caption = alt
            elif captions:
                pos = ref.start()
                caption = min(captions, key=lambda c: abs(c[0] - pos))[1]
            else:
                caption = ""
            mapping[name] = (page_idx, caption)
    return mapping


def write_outputs(
    result: PDFParseResult,
    output_dir: Path,
    markdown: str,
    content_list: object | None = None,
    *,
    stem: str = "parsed",
) -> None:
    """Persist markdown + structured JSON next to the result and record paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if markdown:
        md_path = output_dir / f"{stem}.md"
        md_path.write_text(markdown, encoding="utf-8")
        result.markdown_path = str(md_path)
    if content_list is not None:
        json_path = output_dir / f"{stem}_content_list.json"
        try:
            json_path.write_text(
                json.dumps(content_list, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result.json_path = str(json_path)
        except (TypeError, ValueError):
            logger.debug("Failed to serialise content_list to JSON", exc_info=True)


def add_block(result: PDFParseResult, block: TextBlock, current_section: str) -> str:
    """Append a text block and attach it to the current section. Returns the new section key."""
    result.text_blocks.append(block)
    if block.block_type == "title":
        section_key = f"{block.level}:{block.text}"
        result.sections.setdefault(section_key, [])
        return section_key
    result.sections.setdefault(current_section, [])
    result.sections[current_section].append(block)
    return current_section


def build_from_page_texts(result: PDFParseResult, page_texts: list[str]) -> None:
    """Populate ``text_blocks`` / ``sections`` / ``tables`` from per-page markdown."""
    current_section = "root"
    for page_idx, page_md in enumerate(page_texts):
        current_section = _parse_markdown_page(result, page_md, page_idx, current_section)


def _parse_markdown_page(
    result: PDFParseResult, markdown: str, page_idx: int, current_section: str
) -> str:
    lines = markdown.splitlines()
    i = 0
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph, current_section
        text = " ".join(paragraph).strip()
        paragraph = []
        if text:
            current_section = add_block(
                result,
                TextBlock(text=text, page_idx=page_idx, bbox=(0, 0, 0, 0), block_type="paragraph"),
                current_section,
            )

    while i < len(lines):
        line = lines[i]

        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            current_section = add_block(
                result,
                TextBlock(
                    text=heading.group(2).strip(),
                    page_idx=page_idx,
                    bbox=(0, 0, 0, 0),
                    level=level,
                    block_type="title",
                ),
                current_section,
            )
            i += 1
            continue

        # Markdown table block: a row line followed by a separator line.
        if (
            _MD_TABLE_ROW_RE.match(line)
            and i + 1 < len(lines)
            and _MD_TABLE_SEP_RE.match(lines[i + 1])
        ):
            flush_paragraph()
            table_lines: list[str] = []
            while i < len(lines) and _MD_TABLE_ROW_RE.match(lines[i]):
                table_lines.append(lines[i])
                i += 1
            _append_markdown_table(result, table_lines, page_idx)
            continue

        if not line.strip():
            flush_paragraph()
            i += 1
            continue

        paragraph.append(line.strip())
        i += 1

    flush_paragraph()
    return current_section


def _append_markdown_table(result: PDFParseResult, table_lines: list[str], page_idx: int) -> None:
    html = markdown_table_to_html(table_lines)
    if not html:
        return
    # Caption: look back at the most recent paragraph mentioning "table".
    caption = ""
    for tb in reversed(result.text_blocks):
        if tb.page_idx == page_idx and "table" in tb.text.lower():
            caption = tb.text
            break
    result.tables.append(
        TableInfo(
            path="",
            page_idx=page_idx,
            bbox=(0, 0, 0, 0),
            caption=caption,
            # Store HTML, not raw markdown — downstream pdf_extract_table_data parses
            # html_body with an HTML parser, so a pipe table would yield no rows.
            html_body=html,
            table_number=extract_table_number(caption),
        )
    )


def _split_md_row(line: str) -> list[str]:
    """Split a markdown table row '| a | b |' into ['a', 'b']."""
    cells = [c.strip() for c in line.strip().split("|")]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def markdown_table_to_html(table_lines: list[str]) -> str:
    """Convert markdown pipe-table lines into a simple HTML ``<table>``.

    The first row becomes ``<th>`` headers; the alignment separator row
    (``| --- | :--: |``) is skipped; remaining rows become ``<td>`` cells.
    Returns ``""`` if there is no usable data.
    """
    from html import escape

    data_rows = [line for line in table_lines if line.strip() and not _MD_TABLE_SEP_RE.match(line)]
    if not data_rows:
        return ""
    parts: list[str] = ["<table>"]
    for idx, line in enumerate(data_rows):
        cells = _split_md_row(line)
        if not cells:
            continue
        tag = "th" if idx == 0 else "td"
        cell_html = "".join(f"<{tag}>{escape(c)}</{tag}>" for c in cells)
        parts.append(f"<tr>{cell_html}</tr>")
    parts.append("</table>")
    return "".join(parts)
