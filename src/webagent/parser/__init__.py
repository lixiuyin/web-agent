"""Cloud-native document parser cascade.

Replaces the previous local dots.mocr / Chandra VLM with a content-aware cascade
over HTTP OCR providers (Marker → MinerU → Paddle), with a local PyMuPDF
last-resort fallback.  Selection is driven by a fast local PyMuPDF profile.

Public entry points:
  - ``parse_pdf(path, output_dir, config=...)`` — synchronous, returns PDFParseResult
  - ``parse_structured_async(...)`` — async variant

Configure providers via ``AgentConfig`` (env: ``AGENT_OCR_PROVIDER``,
``AGENT_MINERU_API_KEY``, ``AGENT_MARKER_API_KEY``, ``AGENT_PADDLEOCR_*``, …).
"""

from webagent.parser._errors import (
    AllParsersFailedError,
    FailureReason,
    ParserProviderError,
)
from webagent.parser._profile import DocumentProfile, profile_document
from webagent.parser._router import select_parsers
from webagent.parser.cascade import parse_pdf, parse_structured_async
from webagent.parser.models import (
    ImageInfo,
    PDFParseResult,
    TableInfo,
    TextBlock,
    find_images_by_keyword,
    find_section_by_title,
    find_tables_by_keyword,
    generate_content_summary,
)

__all__ = [
    "AllParsersFailedError",
    "DocumentProfile",
    "FailureReason",
    "ImageInfo",
    "PDFParseResult",
    "ParserProviderError",
    "TableInfo",
    "TextBlock",
    "find_images_by_keyword",
    "find_section_by_title",
    "find_tables_by_keyword",
    "generate_content_summary",
    "parse_pdf",
    "parse_structured_async",
    "profile_document",
    "select_parsers",
]
