"""Local PyMuPDF parser — pure-local text extraction, no cloud, no OCR.

Used only as the cascade's last-resort fallback when every cloud provider is
unavailable, so a downloaded PDF still yields usable text.
"""

from __future__ import annotations

import logging

from .._build import build_from_page_texts, write_outputs
from .._errors import ParserProviderError
from .._request import ParseRequest
from ..models import PDFParseResult

logger = logging.getLogger(__name__)


class LocalPyMuPDFParser:
    """Deterministic, free, local-only text extraction via PyMuPDF."""

    name = "local"

    async def parse(self, client, req: ParseRequest) -> PDFParseResult:
        import asyncio

        return await asyncio.to_thread(self._parse_sync, req)

    def _parse_sync(self, req: ParseRequest) -> PDFParseResult:
        import fitz  # type: ignore[import-untyped]

        page_texts: list[str] = []
        try:
            with fitz.open(str(req.file_path)) as doc:
                for page in doc:
                    raw = page.get_text("text")
                    page_texts.append(raw if isinstance(raw, str) else "")
        except Exception as exc:
            raise ParserProviderError(provider="local", retryable=False, cause=exc) from exc

        result = PDFParseResult(
            markdown_path=None,
            json_path=None,
            images_dir=str(req.images_dir),
            output_dir=str(req.output_dir),
            method="local",
            backend="pymupdf",
        )
        build_from_page_texts(result, page_texts)
        write_outputs(result, req.output_dir, "\n\n".join(t for t in page_texts if t.strip()))
        return result
