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

import logging
import re
from typing import Any

from webagent.core.models import ToolResult
from webagent.tools.builtin._pdf_analysis import (
    extract_citations,
    extract_metrics_and_results,
    extract_paper_metadata,
    extract_section_hierarchy,
    extract_table_data_structured,
    extract_topics_and_keywords,
    find_figure_mentions,
    find_table_mentions,
    get_section_content,
)
from webagent.tools.builtin._pdf_common import PdfToolBase, load_pdf_result
from webagent.tools.registry import tool
from webagent.utils.paths import resolve_pdf_path as _resolve_pdf_path

logger = logging.getLogger("webagent.pdf_mining")

# ============================================================================
# Tool Implementations
# ============================================================================


@tool(
    "pdf_extract_table_data",
    "Extract structured data from a table in a PDF. "
    "Parses HTML table content into headers and rows. "
    "params: path (string), table_number (string), query? (string)",
)
class ExtractTableDataTool(PdfToolBase):
    """Extract structured data from PDF tables."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")
        if not isinstance(params.get("table_number"), str) or not params["table_number"].strip():
            raise ValueError("'table_number' required - e.g., '1', '2'")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        table_number = params["table_number"].strip()
        query = params.get("query", "").strip()

        result, error = await self._load_pdf(params, "pdf_extract_table_data")
        if error:
            return error
        assert result is not None

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
class FindMentionsTool(PdfToolBase):
    """Find mentions of figures/tables in document text."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")
        if params.get("type", "").lower() not in ("figure", "table"):
            raise ValueError("'type' must be 'figure' or 'table'")
        if not isinstance(params.get("number"), str) or not params["number"].strip():
            raise ValueError("'number' required - e.g., '1', '2'")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        ref_type = params["type"].lower()
        number = params["number"].strip()

        result, error = await self._load_pdf(params, "pdf_find_mentions")
        if error:
            return error
        assert result is not None

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
class GetSectionTool(PdfToolBase):
    """Extract complete section content."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")
        if not isinstance(params.get("section_title"), str) or not params["section_title"].strip():
            raise ValueError("'section_title' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        section_title = params["section_title"].strip()

        result, error = await self._load_pdf(params, "pdf_get_section")
        if error:
            return error
        assert result is not None

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
class GetHierarchyTool(PdfToolBase):
    """Extract document section hierarchy."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result, error = await self._load_pdf(params, "pdf_get_hierarchy")
        if error:
            return error
        assert result is not None

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
class GetMetadataTool(PdfToolBase):
    """Extract paper metadata."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
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

        result, parse_error = await load_pdf_result(
            path, self.artifacts_dir, "pdf_get_metadata", config=self.config
        )
        if parse_error:
            return parse_error
        assert result is not None

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
class ExtractMetricsTool(PdfToolBase):
    """Extract performance metrics and results."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result, error = await self._load_pdf(params, "pdf_extract_metrics")
        if error:
            return error
        assert result is not None

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
class ExtractTopicsTool(PdfToolBase):
    """Extract topics and keywords."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        top_n = params.get("top_n", 20)

        result, error = await self._load_pdf(params, "pdf_extract_topics")
        if error:
            return error
        assert result is not None

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
class ExtractCitationsTool(PdfToolBase):
    """Extract citations and references."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result, error = await self._load_pdf(params, "pdf_extract_citations")
        if error:
            return error
        assert result is not None

        # Look for references section
        all_text = " ".join(block.text for block in result.text_blocks)
        citations = extract_citations(all_text)

        return ToolResult(
            success=True,
            tool_name="pdf_extract_citations",
            data={
                "citation_count": len(citations),
                "citations": [{"raw": citation} for citation in citations[:30]],
            },
        )


@tool(
    "pdf_summarize_sections",
    "Generate summaries for all major sections of a PDF document. "
    "Useful for getting an overview of the document structure. "
    "params: path (string)",
)
class SummarizeSectionsTool(PdfToolBase):
    """Summarize document sections."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result, error = await self._load_pdf(params, "pdf_summarize_sections")
        if error:
            return error
        assert result is not None

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
class CompareEntitiesTool(PdfToolBase):
    """Compare entities mentioned in the document."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        entity = params.get("entity", "").strip()

        result, error = await self._load_pdf(params, "pdf_compare_entities")
        if error:
            return error
        assert result is not None

        # Extract comparative statements
        all_text = " ".join(block.text for block in result.text_blocks)

        # Find model/system names (capitalized words with numbers/hyphens)
        entity_pattern = r"\b[A-Z][a-zA-Z0-9-]+(?:-\d+[a-z]?|\s+\d+[a-z]?)?\b"
        entities = list(set(re.findall(entity_pattern, all_text)))
        entities = [e for e in entities if len(e) > 2 and e not in ["The", "This", "That"]]

        # Find comparison statements
        comparisons: list[dict[str, str]] = []
        comparison_patterns = [
            r"([A-Z][a-zA-Z0-9-]+)\s+(?:outperforms?|surpasses?|beats?|is better than)\s+([A-Z][a-zA-Z0-9-]+)",
            r"([A-Z][a-zA-Z0-9-]+)\s+(?:vs\.?|versus|compared to)\s+([A-Z][a-zA-Z0-9-]+)",
        ]

        for pattern in comparison_patterns:
            matches = re.finditer(pattern, all_text)
            comparisons.extend(
                {
                    "entity_a": match.group(1),
                    "entity_b": match.group(2),
                    "context": all_text[max(0, match.start() - 50) : match.end() + 80],
                }
                for match in matches
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
